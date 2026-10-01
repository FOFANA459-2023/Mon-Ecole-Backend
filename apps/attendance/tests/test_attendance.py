from datetime import date

import pytest

from apps.academics.models import ClassSubject, Subject
from apps.attendance import services
from apps.attendance.models import AttendanceRecord, ClassRegister, StaffAttendance
from apps.audit.models import AuditLog
from apps.enrollments import services as enrollment_services

pytestmark = pytest.mark.django_db

TODAY = date(2026, 10, 15)
YESTERDAY = date(2026, 10, 14)
REGISTER = "/api/v1/attendance/register/"


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(services, "school_today", lambda school: TODAY)


@pytest.fixture
def setup(school, make_class, make_student, make_staff, make_member):
    """7ème A with three students, taught (maths) by Mr Barry; 7ème B taught by nobody here."""
    class_a, class_b = make_class(), make_class("7ème B")
    barry_user = make_member(school, "teacher", email="barry@test.local")
    barry = make_staff("Mamadou", "Barry", user=barry_user)
    maths = Subject.objects.create(school=school, name="Mathématiques", code="MATH")
    ClassSubject.objects.create(school=school, class_group=class_a, subject=maths, teacher=barry)
    enrolled = {
        first: enrollment_services.enroll(
            make_student(first, last), class_a, enrollment_date=date(2026, 9, 2)
        )
        for first, last in [("Awa", "Diallo"), ("Binta", "Bah"), ("Moussa", "Condé")]
    }
    return {"a": class_a, "b": class_b, "barry": barry_user, "e": enrolled, "barry_staff": barry}


def take(client, class_group, day, records):
    return client.post(
        REGISTER, {"class_group": class_group.pk, "date": day.isoformat(), "records": records}, format="json"
    )


def marked(enrolled, **changes):
    """A whole register: every student present, except the ones named (first name → status or entry)."""
    rows = []
    for first, enrollment in enrolled.items():
        change = changes.get(first, "present")
        entry = change if isinstance(change, dict) else {"status": change}
        rows.append({"enrollment": enrollment.pk, **entry})
    return rows


