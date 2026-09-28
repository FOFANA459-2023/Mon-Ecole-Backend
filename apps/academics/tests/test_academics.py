from datetime import date

import pytest

from apps.academics.models import AcademicYear, ClassSubject, Level, Subject, Term
from apps.academics.services import split_terms
from apps.audit.models import AuditLog


def test_split_terms_covers_the_whole_year_without_gaps():
    periods = split_terms(date(2026, 9, 1), date(2027, 6, 30), 3)
    assert periods[0][0] == date(2026, 9, 1)
    assert periods[-1][1] == date(2027, 6, 30)
    for (_, end), (start, _) in zip(periods, periods[1:], strict=False):
        assert (start - end).days == 1


@pytest.mark.django_db
class TestAcademicYears:
    def test_admin_creates_a_year_with_terms_and_first_year_becomes_current(
        self, school, make_member, client_for
    ):
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).post(
            "/api/v1/academic-years/",
            {"name": "2026-2027", "start_date": "2026-09-01", "end_date": "2027-06-30", "term_count": 3},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["is_current"] is True
        assert [t["name"] for t in response.data["terms"]] == ["Trimestre 1", "Trimestre 2", "Trimestre 3"]

    def test_set_current_moves_the_flag(self, school, year, make_member, client_for):
        admin = make_member(school, "super_admin")
        client = client_for(admin, school)
        response = client.post(
            "/api/v1/academic-years/",
            {"name": "2027-2028", "start_date": "2027-09-01", "end_date": "2028-06-30"},
            format="json",
        )
        new_id = response.data["id"]
        assert response.data["is_current"] is False
        assert client.post(f"/api/v1/academic-years/{new_id}/set-current/").status_code == 200
        assert AcademicYear.objects.get(is_current=True).pk == new_id

    def test_teacher_can_read_but_not_create_years(self, school, year, make_member, client_for):
        teacher = make_member(school, "teacher")
        client = client_for(teacher, school)
        assert client.get("/api/v1/academic-years/").status_code == 200
        response = client.post(
            "/api/v1/academic-years/",
            {"name": "x", "start_date": "2027-09-01", "end_date": "2028-06-30"},
            format="json",
        )
        assert response.status_code == 403

    def test_term_must_fall_inside_its_year(self, school, year, make_member, client_for):
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).post(
            "/api/v1/terms/",
            {
                "academic_year": year.pk,
                "name": "Été",
                "order": 4,
                "start_date": "2027-07-01",
                "end_date": "2027-08-15",
            },
            format="json",
        )
        assert response.status_code == 400
        assert Term.objects.filter(academic_year=year).count() == 3


@pytest.mark.django_db
class TestClassesAndSubjects:
    def test_create_class_with_level_and_teacher(
        self, school, year, level, make_member, make_staff, client_for
    ):
        admin = make_member(school, "super_admin")
        teacher = make_staff()
        response = client_for(admin, school).post(
            "/api/v1/classes/",
            {
                "academic_year": year.pk,
                "level": level.pk,
                "name": "7ème A",
                "room": "B12",
                "class_teacher": teacher.pk,
                "capacity": 45,
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["class_teacher_name"] == teacher.full_name
        assert response.data["enrolled_count"] == 0

    def test_class_name_is_unique_per_year(self, school, make_class, year, level, make_member, client_for):
        make_class("7ème A")
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).post(
            "/api/v1/classes/", {"academic_year": year.pk, "level": level.pk, "name": "7ème a"}, format="json"
        )
        assert response.status_code == 400

    def test_level_of_another_school_is_rejected(self, school, other_school, year, make_member, client_for):
        foreign_level = Level.objects.create(school=other_school, name="CP", order=1)
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).post(
            "/api/v1/classes/",
            {"academic_year": year.pk, "level": foreign_level.pk, "name": "CP A"},
            format="json",
        )
        assert response.status_code == 400
        assert "level" in response.data["fields"]

    def test_deleting_a_level_in_use_is_refused_and_not_audited(
        self, school, level, make_class, make_member, client_for
    ):
        make_class()
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).delete(f"/api/v1/levels/{level.pk}/")
        assert response.status_code == 400
        assert response.data["code"] == "in_use"
        assert not AuditLog.objects.filter(action="delete", entity_type="academics.level").exists()

    def test_subject_code_is_uppercased_and_unique(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
        client = client_for(admin, school)
        response = client.post(
            "/api/v1/subjects/",
            {"name": "Mathématiques", "code": "math", "default_coefficient": "4"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["code"] == "MATH"
        assert (
            client.post("/api/v1/subjects/", {"name": "Maths 2", "code": "MATH"}, format="json").status_code
            == 400
        )

    def test_class_subject_takes_the_subject_default_coefficient(
        self, school, make_class, make_member, client_for
    ):
        admin = make_member(school, "super_admin")
        subject = Subject.objects.create(school=school, name="Français", code="FR", default_coefficient=3)
        class_group = make_class()
        response = client_for(admin, school).post(
            "/api/v1/class-subjects/", {"class_group": class_group.pk, "subject": subject.pk}, format="json"
        )
        assert response.status_code == 201, response.data
        assert response.data["coefficient"] == "3.0"


@pytest.mark.django_db
class TestTeacherScope:
    def test_teacher_only_sees_classes_they_teach_or_lead(
        self, school, make_class, make_member, make_staff, client_for
    ):
        teacher_user = make_member(school, "teacher")
        staff = make_staff(user=teacher_user)
        led = make_class("7ème A", class_teacher=staff)
        taught = make_class("7ème B")
        make_class("7ème C")
        subject = Subject.objects.create(school=school, name="Histoire", code="HIST")
        ClassSubject.objects.create(school=school, class_group=taught, subject=subject, teacher=staff)

        response = client_for(teacher_user, school).get("/api/v1/classes/")
        assert {row["name"] for row in response.data["results"]} == {led.name, taught.name}

    def test_director_sees_every_class(self, school, make_class, make_member, client_for):
        make_class("7ème A")
        make_class("7ème B")
        director = make_member(school, "director")
        assert client_for(director, school).get("/api/v1/classes/").data["count"] == 2

    def test_teacher_without_staff_profile_sees_nothing(self, school, make_class, make_member, client_for):
        make_class()
        teacher = make_member(school, "teacher")
        assert client_for(teacher, school).get("/api/v1/classes/").data["count"] == 0
