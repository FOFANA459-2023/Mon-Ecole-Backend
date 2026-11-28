"""Who manages whom: the Director runs the school's team, the platform owner runs schools and Directors."""

from datetime import date

import pytest

from apps.academics.services import create_academic_year
from apps.accounts.models import Membership
from apps.audit.models import AuditLog
from apps.people.models import StaffMember
from apps.schools.models import School
from conftest import PASSWORD

pytestmark = pytest.mark.django_db
PLATFORM = "/api/v1/platform/schools/"


def membership(user, school):
    return Membership.objects.filter(user=user, school=school).first()


class TestDirectorRunsTheTeam:
    def test_suspend_restore_and_remove_a_teacher(self, school, make_member, make_staff, client_for):
        director = client_for(make_member(school, "director"), school)
        teacher = make_member(school, "teacher")
        staff = make_staff(user=teacher)
        member_id = membership(teacher, school).pk

        assert director.patch(f"/api/v1/users/{member_id}/", {"is_active": False}).status_code == 200
        assert client_for(teacher, school).get("/api/v1/classes/").status_code == 404
        assert director.patch(f"/api/v1/users/{member_id}/", {"is_active": True}).status_code == 200
        assert client_for(teacher, school).get("/api/v1/classes/").status_code == 200

        assert director.delete(f"/api/v1/users/{member_id}/").status_code == 204
        assert membership(teacher, school) is None
        staff.refresh_from_db()
        assert staff.status == StaffMember.Status.ARCHIVED
        assert client_for(teacher, school).get("/api/v1/classes/").status_code == 404
        assert AuditLog.objects.filter(module="users", action="delete").exists()

    def test_cannot_suspend_or_remove_another_director_or_themselves(self, school, make_member, client_for):
        me = make_member(school, "director")
        other = make_member(school, "director", email="second.director@test.local")
        client = client_for(me, school)
        other_id, my_id = membership(other, school).pk, membership(me, school).pk
        assert client.patch(f"/api/v1/users/{other_id}/", {"is_active": False}).status_code == 400
        assert client.delete(f"/api/v1/users/{other_id}/").status_code == 400
        assert client.delete(f"/api/v1/users/{my_id}/").status_code == 400
        assert membership(other, school).is_active

    def test_archiving_a_staff_member_ends_their_access(self, school, make_member, make_staff, client_for):
        director = client_for(make_member(school, "director"), school)
        teacher = make_member(school, "teacher")
        staff = make_staff(user=teacher)
        assert director.post(f"/api/v1/staff/{staff.pk}/archive/").status_code == 200
        assert not membership(teacher, school).is_active
        assert director.post(f"/api/v1/staff/{staff.pk}/restore/").status_code == 200
        assert membership(teacher, school).is_active


def sign_in(user, password):
    from rest_framework.test import APIClient

    return APIClient().post("/api/v1/auth/login/", {"login": user.email, "password": password}, format="json")


