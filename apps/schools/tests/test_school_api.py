import pytest

from apps.audit.models import AuditLog


@pytest.mark.django_db
class TestCurrentSchool:
    def test_any_member_can_read_the_school_profile(self, school, make_member, client_for):
        teacher = make_member(school, "teacher")
        response = client_for(teacher, school).get("/api/v1/school/")
        assert response.status_code == 200
        assert response.data["name"] == "École Alpha"
        assert response.data["settings"]["idle_timeout_minutes"] == 30

    def test_teacher_cannot_edit_settings(self, school, make_member, client_for):
        teacher = make_member(school, "teacher")
        response = client_for(teacher, school).patch("/api/v1/school/", {"phone": "+224 600"}, format="json")
        assert response.status_code == 403

    def test_admin_edit_is_saved_and_audited(self, school, make_member, client_for):
        admin = make_member(school, "director")
        response = client_for(admin, school).patch(
            "/api/v1/school/",
            {"phone": "+224 620 00 00 00", "currency": "usd", "settings": {"idle_timeout_minutes": 45}},
            format="json",
        )
        assert response.status_code == 200, response.data
        assert response.data["currency"] == "USD"
        assert response.data["settings"]["idle_timeout_minutes"] == 45

        entry = AuditLog.objects.get(school=school, module="settings", action="update")
        assert entry.new_values == {
            "phone": "+224 620 00 00 00",
            "currency": "USD",
            "settings.idle_timeout_minutes": 45,
        }

    def test_validation_errors_use_the_standard_shape(self, school, make_member, client_for):
        admin = make_member(school, "director")
        response = client_for(admin, school).patch(
            "/api/v1/school/", {"settings": {"idle_timeout_minutes": 1}}, format="json"
        )
        assert response.status_code == 400
        assert response.data["code"] == "validation_error"
        assert "settings.idle_timeout_minutes" in response.data["fields"]

    def test_code_cannot_be_changed(self, school, make_member, client_for):
        admin = make_member(school, "director")
        client_for(admin, school).patch("/api/v1/school/", {"code": "hijack"}, format="json")
        school.refresh_from_db()
        assert school.code == "alpha"
