"""The invitation journey: email with a verification link and a temporary password, confirm, sign in,
choose a password."""

import re
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.accounts.services import add_member


def invite(school, email="new.teacher@test.local", role="teacher"):
    add_member(
        school,
        email=email,
        first_name="Awa",
        last_name="Touré",
        roles=[Role.objects.get(school=school, key=role)],
    )
    body = mail.outbox[-1].body
    token = re.search(r"verify-email\?token=(\S+)", body).group(1)
    password = re.search(r"Temporary password: (\S+)", body).group(1)
    return token, password


def login(email, password):
    return APIClient().post("/api/v1/auth/login/", {"login": email, "password": password}, format="json")


@pytest.mark.django_db(transaction=True)
class TestInvitationJourney:
    def test_full_journey(self, school):
        token, temporary = invite(school)

        # 1. Before confirming the email, the temporary password is refused.
        response = login("new.teacher@test.local", temporary)
        assert response.status_code == 403
        assert response.json()["code"] == "email_not_verified"

        # 2. The link confirms the address.
        response = APIClient().post("/api/v1/auth/verify-email/", {"token": token}, format="json")
        assert response.status_code == 200
        assert response.json() == {"email": "new.teacher@test.local"}

        # 3. Sign in with the temporary password: the app must then ask for a new one.
        response = login("new.teacher@test.local", temporary)
        assert response.status_code == 200
        assert response.json()["user"]["must_change_password"] is True

        # 4. New password + confirmation; the current (temporary) one is not asked again.
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.json()['access']}")
        response = client.post(
            "/api/v1/auth/password/change/", {"new_password": "Mangoes-at-Kaloum-9"}, format="json"
        )
        assert response.status_code == 200, response.json()
        assert response.json()["user"]["must_change_password"] is False

        # 5. From now on only the new password works.
        assert login("new.teacher@test.local", temporary).status_code == 401
        assert login("new.teacher@test.local", "Mangoes-at-Kaloum-9").status_code == 200

    def test_verification_link_can_be_clicked_twice(self, school):
        token, _ = invite(school)
        client = APIClient()
        assert client.post("/api/v1/auth/verify-email/", {"token": token}, format="json").status_code == 200
        assert client.post("/api/v1/auth/verify-email/", {"token": token}, format="json").status_code == 200

    def test_a_new_invitation_voids_the_old_link_and_password(self, school):
        from apps.accounts.models import Membership
        from apps.accounts.services import resend_invitation

        old_token, old_password = invite(school)
        resend_invitation(Membership.objects.get(user__email="new.teacher@test.local"))
        response = APIClient().post("/api/v1/auth/verify-email/", {"token": old_token}, format="json")
        assert response.status_code == 400
        assert login("new.teacher@test.local", old_password).status_code == 401

    def test_forged_link_is_refused(self, school):
        response = APIClient().post("/api/v1/auth/verify-email/", {"token": "abc:def:ghi"}, format="json")
        assert response.status_code == 400

    def test_temporary_password_expires_after_7_days(self, school):
        token, temporary = invite(school)
        APIClient().post("/api/v1/auth/verify-email/", {"token": token}, format="json")
        User.objects.filter(email="new.teacher@test.local").update(
            invitation_expires_at=timezone.now() - timedelta(minutes=1)
        )
        response = login("new.teacher@test.local", temporary)
        assert response.status_code == 403
        assert response.json()["code"] == "invitation_expired"

    def test_wrong_password_never_reveals_the_account_state(self, school):
        invite(school)
        response = login("new.teacher@test.local", "not-the-password")
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_credentials"

    def test_new_accounts_use_the_school_language(self, school):
        invite(school)
        assert User.objects.get(email="new.teacher@test.local").language == school.default_language

    def test_normal_password_change_still_needs_the_current_password(self, school, make_member):
        from conftest import PASSWORD

        user = make_member(school, "teacher")
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login(user.email, PASSWORD).json()['access']}")
        response = client.post(
            "/api/v1/auth/password/change/", {"new_password": "Mangoes-at-Kaloum-9"}, format="json"
        )
        assert response.status_code == 400
        assert "current_password" in response.json()["fields"]
