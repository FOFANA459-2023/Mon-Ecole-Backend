"""Notifications: who is told what, preferences, emails to staff and (when enabled) to parents."""

from datetime import date, timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.academics.models import ClassSubject, Subject
from apps.accounts.models import Role
from apps.administration.models import Announcement, CalendarEvent
from apps.enrollments import services as enrollment_services
from apps.notifications import events, services
from apps.notifications.models import Notification, NotificationPreference, OutboundEmail
from apps.people.models import Guardian, StudentGuardian

pytestmark = pytest.mark.django_db
URL = "/api/v1/notifications/"


@pytest.fixture
def setup(school, make_class, make_student, make_staff, make_member, client_for):
    """7ème A: Mr Barry teaches maths, Mrs Camara leads the class; one student; a director."""
    class_group = make_class()
    barry_user = make_member(school, "teacher", email="barry@test.local")
    camara_user = make_member(school, "teacher", email="camara@test.local")
    barry = make_staff("Mamadou", "Barry", user=barry_user)
    camara = make_staff("Fatou", "Camara", user=camara_user)
    class_group.class_teacher = camara
    class_group.save()
    maths = Subject.objects.create(school=school, name="Mathématiques", code="MATH")
    cs = ClassSubject.objects.create(school=school, class_group=class_group, subject=maths, teacher=barry)
    enrollment = enrollment_services.enroll(
        make_student("Awa", "Diallo"), class_group, enrollment_date=date(2026, 9, 2)
    )
    director_user = make_member(school, "director", email="director@test.local")
    return {
        "class": class_group,
        "cs": cs,
        "barry": barry_user,
        "camara": camara_user,
        "director": director_user,
        "enrollment": enrollment,
        "clients": {
            "barry": client_for(barry_user, school),
            "camara": client_for(camara_user, school),
            "director": client_for(director_user, school),
        },
    }


def titles(user):
    return list(Notification.objects.filter(user=user).values_list("title", flat=True))


def gradebook(setup, year):
    from apps.assessments.models import Gradebook
    from apps.assessments.services import ensure_gradebooks

    term = year.terms.order_by("order").first()
    ensure_gradebooks(setup["class"].school, term)
    return Gradebook.objects.get(class_subject=setup["cs"], term=term)


class TestMarks:
    def test_each_step_tells_the_right_people(self, setup, year, school, django_capture_on_commit_callbacks):
        book = gradebook(setup, year)
        barry, director = setup["clients"]["barry"], setup["clients"]["director"]
        category = barry.post("/api/v1/grade-categories/", {"gradebook": book.pk, "name": "Devoirs"}).data
        barry.post(
            "/api/v1/assessments/",
            {"gradebook": book.pk, "category": category["id"], "name": "D1", "max_score": "20"},
        )
        with django_capture_on_commit_callbacks(execute=True):
            assert barry.post(f"/api/v1/gradebooks/{book.pk}/submit/").status_code == 200
        assert titles(setup["director"]) == ["Notes à valider : Mathématiques — 7ème A"]
        assert titles(setup["barry"]) == []  # not told of his own step

        with django_capture_on_commit_callbacks(execute=True):
            director.post(f"/api/v1/gradebooks/{book.pk}/send-back/", {"reason": "Il manque D2."})
        notification = Notification.objects.get(user=setup["barry"])
        assert notification.title == "Notes renvoyées : Mathématiques — 7ème A"
        assert "Il manque D2." in notification.body and notification.link == f"/assessments/{book.pk}"
        # Sent-back marks need acting on: an email by default.
        email = OutboundEmail.objects.get(to="barry@test.local")
        assert email.status == "sent" and len(mail.outbox) == 1

        with django_capture_on_commit_callbacks(execute=True):
            barry.post(f"/api/v1/gradebooks/{book.pk}/submit/")
            director.post(f"/api/v1/gradebooks/{book.pk}/publish/")
        assert "Notes publiées : Mathématiques — 7ème A" in titles(setup["barry"])
        assert titles(setup["camara"]) == ["Notes publiées : Mathématiques — 7ème A"]

    def test_a_preference_turns_a_kind_off(self, setup, year, django_capture_on_commit_callbacks):
        NotificationPreference.objects.create(user=setup["director"], kind="grades_submitted", in_app=False)
        events.gradebook_changed(gradebook(setup, year), "submit", setup["barry"])
        assert titles(setup["director"]) == []


