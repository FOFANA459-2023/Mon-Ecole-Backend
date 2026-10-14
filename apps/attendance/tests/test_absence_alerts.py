"""High-absenteeism alerts: raised in the evening from the registers, followed up by the school."""

from datetime import date, timedelta

import pytest

from apps.academics.models import ClassSubject, Subject
from apps.attendance import alerts, tasks
from apps.attendance.models import AbsenceAlert, AttendanceRecord, ClassRegister
from apps.audit.models import AuditLog
from apps.enrollments import services as enrollment_services

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 15)
ALERTS = "/api/v1/attendance/alerts/"


@pytest.fixture
def setup(school, make_class, make_student, make_staff, make_member):
    """7ème A (Awa, Binta) taught by Mr Barry; 7ème B (Moussa) taught by nobody here."""
    class_a, class_b = make_class(), make_class("7ème B")
    barry_user = make_member(school, "teacher", email="barry@test.local")
    barry = make_staff("Mamadou", "Barry", user=barry_user)
    maths = Subject.objects.create(school=school, name="Mathématiques", code="MATH")
    ClassSubject.objects.create(school=school, class_group=class_a, subject=maths, teacher=barry)
    e = {
        "Awa": enrollment_services.enroll(
            make_student("Awa", "Diallo"), class_a, enrollment_date=date(2026, 9, 2)
        ),
        "Binta": enrollment_services.enroll(
            make_student("Binta", "Bah"), class_a, enrollment_date=date(2026, 9, 2)
        ),
        "Moussa": enrollment_services.enroll(
            make_student("Moussa", "Condé"), class_b, enrollment_date=date(2026, 9, 2)
        ),
    }
    return {"a": class_a, "b": class_b, "barry": barry_user, "e": e}


def mark(school, enrollment, day: date, status: str = "absent"):
    register, _ = ClassRegister.objects.get_or_create(
        school=school, class_group=enrollment.class_group, date=day
    )
    AttendanceRecord.objects.update_or_create(
        register=register, enrollment=enrollment, defaults={"school": school, "status": status}
    )


def days_ago(*numbers: int) -> list[date]:
    return [TODAY - timedelta(days=n) for n in numbers]


class TestCheck:
    def test_three_unexcused_absences_in_30_days_raise_an_alert(self, setup, school):
        e = setup["e"]
        for day in days_ago(1, 5, 20):
            mark(school, e["Awa"], day)
        for day in days_ago(1, 2, 3):
            mark(school, e["Binta"], day, "excused")  # excused absences do not count
        for day in days_ago(2, 40, 50):
            mark(school, e["Moussa"], day)  # two of them are older than 30 days
        assert alerts.check_school(school, TODAY) == {"raised": 1, "updated": 0, "cleared": 0}
        alert = AbsenceAlert.objects.get()
        assert alert.enrollment == e["Awa"] and alert.absences == 3
        assert (alert.since, alert.last_absence, alert.raised_on) == (
            TODAY - timedelta(days=20),
            TODAY - timedelta(days=1),
            TODAY,
        )

        # The next evening: one more absence updates the open alert, it does not raise another one.
        mark(school, e["Awa"], TODAY)
        assert alerts.check_school(school, TODAY) == {"raised": 0, "updated": 1, "cleared": 0}
        assert AbsenceAlert.objects.get().absences == 4

    def test_an_alert_is_cleared_when_corrected_registers_no_longer_reach_the_number(self, setup, school):
        e = setup["e"]
        for day in days_ago(1, 2, 3):
            mark(school, e["Awa"], day)
        alerts.check_school(school, TODAY)
        mark(school, e["Awa"], TODAY - timedelta(days=2), "excused")
        assert alerts.check_school(school, TODAY)["cleared"] == 1
        assert AbsenceAlert.objects.get().status == AbsenceAlert.AlertStatus.CLEARED

    def test_followed_up_absences_are_not_counted_again(self, setup, school, make_member, client_for):
        e = setup["e"]
        for day in days_ago(1, 2, 3):
            mark(school, e["Awa"], day)
        alerts.check_school(school, TODAY)
        director = client_for(make_member(school, "director"), school)
        alert = AbsenceAlert.objects.get()
        response = director.post(
            f"{ALERTS}{alert.pk}/follow-up/", {"note": "Appel au père, Awa était malade."}
        )
        assert response.status_code == 200 and response.data["status"] == "followed_up"
        assert response.data["closed_by_name"] and response.data["note"].startswith("Appel")
        assert AuditLog.objects.filter(module="attendance", summary__startswith="Absence alert").exists()

        assert alerts.check_school(school, TODAY)["raised"] == 0
        # Three new absences after the follow-up raise a new alert.
        later = TODAY + timedelta(days=7)
        for day in [TODAY + timedelta(days=n) for n in (1, 2, 3)]:
            mark(school, e["Awa"], day)
        assert alerts.check_school(school, later)["raised"] == 1
        assert AbsenceAlert.objects.filter(status="open").get().absences == 3

    def test_the_school_chooses_the_rule_or_turns_it_off(self, setup, school):
        e = setup["e"]
        for day in days_ago(1, 2):
            mark(school, e["Awa"], day)
        school.settings.absence_alert_threshold = 2
        school.settings.absence_alert_days = 7
        school.settings.save()
        assert alerts.check_school(school, TODAY)["raised"] == 1
        school.settings.absence_alert_threshold = 0
        school.settings.save()
        assert alerts.check_school(school, TODAY)["cleared"] == 1

    def test_the_nightly_task_checks_every_active_school(self, setup, school, other_school):
        for day in days_ago(1, 2, 3):
            mark(school, setup["e"]["Awa"], day)
        assert tasks.check_absenteeism() == 1


