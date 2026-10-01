"""Security regression suite: sweeps every API route instead of relying on each app's own tests.

New endpoints are picked up automatically, so an endpoint that forgets authentication, permissions or
tenant scoping fails here even if nobody wrote a test for it.
"""

import importlib
import re
import sys
from datetime import timedelta

import pytest
from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.accounts.models import Membership

PUBLIC_ROUTES = {
    "api/v1/auth/login/",
    "api/v1/auth/refresh/",
    "api/v1/auth/logout/",
    "api/v1/auth/password/reset/",
    "api/v1/auth/password/reset/confirm/",
    "api/v1/auth/verify-email/",
}
# Signed-in endpoints that do not act on a school (no X-School-ID needed).
PERSONAL_ROUTES = {"api/v1/me/", "api/v1/auth/password/change/"}
# The platform owner's endpoints: every school, no X-School-ID. Nobody else may call them.
PLATFORM_PREFIX = "api/v1/platform/"
# Readable by any member of the school, whatever their roles.
ANY_MEMBER = {
    ("get", "api/v1/school/"),
    ("get", "api/v1/academic-years/"),
    ("get", "api/v1/academic-years/1/"),
    ("get", "api/v1/terms/"),
    ("get", "api/v1/terms/1/"),
    ("get", "api/v1/levels/"),
    ("get", "api/v1/levels/1/"),
    ("get", "api/v1/grading-scales/"),
    ("get", "api/v1/grading-scales/1/"),
    ("get", "api/v1/search/"),
    # Documents and imports check the owner's / kind's permission inside the view.
    ("get", "api/v1/documents/"),
    ("post", "api/v1/documents/"),
    ("get", "api/v1/documents/1/"),
    ("delete", "api/v1/documents/1/"),
    ("get", "api/v1/documents/1/download/"),
}

_PARAMS = [
    (re.compile(r"\(\?P<pk>[^)]*\)"), "1"),
    (re.compile(r"\(\?P<link_id>[^)]*\)"), "1"),
    (re.compile(r"<str:kind>"), "students"),
    (re.compile(r"<int:pk>"), "1"),
]


def _concrete(route: str) -> str:
    path = route.replace("^", "").replace("$", "")
    for pattern, value in _PARAMS:
        path = pattern.sub(value, path)
    return path


def _methods(pattern: URLPattern) -> list[str]:
    actions = getattr(pattern.callback, "actions", None)
    if actions:
        return sorted(actions)
    view_class = getattr(pattern.callback, "view_class", None)
    return sorted(m for m in ("get", "post", "patch", "delete") if hasattr(view_class, m))


def _walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), p


def api_endpoints() -> list[tuple[str, str]]:
    endpoints = []
    for route, pattern in _walk(get_resolver().url_patterns):
        path = _concrete(route)
        if path.startswith("api/v1/"):
            endpoints += [
                (method, path) for method in _methods(pattern) if method in {"get", "post", "patch", "delete"}
            ]
    return sorted(set(endpoints))


ENDPOINTS = api_endpoints()


def _call(client: APIClient, method: str, path: str):
    kwargs = {"format": "json"} if method in {"post", "patch"} else {}
    return getattr(client, method)(f"/{path}", {} if kwargs else None, **kwargs)


def test_the_sweep_sees_the_whole_api():
    assert len(ENDPOINTS) > 80
    assert ("get", "api/v1/students/") in ENDPOINTS
    assert ("post", "api/v1/enrollments/register/") in ENDPOINTS


@pytest.mark.django_db
@pytest.mark.parametrize(("method", "path"), [e for e in ENDPOINTS if e[1] not in PUBLIC_ROUTES])
def test_every_endpoint_requires_authentication(method, path):
    response = _call(APIClient(), method, path)
    assert response.status_code == 401, f"{method.upper()} /{path} answered {response.status_code}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path"), [e for e in ENDPOINTS if e[1] not in PUBLIC_ROUTES | PERSONAL_ROUTES]
)
def test_member_without_roles_is_refused(method, path, school, make_member, client_for):
    user = make_member(school, "parent")  # built-in role with no permissions
    response = _call(client_for(user, school), method, path)
    assert response.status_code < 500, f"{method.upper()} /{path} crashed"
    if (method, path) not in ANY_MEMBER:
        assert response.status_code in {403, 405}, f"{method.upper()} /{path} answered {response.status_code}"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path"), [e for e in ENDPOINTS if e[1] not in PUBLIC_ROUTES | PERSONAL_ROUTES]
)
def test_other_schools_do_not_exist(method, path, school, other_school, make_member, client_for):
    if path.startswith(PLATFORM_PREFIX):
        pytest.skip("not school-scoped: see test_platform_endpoints_are_owner_only")
    outsider = make_member(other_school, "director")
    response = _call(client_for(outsider, school), method, path)
    assert response.status_code == 404, f"{method.upper()} /{path} answered {response.status_code}"


@pytest.mark.django_db
@pytest.mark.parametrize(("method", "path"), [e for e in ENDPOINTS if e[1].startswith(PLATFORM_PREFIX)])
def test_platform_endpoints_are_owner_only(method, path, school, make_member, client_for):
    director = make_member(school, "director")  # every school permission, but not the platform owner
    response = _call(client_for(director, school), method, path)
    assert response.status_code == 403, f"{method.upper()} /{path} answered {response.status_code}"


# --- tokens --------------------------------------------------------------------------------------