class TestTakingTheRegister:
    def test_nobody_is_marked_in_advance_and_the_teacher_marks_everyone(self, setup, school, client_for):
        client = client_for(setup["barry"], school)
        sheet = client.get(REGISTER, {"class_group": setup["a"].pk}).data
        assert sheet["register"] is None and sheet["can_edit"] is True
        assert sheet["date"] == TODAY.isoformat()
        assert {s["status"] for s in sheet["students"]} == {None}

        e = setup["e"]
        response = take(
            client,
            setup["a"],
            TODAY,
            marked(e, Binta="absent", Moussa={"status": "late", "minutes_late": 15, "note": "Bus"}),
        )
        assert response.status_code == 200, response.data
        statuses = {s["student_name"]: (s["status"], s["minutes_late"]) for s in response.data["students"]}
        assert statuses == {
            "Awa Diallo": ("present", None),
            "Binta Bah": ("absent", None),
            "Moussa Condé": ("late", 15),
        }
        assert response.data["taken_by_name"]
        entry = AuditLog.objects.get(module="attendance", action="create")
        assert "1 absent, 1 late" in entry.summary

    def test_a_register_cannot_be_saved_until_every_student_is_marked(self, setup, school, client_for):
        client = client_for(setup["barry"], school)
        e = setup["e"]
        response = take(client, setup["a"], TODAY, [{"enrollment": e["Binta"].pk, "status": "absent"}])
        assert response.status_code == 400
        assert "2 still have no attendance" in str(response.data)
        assert not ClassRegister.objects.exists()

    def test_minutes_only_count_for_late_students(self, setup, school, client_for):
        e = setup["e"]
        take(
            client_for(setup["barry"], school),
            setup["a"],
            TODAY,
            marked(e, Awa={"status": "absent", "minutes_late": 20}),
        )
        assert AttendanceRecord.objects.get(enrollment=e["Awa"]).minutes_late is None

    def test_corrections_on_the_day_are_audited(self, setup, school, client_for):
        client = client_for(setup["barry"], school)
        e = setup["e"]
        take(client, setup["a"], TODAY, marked(e, Binta="absent"))
        response = take(
            client, setup["a"], TODAY, [{"enrollment": e["Binta"].pk, "status": "excused", "note": "Ill"}]
        )
        assert response.status_code == 200
        assert ClassRegister.objects.count() == 1
        change = AuditLog.objects.get(module="attendance", action="update")
        assert change.old_values == {str(e["Binta"].pk): "absent"}
        assert change.new_values == {str(e["Binta"].pk): "excused — Ill"}
        # Awa and Moussa were not sent again: they stay present.
        assert AttendanceRecord.objects.filter(status="present").count() == 2

    def test_no_register_for_tomorrow_or_outside_the_school_year(self, setup, school, client_for):
        client = client_for(setup["barry"], school)
        assert take(client, setup["a"], date(2026, 10, 16), []).status_code == 400
        assert take(client, setup["a"], date(2026, 8, 20), []).status_code == 400

    def test_a_student_of_another_class_is_refused(self, setup, school, client_for, make_student):
        other = enrollment_services.enroll(make_student("Kadi", "Touré"), setup["b"])
        response = take(
            client_for(setup["barry"], school),
            setup["a"],
            TODAY,
            [{"enrollment": other.pk, "status": "absent"}],
        )
        assert response.status_code == 400

    def test_students_who_left_are_on_registers_before_they_left_only(self, setup, school, client_for):
        enrollment_services.withdraw(setup["e"]["Moussa"], on=date(2026, 10, 10), reason="Moved")
        client = client_for(setup["barry"], school)
        names = lambda day: {  # noqa: E731
            s["student_name"]
            for s in client.get(REGISTER, {"class_group": setup["a"].pk, "date": day}).data["students"]
        }
        assert "Moussa Condé" in names("2026-10-09")
        assert "Moussa Condé" not in names(TODAY.isoformat())


class TestWhoTakesWhich:
    def test_a_teacher_only_reaches_their_own_classes(self, setup, school, client_for):
        client = client_for(setup["barry"], school)
        assert client.get(REGISTER, {"class_group": setup["b"].pk}).status_code == 404
        assert take(client, setup["b"], TODAY, []).status_code == 404

    def test_earlier_days_need_the_correction_right(self, setup, school, make_member, client_for):
        e = setup["e"]
        teacher = client_for(setup["barry"], school)
        response = take(teacher, setup["a"], YESTERDAY, marked(e, Awa="absent"))
        assert response.status_code == 403
        assert (
            teacher.get(REGISTER, {"class_group": setup["a"].pk, "date": YESTERDAY}).data["can_edit"] is False
        )
        director = client_for(make_member(school, "director"), school)
        response = take(director, setup["a"], YESTERDAY, marked(e, Awa="absent"))
        assert response.status_code == 200

    def test_admin_staff_see_registers_but_do_not_take_them(self, setup, school, make_member, client_for):
        client = client_for(make_member(school, "admin_staff"), school)
        assert client.get(REGISTER, {"class_group": setup["a"].pk}).status_code == 200
        assert take(client, setup["a"], TODAY, []).status_code == 403

    def test_other_schools_cannot_reach_the_class(self, setup, other_school, make_member, client_for):
        client = client_for(make_member(other_school, "director"), other_school)
        assert client.get(REGISTER, {"class_group": setup["a"].pk}).status_code == 400


