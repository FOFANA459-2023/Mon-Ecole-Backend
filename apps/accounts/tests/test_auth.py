import re

import pytest
from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from conftest import PASSWORD

COOKIE = settings.REFRESH_COOKIE_NAME


def login(client, login_value, password=PASSWORD):
    return client.post("/api/v1/auth/login/", {"login": login_value, "password": password}, format="json")


@pytest.mark.django_db
class TestLogin:
    def test_returns_access_token_and_http_only_refresh_cookie(self, school, make_member):
        user = make_member(school, "director")
        response = login(APIClient(), user.email)

        assert response.status_code == 200
        assert response.data["access"]
        assert response.data["user"]["email"] == user.email
        cookie = response.cookies[COOKIE]
        assert cookie["httponly"]
        assert cookie["path"] == "/api/v1/auth/"
        assert AuditLog.objects.filter(school=school, user=user, action="login").exists()

    def test_accepts_username_case_insensitively(self, school, make_member):
        user = make_member(school, "teacher")
        assert login(APIClient(), user.username.upper()).status_code == 200

    def test_wrong_password_is_rejected_and_audited(self, school, make_member):
        user = make_member(school, "teacher")
        response = login(APIClient(), user.email, "wrong-password")

        assert response.status_code == 401
        assert response.data["code"] == "invalid_credentials"
        assert COOKIE not in response.cookies
        assert AuditLog.objects.filter(school=school, user=user, action="login_failed").exists()

    def test_inactive_user_cannot_sign_in(self, school, make_member):
        user = make_member(school, "teacher")
        user.is_active = False
        user.save()
        assert login(APIClient(), user.email).status_code == 401

    def test_access_token_authenticates_api_calls(self, school, make_member):
        user = make_member(school, "teacher")
        access = login(APIClient(), user.email).data["access"]
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        assert client.get("/api/v1/me/").status_code == 200


@pytest.mark.django_db
class TestRefreshAndLogout:
    def test_refresh_rotates_the_cookie_and_rejects_the_old_token(self, school, make_member):
        user = make_member(school, "teacher")
        client = APIClient()
        login(client, user.email)
        old_cookie = client.cookies[COOKIE].value

        response = client.post("/api/v1/auth/refresh/")
        assert response.status_code == 200
        assert response.data["access"]
        assert client.cookies[COOKIE].value != old_cookie

        replay = APIClient()
        replay.cookies[COOKIE] = old_cookie
        assert replay.post("/api/v1/auth/refresh/").status_code == 401

    def test_refresh_without_cookie_is_401(self, db):
        response = APIClient().post("/api/v1/auth/refresh/")
        assert response.status_code == 401
        assert response.data["code"] == "session_expired"

    def test_refresh_rejects_foreign_origin(self, school, make_member):
        user = make_member(school, "teacher")
        client = APIClient()
        login(client, user.email)
        response = client.post("/api/v1/auth/refresh/", HTTP_ORIGIN="https://evil.example")
        assert response.status_code == 403

    def test_logout_blacklists_the_refresh_token(self, school, make_member):
        user = make_member(school, "teacher")
        client = APIClient()
        login(client, user.email)
        cookie = client.cookies[COOKIE].value

        assert client.post("/api/v1/auth/logout/").status_code == 204
        replay = APIClient()
        replay.cookies[COOKIE] = cookie
        assert replay.post("/api/v1/auth/refresh/").status_code == 401
        assert AuditLog.objects.filter(user=user, action="logout").exists()


@pytest.mark.django_db
class TestMe:
    def test_lists_memberships_with_effective_permissions(self, school, other_school, make_member):
        user = make_member(school, "teacher")
        response = APIClient()
        response = login(response, user.email)
        memberships = response.data["user"]["memberships"]

        assert [m["school"]["code"] for m in memberships] == ["alpha"]
        perms = memberships[0]["permissions"]
        assert "grades.enter" in perms
        assert "finance.view" not in perms

    def test_user_can_update_own_language(self, school, make_member, client_for):
        user = make_member(school, "teacher")
        response = client_for(user).patch("/api/v1/me/", {"language": "en"}, format="json")
        assert response.status_code == 200
        assert response.data["language"] == "en"


@pytest.mark.django_db
class TestPasswords:
    def test_change_password_signs_out_other_sessions(self, school, make_member):
        user = make_member(school, "teacher")
        other_device = APIClient()
        login(other_device, user.email)

        client = APIClient()
        access = login(client, user.email).data["access"]
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = client.post(
            "/api/v1/auth/password/change/",
            {"current_password": PASSWORD, "new_password": "A-brand-new-Passw0rd"},
            format="json",
        )
        assert response.status_code == 200
        assert other_device.post("/api/v1/auth/refresh/").status_code == 401
        assert login(APIClient(), user.email, "A-brand-new-Passw0rd").status_code == 200

    def test_change_password_requires_current_password(self, school, make_member, client_for):
        user = make_member(school, "teacher")
        response = client_for(user).post(
            "/api/v1/auth/password/change/",
            {"current_password": "nope", "new_password": "A-brand-new-Passw0rd"},
            format="json",
        )
        assert response.status_code == 400
        assert "current_password" in response.data["fields"]

    def test_weak_new_password_is_rejected(self, school, make_member, client_for):
        user = make_member(school, "teacher")
        response = client_for(user).post(
            "/api/v1/auth/password/change/",
            {"current_password": PASSWORD, "new_password": "12345"},
            format="json",
        )
        assert response.status_code == 400
        assert "new_password" in response.data["fields"]

    def test_reset_flow_by_email(self, school, make_member, django_capture_on_commit_callbacks):
        user = make_member(school, "teacher")
        with django_capture_on_commit_callbacks(execute=True):
            response = APIClient().post("/api/v1/auth/password/reset/", {"email": user.email}, format="json")
        assert response.status_code == 204
        assert len(mail.outbox) == 1

        uid, token = re.search(r"uid=([\w-]+)&token=([\w-]+)", mail.outbox[0].body).groups()
        response = APIClient().post(
            "/api/v1/auth/password/reset/confirm/",
            {"uid": uid, "token": token, "new_password": "Reset-Passw0rd-2026"},
            format="json",
        )
        assert response.status_code == 204
        assert login(APIClient(), user.email, "Reset-Passw0rd-2026").status_code == 200

        # The link only works once.
        response = APIClient().post(
            "/api/v1/auth/password/reset/confirm/",
            {"uid": uid, "token": token, "new_password": "Another-Passw0rd-2026"},
            format="json",
        )
        assert response.status_code == 400

    def test_reset_request_does_not_reveal_unknown_accounts(self, db, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            response = APIClient().post(
                "/api/v1/auth/password/reset/", {"email": "nobody@test.local"}, format="json"
            )
        assert response.status_code == 204
        assert len(mail.outbox) == 0