class TestForgottenPasswords:
    """No email: the Director hands their staff a temporary password, the platform owner does it for
    Directors, and the person chooses their own at the next sign-in."""

    def test_director_resets_a_teachers_password(self, school, make_member, client_for):
        director = client_for(make_member(school, "director"), school)
        teacher = make_member(school, "teacher")
        url = f"/api/v1/users/{membership(teacher, school).pk}/reset-password/"
        assert director.post(url, {"new_password": "123"}, format="json").status_code == 400
        assert director.post(url, {"new_password": "Temporary-Pass-2026"}, format="json").status_code == 204

        assert sign_in(teacher, PASSWORD).status_code == 401
        response = sign_in(teacher, "Temporary-Pass-2026")
        assert response.status_code == 200
        assert response.data["user"]["must_change_password"] is True
        assert AuditLog.objects.filter(module="users", action="password_reset").exists()

    def test_works_for_someone_who_never_received_the_invitation_email(self, school, make_member, client_for):
        director = client_for(make_member(school, "director"), school)
        teacher = make_member(school, "teacher")
        teacher.email_verified_at = None
        teacher.save(update_fields=["email_verified_at"])
        url = f"/api/v1/users/{membership(teacher, school).pk}/reset-password/"
        assert director.post(url, {"new_password": "Temporary-Pass-2026"}, format="json").status_code == 204
        assert sign_in(teacher, "Temporary-Pass-2026").status_code == 200

    def test_director_cannot_reset_a_director_or_themselves(self, school, make_member, client_for):
        me = make_member(school, "director")
        other = make_member(school, "director", email="second.director@test.local")
        client = client_for(me, school)
        for person in (me, other):
            url = f"/api/v1/users/{membership(person, school).pk}/reset-password/"
            assert client.post(url, {"new_password": "Temporary-Pass-2026"}, format="json").status_code == 400
        assert sign_in(other, PASSWORD).status_code == 200

    def test_nor_a_teacher_who_directs_another_school(self, school, other_school, make_member, client_for):
        director = client_for(make_member(school, "director"), school)
        teacher = make_member(school, "teacher")
        Membership.objects.create(user=teacher, school=other_school).roles.set(
            other_school.roles.filter(key="director")
        )
        url = f"/api/v1/users/{membership(teacher, school).pk}/reset-password/"
        assert director.post(url, {"new_password": "Temporary-Pass-2026"}, format="json").status_code == 400

    def test_teachers_cannot_reset_anyone(self, school, make_member, client_for):
        teacher = make_member(school, "teacher")
        colleague = make_member(school, "teacher", email="colleague@test.local")
        url = f"/api/v1/users/{membership(colleague, school).pk}/reset-password/"
        client = client_for(teacher, school)
        response = client.post(url, {"new_password": "Temporary-Pass-2026"}, format="json")
        assert response.status_code == 403

    def test_platform_owner_resets_a_directors_password(self, school, owner, make_member, client_for):
        director = make_member(school, "director")
        client = client_for(owner)
        url = f"{PLATFORM}{school.pk}/directors/reset-password/"
        body = {"user_id": director.pk, "new_password": "Temporary-Pass-2026"}
        assert client.post(url, body, format="json").status_code == 204
        assert sign_in(director, "Temporary-Pass-2026").data["user"]["must_change_password"] is True

        teacher = make_member(school, "teacher")
        body = {"user_id": teacher.pk, "new_password": "Temporary-Pass-2026"}
        assert client.post(url, body, format="json").status_code == 400


class TestNewSchoolYear:
    def test_staff_not_kept_leave_and_lose_access(self, school, year, make_member, make_staff, client_for):
        director_user = make_member(school, "director")
        make_staff("Awa", "Director", user=director_user)
        stays = make_staff("Ibrahima", "Sow", user=make_member(school, "teacher"))
        leaver_user = make_member(school, "accountant")
        make_staff("Fanta", "Camara", user=leaver_user)
        make_staff("Sekou", "Keita")  # no account: simply archived
        next_year = create_academic_year(
            school, name="2027-2028", start_date=date(2027, 9, 1), end_date=date(2028, 6, 30), term_count=3
        )

        response = client_for(director_user, school).post(
            f"/api/v1/academic-years/{next_year.pk}/set-current/",
            {"continuing_staff_ids": [stays.pk]},
            format="json",
        )

        assert response.status_code == 200 and response.data["is_current"]
        statuses = dict(StaffMember.objects.values_list("last_name", "status"))
        assert statuses == {"Director": "active", "Sow": "active", "Camara": "archived", "Keita": "archived"}
        assert not membership(leaver_user, school).is_active
        assert membership(director_user, school).is_active

    def test_changing_the_year_alone_keeps_everyone(self, school, year, make_member, make_staff, client_for):
        director_user = make_member(school, "director")
        make_staff("Fanta", "Camara", user=make_member(school, "teacher"))
        next_year = create_academic_year(
            school, name="2027-2028", start_date=date(2027, 9, 1), end_date=date(2028, 6, 30)
        )
        client = client_for(director_user, school)
        assert client.post(f"/api/v1/academic-years/{next_year.pk}/set-current/").status_code == 200
        assert not StaffMember.objects.filter(status=StaffMember.Status.ARCHIVED).exists()


