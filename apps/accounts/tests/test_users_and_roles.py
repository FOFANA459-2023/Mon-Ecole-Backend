import pytest
from django.core import mail

from apps.accounts.models import Membership, Role, User
from apps.accounts.permissions_registry import ALL_CODES, SYSTEM_ROLES
from apps.audit.models import AuditLog


def role_id(school, key):
    return Role.objects.get(school=school, key=key).pk


def test_system_roles_only_use_known_permission_codes():
    for key, spec in SYSTEM_ROLES.items():
        assert set(spec["permissions"]) <= ALL_CODES, key
    assert set(SYSTEM_ROLES["director"]["permissions"]) == ALL_CODES
    assert "super_admin" not in SYSTEM_ROLES  # the platform owner is not a school role


@pytest.mark.django_db
class TestPermissions:
    def test_teacher_cannot_manage_users(self, school, make_member, client_for):
        teacher = make_member(school, "teacher")
        assert client_for(teacher, school).get("/api/v1/users/").status_code == 403

    def test_accountant_cannot_manage_users(self, school, make_member, client_for):
        accountant = make_member(school, "accountant")
        assert client_for(accountant, school).get("/api/v1/users/").status_code == 403

    def test_director_manages_users_and_reads_the_audit_log(self, school, make_member, client_for):
        client = client_for(make_member(school, "director"), school)
        assert client.get("/api/v1/audit-logs/").status_code == 200
        assert client.get("/api/v1/users/").status_code == 200

    def test_permission_registry_is_grouped_by_module(self, school, make_member, client_for):
        admin = make_member(school, "director")
        response = client_for(admin, school).get("/api/v1/permissions/")
        assert response.status_code == 200
        codes = {p["code"] for group in response.data for p in group["permissions"]}
        assert codes == ALL_CODES


