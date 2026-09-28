import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role, User
from apps.schools.services import create_school

PASSWORD = "Correct-Horse-2026"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def school(db):
    return create_school(name="École Alpha", code="alpha")


@pytest.fixture
def other_school(db):
    return create_school(name="École Beta", code="beta")


@pytest.fixture
def make_member(db):
    """Create a user with the given built-in role(s) in a school."""

    def _make(school, *role_keys, email=None, **extra):
        role_keys = role_keys or ("super_admin",)
        email = email or f"{'-'.join(role_keys)}.{school.code}@test.local"
        user = User.objects.create_user(
            username=email, email=email, password=PASSWORD, first_name="Test", last_name=role_keys[0], **extra
        )
        membership = Membership.objects.create(user=user, school=school)
        membership.roles.set(Role.objects.filter(school=school, key__in=role_keys))
        return user

    return _make


@pytest.fixture
def client_for():
    """API client authenticated as `user`, acting on `school` (X-School-ID header)."""

    def _client(user, school=None):
        client = APIClient()
        client.force_authenticate(user=user)
        if school is not None:
            client.credentials(HTTP_X_SCHOOL_ID=str(school.pk))
        return client

    return _client