class TestEndpoints:
    def test_the_list_unread_count_and_reading(self, setup, school, other_school, make_member, client_for):
        barry = setup["clients"]["barry"]
        for n in range(3):
            Notification.objects.create(school=school, user=setup["barry"], kind="exam_soon", title=f"N{n}")
        mine_elsewhere = make_member(other_school, "teacher", email="barry2@test.local")
        Notification.objects.create(school=other_school, user=mine_elsewhere, kind="exam_soon", title="Other")
        assert barry.get(f"{URL}unread-count/").data == {"unread": 3}
        first = barry.get(URL).data["results"][0]
        assert barry.post(f"{URL}{first['id']}/read/").data["read_at"] is not None
        assert barry.get(URL, {"unread": "1"}).data["count"] == 2
        assert barry.post(f"{URL}read-all/").data == {"unread": 0}
        other = Notification.objects.get(title="Other")
        assert barry.post(f"{URL}{other.pk}/read/").status_code == 404
        assert setup["clients"]["camara"].post(f"{URL}{first['id']}/read/").status_code == 404

    def test_preferences(self, setup):
        barry = setup["clients"]["barry"]
        rows = {r["kind"]: r for r in barry.get(f"{URL}preferences/").data}
        assert rows["grades_sent_back"]["email"] is True and rows["exam_soon"]["email"] is False
        response = barry.patch(
            f"{URL}preferences/", [{"kind": "exam_soon", "in_app": True, "email": True}], format="json"
        )
        assert response.status_code == 200
        assert {r["kind"]: r["email"] for r in response.data}["exam_soon"] is True
        assert (
            barry.patch(
                f"{URL}preferences/", [{"kind": "nope", "in_app": 1, "email": 0}], format="json"
            ).status_code
            == 400
        )


class TestAnnouncements:
    def test_the_audience_is_told_once_when_it_is_published(
        self, setup, school, make_member, django_capture_on_commit_callbacks
    ):
        accountant = make_member(school, "accountant", email="compta@test.local")
        teachers = Role.objects.get(school=school, key="teacher")
        director = setup["clients"]["director"]
        with django_capture_on_commit_callbacks(execute=True):
            director.post(
                "/api/v1/announcements/",
                {"title": "Conseil pédagogique", "body": "Jeudi 15 h.", "roles": [teachers.pk]},
                format="json",
            )
        assert titles(setup["barry"]) == ["Conseil pédagogique"] and titles(accountant) == []
        assert titles(setup["director"]) == []  # the author

        later = timezone.now() + timedelta(hours=2)
        with django_capture_on_commit_callbacks(execute=True):
            director.post(
                "/api/v1/announcements/",
                {"title": "Rentrée", "body": "…", "priority": "important", "publish_at": later.isoformat()},
                format="json",
            )
        assert titles(accountant) == []
        Announcement.objects.filter(title="Rentrée").update(publish_at=timezone.now())
        with django_capture_on_commit_callbacks(execute=True):
            assert events.dispatch_due_announcements() == 3  # Barry, Camara, the accountant
            assert events.dispatch_due_announcements() == 0
        assert titles(accountant) == ["Rentrée"]
        # Important: by email too.
        assert OutboundEmail.objects.filter(kind="announcement_important").count() == 3


class TestDailyRounds:
    def test_new_absence_alerts_are_told_to_the_class_teacher_and_the_school(self, setup, school):
        from apps.attendance.models import AbsenceAlert

        alert = AbsenceAlert.objects.create(
            school=school,
            enrollment=setup["enrollment"],
            raised_on=date(2026, 10, 15),
            absences=3,
            since=date(2026, 10, 1),
            last_absence=date(2026, 10, 14),
        )
        events.absence_alerts_raised(school, [alert], date(2026, 10, 15))
        assert titles(setup["camara"]) == ["1 élève(s) trop souvent absent(s)"]
        assert titles(setup["director"]) == ["1 élève(s) trop souvent absent(s)"]
        assert titles(setup["barry"]) == []
        events.absence_alerts_raised(school, [alert], date(2026, 10, 15))
        assert Notification.objects.filter(kind="absence_alert").count() == 2

    def test_teachers_hear_of_an_exam_a_week_before(self, setup, school, level):
        event = CalendarEvent.objects.create(
            school=school,
            title="Compositions",
            kind="exam",
            start_date=date(2026, 12, 14),
            end_date=date(2026, 12, 18),
        )
        event.levels.add(level)
        assert events.exam_reminders(school, date(2026, 12, 1)) == 0
        assert events.exam_reminders(school, date(2026, 12, 9)) == 1
        assert events.exam_reminders(school, date(2026, 12, 10)) == 0
        assert titles(setup["barry"]) == ["Examen le 14/12/2026 : Compositions"]
        assert len(titles(setup["camara"])) == 1 and titles(setup["director"]) == []


