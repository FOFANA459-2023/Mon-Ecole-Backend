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
    assert set(SYSTEM_ROLES["super_admin"]["permissions"]) == ALL_CODES


@pytest.mark.django_db
class TestPermissions:
    def test_teacher_cannot_manage_users(self, school, make_member, client_for):
        teacher = make_member(school, "teacher")
        assert client_for(teacher, school).get("/api/v1/users/").status_code == 403

    def test_director_can_read_audit_log_but_not_manage_users(self, school, make_member, client_for):
        director = make_member(school, "director")
        client = client_for(director, school)
        assert client.get("/api/v1/audit-logs/").status_code == 200
        assert client.get("/api/v1/users/").status_code == 403

    def test_permission_registry_is_grouped_by_module(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).get("/api/v1/permissions/")
        assert response.status_code == 200
        codes = {p["code"] for group in response.data for p in group["permissions"]}
        assert codes == ALL_CODES


@pytest.mark.django_db
class TestMembers:
    def test_new_person_gets_an_invitation_email(
        self, school, make_member, client_for, django_capture_on_commit_callbacks
    ):
        admin = make_member(school, "super_admin")
        with django_capture_on_commit_callbacks(execute=True):
            response = client_for(admin, school).post(
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
        assert response.data["user"]["has_password"] is False
        assert [r["key"] for r in response.data["roles"]] == ["teacher"]
        assert len(mail.outbox) == 1
        assert "reset-password?uid=" in mail.outbox[0].body
        assert AuditLog.objects.filter(school=school, module="users", action="create").exists()

    def test_initial_password_forces_a_change_at_first_login(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).post(
            "/api/v1/users/",
            {
                "email": "compta@test.local",
                "first_name": "Sékou",
                "last_name": "Keita",
                "role_ids": [role_id(school, "accountant")],
                "password": "Temporary-Pass-2026",
            },
            format="json",
        )
        assert response.status_code == 201
        assert User.objects.get(email="compta@test.local").must_change_password is True

    def test_existing_account_from_another_school_is_reused(
        self, school, other_school, make_member, client_for
    ):
        admin = make_member(school, "super_admin")
        teacher_elsewhere = make_member(other_school, "teacher")
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

    def test_role_from_another_school_is_rejected(self, school, other_school, make_member, client_for):
        admin = make_member(school, "super_admin")
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
        admin = make_member(school, "super_admin")
        membership = Membership.objects.get(user=admin)
        response = client_for(admin, school).patch(
            f"/api/v1/users/{membership.pk}/", {"is_active": False}, format="json"
        )
        assert response.status_code == 400

    def test_last_super_admin_cannot_be_demoted(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
        other_admin = make_member(school, "super_admin", email="second@test.local")
        membership = Membership.objects.get(user=admin)

        # Two admins: demoting one is fine.
        response = client_for(other_admin, school).patch(
            f"/api/v1/users/{membership.pk}/", {"role_ids": [role_id(school, "director")]}, format="json"
        )
        assert response.status_code == 200
        # Now other_admin is the last one and cannot be demoted by a director-turned-admin... by anyone.
        last = Membership.objects.get(user=other_admin)
        Membership.objects.get(user=admin).roles.add(Role.objects.get(school=school, key="super_admin"))
        Membership.objects.get(user=admin).roles.remove(Role.objects.get(school=school, key="super_admin"))
        response = client_for(other_admin, school).patch(
            f"/api/v1/users/{last.pk}/", {"role_ids": [role_id(school, "teacher")]}, format="json"
        )
        assert response.status_code == 400

    def test_role_change_is_audited_with_old_and_new_values(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
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
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).post(
            "/api/v1/roles/",
            {"name": "Surveillant", "permissions": ["attendance.view", "attendance.record"]},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["member_count"] == 0
        assert response.data["is_system"] is False

    def test_unknown_permission_codes_are_rejected(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).post(
            "/api/v1/roles/", {"name": "Hacker", "permissions": ["root.everything"]}, format="json"
        )
        assert response.status_code == 400

    def test_system_roles_cannot_be_deleted(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).delete(f"/api/v1/roles/{role_id(school, 'teacher')}/")
        assert response.status_code == 400

    def test_super_admin_permissions_cannot_be_reduced(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
        response = client_for(admin, school).patch(
            f"/api/v1/roles/{role_id(school, 'super_admin')}/", {"permissions": []}, format="json"
        )
        assert response.status_code == 200
        assert set(response.data["permissions"]) == ALL_CODES

    def test_role_in_use_cannot_be_deleted(self, school, make_member, client_for):
        admin = make_member(school, "super_admin")
        role = Role.objects.create(school=school, name="Custom", permissions=["students.view"])
        Membership.objects.get(user=admin).roles.add(role)
        assert client_for(admin, school).delete(f"/api/v1/roles/{role.pk}/").status_code == 400
