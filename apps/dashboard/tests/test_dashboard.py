"""The dashboard's finance, attendance, results and alert blocks, its filters and who sees what."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.administration.models import Announcement, CalendarEvent
from apps.attendance.models import AttendanceRecord, ClassRegister
from apps.enrollments import services as enrollment_services

pytestmark = pytest.mark.django_db
URL = "/api/v1/dashboard/summary/"
TODAY = date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr("apps.dashboard.selectors.today_for", lambda school: TODAY)


@pytest.fixture
def school_data(school, year, fees, make_class, make_student, make_member, client_for, cash_session):
    """Two classes of 7ème; Awa (7ème A) has paid her registration and first installment, Binta (7ème B)
    nothing yet."""
    from apps.finance import services as finance

    class_a, class_b = make_class("7ème A"), make_class("7ème B")
    awa = make_student("Awa", "Diallo")
    binta = make_student("Binta", "Bah", gender="F")
    enrollment_services.enroll(awa, class_a, enrollment_date=date(2026, 9, 2))
    enrollment_services.enroll(binta, class_b, enrollment_date=date(2026, 9, 2))
    director = make_member(school, "director")
    finance.record_payment(
        awa, amount=D("1250000"), method="cash", payment_date=date(2026, 9, 10), cash_session=cash_session
    )
    return {"a": class_a, "b": class_b, "director": client_for(director, school), "school": school}


class TestFinance:
    def test_expected_collected_outstanding_and_by_month(self, school_data):
        finance = school_data["director"].get(URL).data["finance"]
        # Two students × (3 000 000 tuition + 250 000 registration).
        assert D(finance["expected"]) == D("6500000")
        assert D(finance["collected"]) == D("1250000")
        assert D(finance["outstanding"]) == D("5250000")
        # Binta's first installment (01/10) and registration (01/09) are overdue.
        assert D(finance["overdue"]) == D("1250000")
        assert finance["students_with_balance"] == 2 and finance["students_overdue"] == 1
        assert [m["month"] for m in finance["by_month"]] == ["2026-09", "2026-10"]
        assert D(finance["by_month"][0]["amount"]) == D("1250000")
        assert finance["outstanding_by_class"][0]["class_name"] == "7ème B"

    def test_filters_narrow_the_figures(self, school_data):
        director = school_data["director"]
        by_class = director.get(URL, {"class_group": school_data["a"].pk}).data
        assert by_class["students"] == 1 and D(by_class["finance"]["expected"]) == D("3250000")
        overdue = director.get(URL, {"payment_status": "overdue"}).data["finance"]
        assert D(overdue["expected"]) == D("3250000") and overdue["students_with_balance"] == 1
        assert director.get(URL, {"payment_status": "nonsense"}).status_code == 400

    def test_teachers_see_no_money(self, school_data, school, make_member, client_for):
        teacher = client_for(make_member(school, "teacher"), school)
        data = teacher.get(URL).data
        assert data["finance"] is None and data["scope"] == "my_classes" and data["students"] == 0


class TestAttendanceAndResults:
    def test_attendance_rate_over_the_term(self, school_data, school):
        class_a = school_data["a"]
        enrollment = class_a.enrollments.get()
        for day, status in [
            (date(2026, 9, 14), "present"),
            (date(2026, 9, 15), "absent"),
            (date(2026, 9, 16), "late"),
        ]:
            register = ClassRegister.objects.create(school=school, class_group=class_a, date=day)
            AttendanceRecord.objects.create(
                school=school, register=register, enrollment=enrollment, status=status
            )
        attendance = school_data["director"].get(URL).data["attendance"]
        assert len(attendance["days"]) == 3 and str(attendance["days"][1]["rate"]) == "0.0"
        assert str(attendance["rate"]) == "66.7" and attendance["absent"] == 1

    def test_results_and_missing_grades_follow_the_term(self, school_data, year):
        data = school_data["director"].get(URL).data
        term = year.terms.order_by("order").first()
        assert data["filters"]["term"] == term.pk
        assert data["performance"]["counted"] == 0 and data["performance"]["by_class"] == []
        assert data["missing_grades"]["open"] == 0  # no subject yet, so no gradebook


class TestAlerts:
    def test_upcoming_exams_and_announcements(self, school_data, school, level):
        exam = CalendarEvent.objects.create(
            school=school,
            title="Compositions",
            kind="exam",
            start_date=TODAY + timedelta(days=10),
            end_date=TODAY + timedelta(days=12),
        )
        exam.levels.add(level)
        CalendarEvent.objects.create(
            school=school,
            title="Too far",
            kind="exam",
            start_date=TODAY + timedelta(days=60),
            end_date=TODAY + timedelta(days=60),
        )
        Announcement.objects.create(school=school, title="Rentrée", body="…", publish_at=timezone.now())
        data = school_data["director"].get(URL).data
        assert [e["title"] for e in data["upcoming_exams"]] == ["Compositions"]
        assert [a["title"] for a in data["announcements"]] == ["Rentrée"]
        assert data["finance"]["upcoming_deadlines"]["days"] == 14