@pytest.fixture
def invoiced(school, fees, make_class, make_student):
    """Binta: the year's fees (tuition due 01/10/2026, 10/01/2027, 01/04/2027); her mother has an email."""
    student = make_student("Binta", "Bah")
    enrollment_services.enroll(student, make_class("7ème B"), enrollment_date=date(2026, 9, 2))
    mother = Guardian.objects.create(
        school=school, first_name="Mariam", last_name="Bah", email="mariam@test.local"
    )
    StudentGuardian.objects.create(
        school=school, student=student, guardian=mother, relationship="mother", is_primary=True
    )
    return student


class TestPayments:
    def test_staff_hear_of_newly_overdue_invoices(self, school, invoiced, make_member):
        accountant = make_member(school, "accountant", email="compta@test.local")
        outcome = events.payment_reminders(school, date(2026, 10, 2))
        assert outcome["overdue"] == 1 and outcome["parent_overdue"] == 0
        assert titles(accountant) == ["1 facture(s) passée(s) en retard"]
        assert not OutboundEmail.objects.filter(audience="parent").exists()  # the school has not turned it on
        events.payment_reminders(school, date(2026, 10, 2))
        assert Notification.objects.filter(kind="payments_overdue").count() == 1

    def test_parents_get_reminders_when_the_school_wants_them(
        self, school, invoiced, django_capture_on_commit_callbacks
    ):
        school.settings.parent_email_payments = True
        school.settings.save()
        with django_capture_on_commit_callbacks(execute=True):
            outcome = events.payment_reminders(school, date(2027, 1, 8))
        assert outcome["parent_due"] == 1
        email = OutboundEmail.objects.get(audience="parent")
        assert email.to == "mariam@test.local" and "10/01/2027" in email.subject
        assert "Binta Bah" in email.message and email.status == "sent"
        # Not twice for the same installment.
        events.payment_reminders(school, date(2027, 1, 9))
        assert OutboundEmail.objects.filter(audience="parent").count() == 1
        events.payment_reminders(school, date(2027, 1, 11))
        assert OutboundEmail.objects.filter(kind="parent_payment_overdue").count() == 1


class TestParentAbsences:
    def test_an_email_the_evening_of_an_absence(self, setup, school):
        from apps.attendance.models import AttendanceRecord, ClassRegister

        mother = Guardian.objects.create(
            school=school, first_name="A", last_name="Diallo", email="mere@test.local"
        )
        StudentGuardian.objects.create(
            school=school,
            student=setup["enrollment"].student,
            guardian=mother,
            relationship="mother",
            is_primary=True,
        )
        register = ClassRegister.objects.create(
            school=school, class_group=setup["class"], date=date(2026, 10, 15)
        )
        AttendanceRecord.objects.create(
            school=school, register=register, enrollment=setup["enrollment"], status="absent"
        )
        assert events.parent_absence_emails(school, date(2026, 10, 15)) == 0
        school.settings.parent_email_absences = True
        school.settings.save()
        assert events.parent_absence_emails(school, date(2026, 10, 15)) == 1
        assert events.parent_absence_emails(school, date(2026, 10, 15)) == 0
        assert OutboundEmail.objects.get().subject == "École Alpha — absence de Awa Diallo"


class TestSending:
    def test_the_monthly_limit_stops_emails(self, school, settings, django_capture_on_commit_callbacks):
        settings.EMAILJS_MONTHLY_LIMIT = 1
        with django_capture_on_commit_callbacks(execute=True):
            services.queue_email(school, to="a@test.local", kind="x", subject="1", message="m")
            services.queue_email(school, to="b@test.local", kind="x", subject="2", message="m")
        assert list(OutboundEmail.objects.order_by("id").values_list("status", flat=True)) == [
            "sent",
            "skipped",
        ]
        assert len(mail.outbox) == 1

    def test_a_provider_error_is_recorded(self, school, monkeypatch, django_capture_on_commit_callbacks):
        def fail(**kwargs):
            raise RuntimeError("EmailJS said no")

        monkeypatch.setattr("apps.core.email.send_email", fail)
        with django_capture_on_commit_callbacks(execute=True):
            services.queue_email(school, to="a@test.local", kind="x", subject="1", message="m")
        email = OutboundEmail.objects.get()
        assert email.status == "failed" and "EmailJS said no" in email.error