@pytest.mark.django_db
class TestMembers:
    def test_new_person_gets_a_verification_link_and_temporary_password(
        self, school, make_member, client_for, django_capture_on_commit_callbacks
    ):
        director = make_member(school, "director")
        with django_capture_on_commit_callbacks(execute=True):
            response = client_for(director, school).post(
                "/api/v1/users/",
                {
                    "email": "New.Teacher@Test.local",
                    "first_name": "Awa",
                    "last_name": "Touré",
                    "role_ids": [role_id(school, "teacher")],
                },
                format="json",
            )
        assert response.status_code == 201, response.data
        assert response.data["user"]["email"] == "new.teacher@test.local"
        assert response.data["user"]["account_status"] == "pending"
        assert [r["key"] for r in response.data["roles"]] == ["teacher"]
        assert len(mail.outbox) == 1
        body = mail.outbox[0].body
        assert "/verify-email?token=" in body
        assert "Temporary password: " in body
        user = User.objects.get(email="new.teacher@test.local")
        assert user.must_change_password is True
        assert user.email_verified_at is None
        assert AuditLog.objects.filter(school=school, module="users", action="create").exists()

    def test_directors_cannot_create_directors(self, school, make_member, client_for):
        response = client_for(make_member(school, "director"), school).post(
            "/api/v1/users/",
            {
                "email": "deputy@test.local",
                "first_name": "a",
                "last_name": "b",
                "role_ids": [role_id(school, "director")],
            },
            format="json",
        )
        assert response.status_code == 400
        assert "role_ids" in response.data["fields"]
        assert not User.objects.filter(email="deputy@test.local").exists()

    def test_the_owner_can_add_a_director(self, school, owner, client_for):
        response = client_for(owner, school).post(
            "/api/v1/users/",
            {
                "email": "deputy@test.local",
                "first_name": "a",
                "last_name": "b",
                "role_ids": [role_id(school, "director")],
            },
            format="json",
        )
        assert response.status_code == 201, response.data

    def test_existing_account_from_another_school_is_reused(
        self, school, other_school, make_member, client_for, django_capture_on_commit_callbacks
    ):
        admin = make_member(school, "director")
        teacher_elsewhere = make_member(other_school, "teacher")
        with django_capture_on_commit_callbacks(execute=True):
            response = client_for(admin, school).post(
                "/api/v1/users/",
                {
                    "email": teacher_elsewhere.email,
                    "first_name": "x",
                    "last_name": "y",
                    "role_ids": [role_id(school, "teacher")],
                },
                format="json",
            )
        assert response.status_code == 201
        assert User.objects.filter(email=teacher_elsewhere.email).count() == 1
        assert Membership.objects.filter(user=teacher_elsewhere).count() == 2
        # An active account keeps its password; the person is only told about the new school.
        assert User.objects.get(pk=teacher_elsewhere.pk).check_password("Correct-Horse-2026")
        assert "Temporary password" not in mail.outbox[0].body

    def test_role_from_another_school_is_rejected(self, school, other_school, make_member, client_for):
        admin = make_member(school, "director")
        response = client_for(admin, school).post(
            "/api/v1/users/",
            {
                "email": "a@test.local",
                "first_name": "a",
                "last_name": "b",
                "role_ids": [role_id(other_school, "teacher")],
            },
            format="json",
        )
        assert response.status_code == 400
        assert "role_ids" in response.data["fields"]

    def test_cannot_remove_own_access(self, school, make_member, client_for):
        admin = make_member(school, "director")
        membership = Membership.objects.get(user=admin)
        response = client_for(admin, school).patch(
            f"/api/v1/users/{membership.pk}/", {"is_active": False}, format="json"
        )
        assert response.status_code == 400

    def test_only_the_owner_changes_a_directors_role_and_one_director_always_remains(
        self, school, owner, make_member, client_for
    ):
        director = make_member(school, "director")
        second = make_member(school, "director", email="second@test.local")
        membership = Membership.objects.get(user=director)
        demote = {"role_ids": [role_id(school, "teacher")]}

        # A Director cannot demote another Director.
        response = client_for(second, school).patch(f"/api/v1/users/{membership.pk}/", demote, format="json")
        assert response.status_code == 400
        # The owner can, while another Director remains...
        response = client_for(owner, school).patch(f"/api/v1/users/{membership.pk}/", demote, format="json")
        assert response.status_code == 200
        # ...but not the last one.
        last = Membership.objects.get(user=second)
        response = client_for(owner, school).patch(f"/api/v1/users/{last.pk}/", demote, format="json")
        assert response.status_code == 400

    def test_resending_an_invitation_replaces_the_temporary_password(
        self, school, make_member, client_for, django_capture_on_commit_callbacks
    ):
        director = make_member(school, "director")
        client = client_for(director, school)
        with django_capture_on_commit_callbacks(execute=True):
            created = client.post(
                "/api/v1/users/",
                {
                    "email": "t@test.local",
                    "first_name": "a",
                    "last_name": "b",
                    "role_ids": [role_id(school, "teacher")],
                },
                format="json",
            )
        first_hash = User.objects.get(email="t@test.local").password
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(f"/api/v1/users/{created.data['id']}/send-invite/")
        assert response.status_code == 204
        assert User.objects.get(email="t@test.local").password != first_hash
        assert len(mail.outbox) == 2

    def test_no_invitation_for_an_account_already_activated(self, school, make_member, client_for):
        director = make_member(school, "director")
        teacher = make_member(school, "teacher")
        membership = Membership.objects.get(user=teacher)
        response = client_for(director, school).post(f"/api/v1/users/{membership.pk}/send-invite/")
        assert response.status_code == 400

    def test_role_change_is_audited_with_old_and_new_values(self, school, make_member, client_for):
        admin = make_member(school, "director")
        teacher = make_member(school, "teacher")
        membership = Membership.objects.get(user=teacher)
        client_for(admin, school).patch(
            f"/api/v1/users/{membership.pk}/", {"role_ids": [role_id(school, "accountant")]}, format="json"
        )
        entry = AuditLog.objects.filter(school=school, action="update", module="users").latest("created_at")
        assert entry.old_values == {"roles": ["Teacher"]}
        assert entry.new_values == {"roles": ["Accountant"]}


@pytest.mark.django_db
class TestRoles:
    def test_create_custom_role(self, school, make_member, client_for):
        admin = make_member(school, "director")
        response = client_for(admin, school).post(
            "/api/v1/roles/",
            {"name": "Surveillant", "permissions": ["attendance.view", "attendance.record"]},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["member_count"] == 0
        assert response.data["is_system"] is False

    def test_unknown_permission_codes_are_rejected(self, school, make_member, client_for):
        admin = make_member(school, "director")
        response = client_for(admin, school).post(
            "/api/v1/roles/", {"name": "Hacker", "permissions": ["root.everything"]}, format="json"
        )
        assert response.status_code == 400

    def test_system_roles_cannot_be_deleted(self, school, make_member, client_for):
        admin = make_member(school, "director")
        response = client_for(admin, school).delete(f"/api/v1/roles/{role_id(school, 'teacher')}/")
        assert response.status_code == 400

    def test_director_permissions_cannot_be_reduced(self, school, make_member, client_for):
        admin = make_member(school, "director")
        response = client_for(admin, school).patch(
            f"/api/v1/roles/{role_id(school, 'director')}/", {"permissions": []}, format="json"
        )
        assert response.status_code == 200
        assert set(response.data["permissions"]) == ALL_CODES

    def test_role_in_use_cannot_be_deleted(self, school, make_member, client_for):
        admin = make_member(school, "director")
        role = Role.objects.create(school=school, name="Custom", permissions=["students.view"])
        Membership.objects.get(user=admin).roles.add(role)
        assert client_for(admin, school).delete(f"/api/v1/roles/{role.pk}/").status_code == 400