class TestEndpoints:
    def test_teachers_see_the_alerts_of_their_classes(self, setup, school, client_for, make_member):
        e = setup["e"]
        for enrollment in (e["Awa"], e["Moussa"]):
            for day in days_ago(1, 2, 3):
                mark(school, enrollment, day)
        alerts.check_school(school, TODAY)
        barry = client_for(setup["barry"], school)
        rows = barry.get(ALERTS).data["results"]
        assert [(r["student_name"], r["class_name"], r["absences"]) for r in rows] == [
            ("Awa Diallo", "7ème A", 3)
        ]
        director = client_for(make_member(school, "director"), school)
        assert director.get(ALERTS).data["count"] == 2
        assert director.get(ALERTS, {"class_group": setup["b"].pk}).data["count"] == 1
        assert director.get(ALERTS, {"status": "followed_up"}).data["count"] == 0
        moussa = AbsenceAlert.objects.get(enrollment=e["Moussa"])
        assert barry.post(f"{ALERTS}{moussa.pk}/follow-up/", {"note": "x"}).status_code == 404

    def test_a_closed_alert_cannot_be_followed_up_again(self, setup, school, client_for, make_member):
        for day in days_ago(1, 2, 3):
            mark(school, setup["e"]["Awa"], day)
        alerts.check_school(school, TODAY)
        director = client_for(make_member(school, "director"), school)
        alert = AbsenceAlert.objects.get()
        assert director.post(f"{ALERTS}{alert.pk}/follow-up/", {"note": ""}).status_code == 400
        assert director.post(f"{ALERTS}{alert.pk}/follow-up/", {"note": "Vu."}).status_code == 200
        assert director.post(f"{ALERTS}{alert.pk}/follow-up/", {"note": "Encore"}).status_code == 400

    def test_the_dashboard_counts_the_open_alerts(self, setup, school, client_for, make_member):
        for day in days_ago(1, 2, 3):
            mark(school, setup["e"]["Awa"], day)
        alerts.check_school(school, TODAY)
        director = client_for(make_member(school, "director"), school)
        assert director.get("/api/v1/dashboard/summary/").data["absence_alerts"] == 1
        accountant = client_for(make_member(school, "accountant"), school)
        assert accountant.get("/api/v1/dashboard/summary/").data["absence_alerts"] is None

    def test_the_rule_is_a_school_setting(self, school, client_for, make_member):
        director = client_for(make_member(school, "director"), school)
        response = director.patch(
            "/api/v1/school/",
            {"settings": {"absence_alert_threshold": 5, "absence_alert_days": 14}},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["settings"]["absence_alert_threshold"] == 5
        bad = director.patch("/api/v1/school/", {"settings": {"absence_alert_days": 2}}, format="json")
        assert bad.status_code == 400
