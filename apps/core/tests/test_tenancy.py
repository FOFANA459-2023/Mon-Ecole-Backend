import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role, User
from apps.audit import services as audit
from apps.core.permissions import required_permissions_for
from apps.core.sequences import next_number


@pytest.mark.django_db
class TestTenantIsolation:
    def test_member_of_another_school_gets_404(self, school, other_school, make_member, client_for):
        outsider = make_member(other_school, "super_admin")
        response = client_for(outsider, school).get("/api/v1/school/")
        assert response.status_code == 404

    def test_single_membership_is_used_without_header(self, school, make_member, client_for):
        user = make_member(school, "teacher")
        response = client_for(user).get("/api/v1/school/")
        assert response.status_code == 200
        assert response.data["code"] == "alpha"

    def test_several_memberships_require_the_header(self, school, other_school, make_member, client_for):
        user = make_member(school, "teacher")
        membership = Membership.objects.create(user=user, school=other_school)
        membership.roles.set(Role.objects.filter(school=other_school, key="teacher"))

        response = client_for(user).get("/api/v1/school/")
        assert response.status_code == 400
        assert response.data["code"] == "school_required"
        assert client_for(user, other_school).get("/api/v1/school/").data["code"] == "beta"

    def test_inactive_membership_has_no_access(self, school, make_member, client_for):
        user = make_member(school, "super_admin")
        Membership.objects.filter(user=user).update(is_active=False)
        assert client_for(user, school).get("/api/v1/school/").status_code == 404

    def test_non_numeric_header_is_404(self, school, make_member):
        user = make_member(school, "teacher")
        client = APIClient()
        client.force_authenticate(user)
        assert client.get("/api/v1/school/", HTTP_X_SCHOOL_ID="abc").status_code == 404

    def test_platform_admin_can_open_any_school(self, school, client_for):
        admin = User.objects.create_superuser(
            username="root", email="root@test.local", password="x-Pass-12345"
        )
        assert client_for(admin, school).get("/api/v1/school/").status_code == 200

    def test_audit_log_only_shows_current_school(self, school, other_school, make_member, client_for):
        audit.record("test_event", school=school, summary="alpha event")
        audit.record("test_event", school=other_school, summary="beta event")
        admin = make_member(school, "super_admin")

        response = client_for(admin, school).get("/api/v1/audit-logs/", {"action": "test_event"})
        assert response.status_code == 200
        assert [row["summary"] for row in response.data["results"]] == ["alpha event"]

    def test_users_list_only_shows_current_school(self, school, other_school, make_member, client_for):
        admin = make_member(school, "super_admin")
        make_member(other_school, "teacher")
        response = client_for(admin, school).get("/api/v1/users/")
        assert {row["user"]["email"] for row in response.data["results"]} == {admin.email}

    def test_cannot_open_another_schools_member_by_id(self, school, other_school, make_member, client_for):
        admin = make_member(school, "super_admin")
        foreign = make_member(other_school, "teacher")
        foreign_membership = Membership.objects.get(user=foreign)
        response = client_for(admin, school).patch(
            f"/api/v1/users/{foreign_membership.pk}/", {"is_active": False}, format="json"
        )
        assert response.status_code == 404


@pytest.mark.django_db
def test_every_phase_2_record_of_another_school_is_invisible(school, other_school, make_member, client_for):
    """Build one record of each kind in another school and read it with this school's super admin."""
    from datetime import date

    from apps.academics.models import ClassGroup, ClassSubject, Level, Subject
    from apps.academics.services import create_academic_year
    from apps.documents.models import Document
    from apps.enrollments.services import enroll
    from apps.people.services import create_staff, create_student, link_guardian

    year = create_academic_year(
        other_school, name="2026-2027", start_date=date(2026, 9, 1), end_date=date(2027, 6, 30), term_count=3
    )
    level = Level.objects.create(school=other_school, name="CP")
    class_group = ClassGroup.objects.create(school=other_school, academic_year=year, level=level, name="CP A")
    subject = Subject.objects.create(school=other_school, name="Maths", code="M")
    class_subject = ClassSubject.objects.create(school=other_school, class_group=class_group, subject=subject)
    student = create_student(other_school, data={"first_name": "A", "last_name": "B"})
    guardian = link_guardian(
        student, guardian_data={"first_name": "C", "last_name": "D", "phone": "1"}
    ).guardian
    staff = create_staff(other_school, data={"first_name": "E", "last_name": "F"})
    enrolment = enroll(student, class_group)
    document = Document.objects.create(
        school=other_school, owner_type="student", owner_id=student.pk, title="x", file="x.pdf"
    )

    client = client_for(make_member(school, "super_admin"), school)
    urls = [
        f"/api/v1/academic-years/{year.pk}/",
        f"/api/v1/terms/{year.terms.first().pk}/",
        f"/api/v1/levels/{level.pk}/",
        f"/api/v1/classes/{class_group.pk}/",
        f"/api/v1/subjects/{subject.pk}/",
        f"/api/v1/class-subjects/{class_subject.pk}/",
        f"/api/v1/students/{student.pk}/",
        f"/api/v1/students/{student.pk}/card/",
        f"/api/v1/guardians/{guardian.pk}/",
        f"/api/v1/staff/{staff.pk}/",
        f"/api/v1/enrollments/{enrolment.pk}/",
        f"/api/v1/enrollments/{enrolment.pk}/form/",
        f"/api/v1/documents/{document.pk}/download/",
    ]
    for url in urls:
        assert client.get(url).status_code == 404, url
    assert (
        client.patch(f"/api/v1/students/{student.pk}/", {"first_name": "Z"}, format="json").status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/enrollments/{enrolment.pk}/withdraw/", {"reason": "x"}, format="json"
        ).status_code
        == 404
    )
    assert client.get("/api/v1/students/").data["count"] == 0


class _View:
    def __init__(self, action=None, mapping=None):
        self.action = action
        if mapping is not None:
            self.required_permissions = mapping


class _Request:
    method = "GET"


def test_views_without_declared_permissions_are_denied():
    assert required_permissions_for(_View("list"), _Request()) is None


def test_wildcard_permissions_apply_to_every_action():
    view = _View("destroy", {"*": ["users.manage"]})
    assert required_permissions_for(view, _Request()) == ["users.manage"]


@pytest.mark.django_db
def test_sequences_are_independent_per_school_and_year(school, other_school):
    assert next_number(school, "receipt", "REC", 2026) == "REC-2026-000001"
    assert next_number(school, "receipt", "REC", 2026) == "REC-2026-000002"
    assert next_number(other_school, "receipt", "REC", 2026) == "REC-2026-000001"
    assert next_number(school, "receipt", "REC", 2027) == "REC-2027-000001"


def test_healthz(client, db):
    response = client.get("/healthz", HTTP_HOST="10.0.12.34")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