class TestPlatformOwner:
    def test_has_every_director_permission(self, school, owner, make_staff, make_member, client_for):
        from apps.accounts.permissions_registry import ALL_CODES

        staff = make_staff()
        teacher = make_member(school, "teacher")
        client = client_for(owner, school)
        assert client.get("/api/v1/dashboard/summary/").status_code == 200
        assert client.post(f"/api/v1/staff/{staff.pk}/archive/").status_code == 200
        assert client.get("/api/v1/users/").status_code == 200
        member_id = membership(teacher, school).pk
        assert client.patch(f"/api/v1/users/{member_id}/", {"is_active": False}).status_code == 200
        url = f"/api/v1/users/{member_id}/reset-password/"
        assert client.post(url, {"new_password": "Temporary-Pass-2026"}, format="json").status_code == 204
        me = client_for(owner).get("/api/v1/me/").data
        permissions = next(m["permissions"] for m in me["memberships"] if m["school"]["id"] == school.pk)
        assert set(permissions) == ALL_CODES

    def test_adds_suspends_restores_and_removes_directors(self, school, owner, make_member, client_for):
        first = make_member(school, "director")
        client = client_for(owner)
        response = client.post(
            f"{PLATFORM}{school.pk}/directors/",
            {"first_name": "Mariama", "last_name": "Bah", "email": "mariama@test.local"},
            format="json",
        )
        assert response.status_code == 201
        assert {d["email"] for d in response.data["directors"]} == {first.email, "mariama@test.local"}

        url = f"{PLATFORM}{school.pk}/directors/change/"
        response = client.post(url, {"user_id": first.pk, "action": "suspend"}, format="json")
        assert response.status_code == 200
        assert next(d for d in response.data["directors"] if d["id"] == first.pk)["is_active"] is False
        assert client.post(url, {"user_id": first.pk, "action": "restore"}, format="json").status_code == 200
        assert client.post(url, {"user_id": first.pk, "action": "remove"}, format="json").status_code == 200
        assert membership(first, school) is None

    def test_a_school_keeps_one_active_director(self, school, owner, make_member, client_for):
        only = make_member(school, "director")
        url = f"{PLATFORM}{school.pk}/directors/change/"
        payload = {"user_id": only.pk, "action": "remove"}
        assert client_for(owner).post(url, payload, format="json").status_code == 400

    def test_deletes_a_suspended_school_after_typing_its_code(
        self,
        school,
        other_school,
        owner,
        make_member,
        make_class,
        client_for,
        django_capture_on_commit_callbacks,
    ):
        make_class()
        director = make_member(school, "director")
        client = client_for(owner)
        url = f"{PLATFORM}{school.pk}/delete/"
        assert client.post(url, {"confirm_code": school.code}, format="json").status_code == 400  # active
        client.post(f"{PLATFORM}{school.pk}/status/", {"status": "suspended"}, format="json")
        assert client.post(url, {"confirm_code": "wrong"}, format="json").status_code == 400

        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(url, {"confirm_code": school.code.upper()}, format="json")

        assert response.status_code == 202 and response.data["status"] == "deleting"
        assert not School.objects.filter(pk=school.pk).exists()
        assert School.objects.filter(pk=other_school.pk).exists()
        director.refresh_from_db()  # the account stays: it may belong to other schools
        assert AuditLog.objects.filter(module="platform", summary__startswith="School deleted").exists()

    def test_nobody_else_reaches_the_platform(self, school, make_member, client_for):
        director = client_for(make_member(school, "director"), school)
        assert director.post(f"{PLATFORM}{school.pk}/directors/", {}, format="json").status_code == 403
        assert director.post(f"{PLATFORM}{school.pk}/delete/", {}, format="json").status_code == 403