class TestOverviewAndReports:
    def test_the_day_lists_my_classes_and_whether_the_register_is_taken(self, setup, school, client_for):
        client = client_for(setup["barry"], school)
        rows = client.get("/api/v1/attendance/day/").data
        assert [(r["class_name"], r["register"], r["student_count"]) for r in rows] == [("7ème A", None, 3)]
        take(client, setup["a"], TODAY, marked(setup["e"], Awa="absent"))
        row = client.get("/api/v1/attendance/day/").data[0]
        assert (row["present"], row["absent"], row["can_take"]) == (2, 1, True)

    def test_month_grid_absences_and_student_summary(self, setup, school, make_member, client_for, year):
        director = client_for(make_member(school, "director"), school)
        e = setup["e"]
        for day, status in [(date(2026, 10, 13), "absent"), (YESTERDAY, "absent"), (TODAY, "late")]:
            take(director, setup["a"], day, marked(e, Binta={"status": status, "minutes_late": 5}))

        month = director.get(
            "/api/v1/attendance/class-month/", {"class_group": setup["a"].pk, "month": "2026-10"}
        ).data
        assert month["dates"] == ["2026-10-13", "2026-10-14", "2026-10-15"]
        binta = next(r for r in month["students"] if r["student_name"] == "Binta Bah")
        assert (binta["absent"], binta["late"], binta["present"]) == (2, 1, 0)
        assert month["totals"]["present"] == 6

        rows = director.get("/api/v1/attendance/absences/", {"min_absences": 2}).data
        assert [(r["student_name"], r["absent"], r["late"]) for r in rows] == [("Binta Bah", 2, 1)]

        summary = director.get(f"/api/v1/attendance/students/{e['Binta'].student_id}/").data
        assert (summary["days"], summary["absent"], summary["late"]) == (3, 2, 1)
        assert [ev["status"] for ev in summary["events"]] == ["late", "absent", "absent"]

    def test_a_teacher_cannot_read_students_of_other_classes(self, setup, school, client_for, make_student):
        other = enrollment_services.enroll(make_student("Kadi", "Touré"), setup["b"])
        client = client_for(setup["barry"], school)
        assert client.get(f"/api/v1/attendance/students/{other.student_id}/").status_code == 404


class TestStaffAttendance:
    URL = "/api/v1/attendance/staff/"

    def test_admin_staff_record_who_came_to_work(self, setup, school, make_member, make_staff, client_for):
        cook = make_staff("Aïssatou", "Sylla", staff_type="support")
        client = client_for(make_member(school, "admin_staff"), school)
        sheet = client.get(self.URL).data
        assert {r["full_name"] for r in sheet["staff"]} == {"Mamadou Barry", "Aïssatou Sylla"}
        assert not any(r["recorded"] for r in sheet["staff"])
        response = client.post(
            self.URL,
            {
                "date": TODAY.isoformat(),
                "entries": [
                    {"staff": setup["barry_staff"].pk, "status": "late", "minutes_late": 30},
                    {"staff": cook.pk, "status": "leave", "note": "Maternity leave"},
                ],
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        assert StaffAttendance.objects.get(staff=cook).status == "leave"
        assert AuditLog.objects.filter(module="attendance", entity_id=TODAY.isoformat()).exists()
        assert client.post(self.URL, {"date": "2026-10-16", "entries": []}, format="json").status_code == 400

    def test_teachers_cannot_see_staff_attendance(self, setup, school, client_for):
        assert client_for(setup["barry"], school).get(self.URL).status_code == 403


def test_the_dashboard_counts_today_s_registers(setup, school, client_for, monkeypatch):
    from apps.attendance import services as attendance_services

    monkeypatch.setattr(attendance_services, "school_today", lambda school: TODAY)
    client = client_for(setup["barry"], school)
    take(client, setup["a"], TODAY, marked(setup["e"], Awa="absent", Binta="late"))
    today = client.get("/api/v1/dashboard/summary/").data["attendance_today"]
    assert today == {
        "date": TODAY.isoformat(),
        "registers_taken": 1,
        "classes": 1,
        "present": 1,
        "absent": 1,
        "late": 1,
        "excused": 0,
    }
