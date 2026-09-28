from datetime import date

import pytest

from apps.academics.models import AcademicYear
from apps.academics.services import create_academic_year
from apps.enrollments.models import Enrollment
from apps.enrollments.services import enroll
from apps.people.models import Student


def register_payload(class_group, **extra):
    return {
        "student": {"first_name": "Awa", "last_name": "Diallo", "gender": "F", "date_of_birth": "2014-05-31"},
        "guardians": [
            {
                "first_name": "Mariama",
                "last_name": "Bah",
                "phone": "+224 620 00 00 01",
                "relationship": "mother",
            },
            {
                "first_name": "Alpha",
                "last_name": "Diallo",
                "phone": "+224 620 00 00 02",
                "relationship": "father",
            },
        ],
        "class_group": class_group.pk,
        "enrollment_date": "2026-09-02",
        "previous_school": "École Kipé",
        **extra,
    }


@pytest.mark.django_db
class TestRegistration:
    def test_registers_student_guardians_and_class_in_one_step(
        self, school, make_class, make_member, client_for
    ):
        class_group = make_class()
        client = client_for(make_member(school, "admin_staff"), school)
        response = client.post("/api/v1/enrollments/register/", register_payload(class_group), format="json")
        assert response.status_code == 201, response.data
        assert response.data["class_name"] == "7ème A"
        assert response.data["status"] == "active"
        student = Student.objects.get(pk=response.data["student"]["id"])
        links = list(student.guardian_links.all())
        assert len(links) == 2
        assert [link.is_primary for link in links].count(True) == 1

    def test_failed_registration_saves_nothing(self, school, make_class, make_member, client_for):
        class_group = make_class(capacity=0)
        client = client_for(make_member(school, "admin_staff"), school)
        response = client.post("/api/v1/enrollments/register/", register_payload(class_group), format="json")
        assert response.status_code == 400
        assert not Student.objects.exists()

    def test_new_student_requires_students_create(self, school, make_class, make_member, client_for):
        from apps.accounts.models import Role

        custom = Role.objects.create(school=school, name="Enrol only", permissions=["enrollments.create"])
        user = make_member(school, "teacher")
        user.memberships.get().roles.set([custom])
        response = client_for(user, school).post(
            "/api/v1/enrollments/register/", register_payload(make_class()), format="json"
        )
        assert response.status_code == 403

    def test_either_new_or_existing_student(self, school, make_class, make_student, make_member, client_for):
        payload = register_payload(make_class(), student_id=make_student().pk)
        response = client_for(make_member(school, "admin_staff"), school).post(
            "/api/v1/enrollments/register/", payload, format="json"
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestEnrolmentRules:
    def test_one_active_enrolment_per_year(self, school, make_class, make_student, make_member, client_for):
        student = make_student()
        enroll(student, make_class("7ème A"))
        response = client_for(make_member(school, "admin_staff"), school).post(
            "/api/v1/enrollments/",
            {"student": student.pk, "class_group": make_class("7ème B").pk},
            format="json",
        )
        assert response.status_code == 400

    def test_class_capacity_is_enforced(self, school, make_class, make_student):
        from rest_framework.exceptions import ValidationError

        class_group = make_class(capacity=1)
        enroll(make_student("A", "One"), class_group)
        with pytest.raises(ValidationError):
            enroll(make_student("B", "Two"), class_group)

    def test_closed_year_refuses_enrolments(self, school, make_class, make_student, year):
        from rest_framework.exceptions import ValidationError

        class_group = make_class()
        AcademicYear.objects.filter(pk=year.pk).update(status="closed")
        class_group.refresh_from_db()
        with pytest.raises(ValidationError):
            enroll(make_student(), class_group)


@pytest.mark.django_db
class TestMovesAndDepartures:
    def test_change_class_keeps_history(self, school, make_class, make_student, make_member, client_for):
        student = make_student()
        enrolment = enroll(student, make_class("7ème A"))
        target = make_class("7ème B")
        response = client_for(make_member(school, "admin_staff"), school).post(
            f"/api/v1/enrollments/{enrolment.pk}/change-class/",
            {"class_group": target.pk, "reason": "Effectifs"},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["class_name"] == "7ème B"
        enrolment.refresh_from_db()
        assert enrolment.status == Enrollment.Status.CLASS_CHANGED
        assert student.enrollments.count() == 2

    def test_withdraw_to_another_school(self, school, make_class, make_student, make_member, client_for):
        enrolment = enroll(make_student(), make_class())
        response = client_for(make_member(school, "admin_staff"), school).post(
            f"/api/v1/enrollments/{enrolment.pk}/withdraw/",
            {"reason": "Déménagement", "transfer_to": "Lycée de Kindia"},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["status"] == "withdrawn"
        assert response.data["transfer_to"] == "Lycée de Kindia"

    def test_cancel_requires_permission(self, school, make_class, make_student, make_member, client_for):
        enrolment = enroll(make_student(), make_class())
        accountant = make_member(school, "accountant")
        assert (
            client_for(accountant, school)
            .post(f"/api/v1/enrollments/{enrolment.pk}/cancel/", {"reason": "x"}, format="json")
            .status_code
            == 403
        )
        director = make_member(school, "director")
        assert (
            client_for(director, school)
            .post(
                f"/api/v1/enrollments/{enrolment.pk}/cancel/", {"reason": "Erreur de saisie"}, format="json"
            )
            .data["status"]
            == "cancelled"
        )

    def test_enrolment_form_is_a_pdf(self, school, make_class, make_student, make_member, client_for):
        enrolment = enroll(make_student(), make_class())
        response = client_for(make_member(school, "admin_staff"), school).get(
            f"/api/v1/enrollments/{enrolment.pk}/form/"
        )
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")


@pytest.mark.django_db
class TestPromotion:
    def test_promotes_the_class_and_skips_students_already_enrolled(
        self, school, year, level, make_class, make_student, make_member, client_for
    ):
        next_year = create_academic_year(
            school, name="2027-2028", start_date=date(2027, 9, 1), end_date=date(2028, 6, 30)
        )
        from_class = make_class("7ème A")
        to_class = make_class("8ème A", academic_year=next_year)
        promoted = make_student("Awa", "Diallo")
        enroll(promoted, from_class)
        already = make_student("Fanta", "Keita")
        enroll(already, from_class)
        enroll(already, make_class("8ème B", academic_year=next_year))

        response = client_for(make_member(school, "admin_staff"), school).post(
            "/api/v1/enrollments/promote/",
            {"from_class": from_class.pk, "to_class": to_class.pk},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["promoted"] == 1
        assert [s["student_id"] for s in response.data["skipped"]] == [already.pk]
        new = Enrollment.objects.get(student=promoted, academic_year=next_year)
        assert new.kind == Enrollment.Kind.RE_ENROLMENT
        assert Enrollment.objects.get(student=promoted, academic_year=year).status == "completed"

    def test_cannot_promote_backwards(self, school, make_class, make_member, client_for):
        from_class = make_class("7ème A")
        response = client_for(make_member(school, "admin_staff"), school).post(
            "/api/v1/enrollments/promote/",
            {"from_class": from_class.pk, "to_class": make_class("7ème B").pk},
            format="json",
        )
        assert response.status_code == 400