@pytest.mark.django_db
class TestAccessTokens:
    def _get(self, token: str, school):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}", HTTP_X_SCHOOL_ID=str(school.pk))
        return client.get("/api/v1/school/")

    def test_valid_token_is_accepted(self, school, make_member):
        user = make_member(school, "teacher")
        assert self._get(str(AccessToken.for_user(user)), school).status_code == 200

    def test_expired_token_is_rejected(self, school, make_member):
        token = AccessToken.for_user(make_member(school, "teacher"))
        token.set_exp(lifetime=-timedelta(seconds=1))
        assert self._get(str(token), school).status_code == 401

    def test_token_signed_with_another_key_is_rejected(self, school, make_member):
        import jwt

        user = make_member(school, "teacher")
        payload = dict(AccessToken.for_user(user).payload)
        forged = jwt.encode(payload, "not-the-server-key-" * 3, algorithm="HS256")
        assert self._get(forged, school).status_code == 401

    def test_unsigned_token_is_rejected(self, school, make_member):
        import jwt

        user = make_member(school, "teacher")
        forged = jwt.encode(dict(AccessToken.for_user(user).payload), None, algorithm="none")
        assert self._get(forged, school).status_code == 401

    def test_deactivated_user_loses_access_immediately(self, school, make_member):
        user = make_member(school, "teacher")
        token = str(AccessToken.for_user(user))
        user.is_active = False
        user.save()
        assert self._get(token, school).status_code == 401

    def test_removed_membership_loses_access_immediately(self, school, make_member):
        user = make_member(school, "director")
        token = str(AccessToken.for_user(user))
        Membership.objects.filter(user=user).update(is_active=False)
        assert self._get(token, school).status_code == 404


# --- HTTP hardening ------------------------------------------------------------------------------


@pytest.mark.django_db
class TestResponses:
    def test_security_headers_on_api_responses(self, client):
        response = client.get("/api/v1/me/")
        assert response["X-Frame-Options"] == "DENY"
        assert response["X-Content-Type-Options"] == "nosniff"
        assert response["Referrer-Policy"] == "same-origin"
        assert response["Cross-Origin-Opener-Policy"] == "same-origin"

    def test_unknown_origin_gets_no_cors_grant(self, client):
        response = client.options(
            "/api/v1/auth/login/",
            HTTP_ORIGIN="https://evil.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        assert "Access-Control-Allow-Origin" not in response

    def test_frontend_origin_gets_cors_with_credentials(self, client):
        origin = settings.CORS_ALLOWED_ORIGINS[0]
        response = client.options(
            "/api/v1/auth/login/", HTTP_ORIGIN=origin, HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST"
        )
        assert response["Access-Control-Allow-Origin"] == origin
        assert response["Access-Control-Allow-Credentials"] == "true"

    def test_refresh_cookie_is_locked_down(self, school, make_member):
        from conftest import PASSWORD

        user = make_member(school, "teacher")
        response = APIClient().post(
            "/api/v1/auth/login/", {"login": user.email, "password": PASSWORD}, format="json"
        )
        cookie = response.cookies[settings.REFRESH_COOKIE_NAME]
        assert cookie["httponly"]
        assert cookie["samesite"] == "Lax"
        assert cookie["path"] == "/api/v1/auth/"
        assert settings.REFRESH_COOKIE_NAME not in response.json()["user"]

    def test_audit_log_is_read_only(self, school, make_member, client_for):
        client = client_for(make_member(school, "director"), school)
        assert client.post("/api/v1/audit-logs/", {}, format="json").status_code in {403, 405}
        assert client.patch("/api/v1/audit-logs/1/", {}, format="json").status_code in {403, 405}
        assert client.delete("/api/v1/audit-logs/1/").status_code in {403, 405}

    def test_errors_do_not_leak_internals(self, school, make_member, client_for):
        client = client_for(make_member(school, "director"), school)
        body = client.post("/api/v1/students/", {"first_name": "x" * 500}, format="json").json()
        assert set(body) == {"code", "message", "fields"}
        assert "Traceback" not in str(body)


# --- configuration ---------------------------------------------------------------------------------


def test_passwords_use_argon2_and_strong_validation():
    base = importlib.import_module("config.settings.base")  # the test settings swap in a fast hasher
    assert base.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")
    validators = {v["NAME"].rsplit(".", 1)[-1]: v for v in base.AUTH_PASSWORD_VALIDATORS}
    assert validators["MinimumLengthValidator"]["OPTIONS"]["min_length"] >= 10
    assert {"CommonPasswordValidator", "UserAttributeSimilarityValidator", "NumericPasswordValidator"} <= set(
        validators
    )


def test_login_and_password_reset_are_throttled():
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["login"] and rates["password_reset"]


def test_access_tokens_are_short_lived():
    assert settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] <= timedelta(minutes=15)
    assert settings.SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] and settings.SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"]


def test_production_settings_are_hardened(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "x" * 64)
    monkeypatch.setenv("AWS_STORAGE_BUCKET_NAME", "bucket")
    monkeypatch.delenv("DJANGO_DEBUG", raising=False)
    for name in ("config.settings.production", "config.settings.base"):
        sys.modules.pop(name, None)
    try:
        prod = importlib.import_module("config.settings.production")
    finally:
        for name in ("config.settings.production", "config.settings.base"):
            sys.modules.pop(name, None)
    assert prod.DEBUG is False
    assert prod.SECURE_SSL_REDIRECT is True
    assert prod.SECURE_HSTS_SECONDS >= 31536000
    assert prod.SESSION_COOKIE_SECURE and prod.CSRF_COOKIE_SECURE and prod.REFRESH_COOKIE_SECURE
    assert prod.X_FRAME_OPTIONS == "DENY"
    assert prod.API_DOCS_ENABLED is False
    storage = prod.STORAGES["default"]["OPTIONS"]
    assert storage["querystring_auth"] is True and storage["default_acl"] is None
    assert storage["querystring_expire"] <= 600
