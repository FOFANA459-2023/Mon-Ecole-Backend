from datetime import date
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.utils import timezone
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
        role_keys = role_keys or ("director",)
        email = email or f"{'-'.join(role_keys)}.{school.code}@test.local"
        extra.setdefault("email_verified_at", timezone.now())
        user = User.objects.create_user(
            username=email, email=email, password=PASSWORD, first_name="Test", last_name=role_keys[0], **extra
        )
        membership = Membership.objects.create(user=user, school=school)
        membership.roles.set(Role.objects.filter(school=school, key__in=role_keys))
        return user

    return _make


@pytest.fixture
def owner(db):
    """The platform owner: a superuser, member of no school."""
    return User.objects.create_user(
        username="owner@test.local",
        email="owner@test.local",
        password=PASSWORD,
        first_name="Platform",
        last_name="Owner",
        is_superuser=True,
        is_staff=True,
        email_verified_at=timezone.now(),
    )


@pytest.fixture
def year(school):
    from apps.academics.services import create_academic_year

    return create_academic_year(
        school, name="2026-2027", start_date=date(2026, 9, 1), end_date=date(2027, 6, 30), term_count=3
    )


@pytest.fixture
def level(school):
    from apps.academics.models import Level

    return Level.objects.create(school=school, name="7ème année", order=7)


@pytest.fixture
def make_class(school, year, level):
    from apps.academics.models import ClassGroup

    def _make(name="7ème A", *, capacity=None, academic_year=None, class_level=None, **extra):
        return ClassGroup.objects.create(
            school=school,
            academic_year=academic_year or year,
            level=class_level or level,
            name=name,
            capacity=capacity,
            **extra,
        )

    return _make


@pytest.fixture
def make_student(school):
    from apps.people.services import create_student

    def _make(first_name="Awa", last_name="Diallo", *, target_school=None, **extra):
        return create_student(
            target_school or school,
            data={"first_name": first_name, "last_name": last_name, "gender": "F", **extra},
        )

    return _make


@pytest.fixture
def make_staff(school):
    from apps.people.services import create_staff

    def _make(first_name="Mamadou", last_name="Barry", *, user=None, **extra):
        staff = create_staff(school, data={"first_name": first_name, "last_name": last_name, **extra})
        if user is not None:
            staff.user = user
            staff.save()
        return staff

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


@pytest.fixture
def tuition(school):
    from apps.finance.models import FeeCategory

    return FeeCategory.objects.create(school=school, name="Scolarité", kind="tuition")


@pytest.fixture
def registration(school):
    from apps.finance.models import FeeCategory

    return FeeCategory.objects.create(school=school, name="Inscription", kind="registration")


@pytest.fixture
def fees(school, year, level, tuition, registration):
    """Tuition 3 000 000 GNF in three installments, plus 250 000 GNF registration for new students."""
    from apps.finance.models import FeeSchedule

    FeeSchedule.objects.create(
        school=school,
        academic_year=year,
        level=level,
        category=tuition,
        amount=Decimal("3000000"),
        installments=[
            {"label": "", "due_date": "2026-10-01", "amount": "1000000"},
            {"label": "", "due_date": "2027-01-10", "amount": "1000000"},
            {"label": "", "due_date": "2027-04-01", "amount": "1000000"},
        ],
    )
    FeeSchedule.objects.create(
        school=school,
        academic_year=year,
        level=level,
        category=registration,
        applies_to=FeeSchedule.AppliesTo.NEW,
        amount=Decimal("250000"),
        installments=[{"label": "", "due_date": "2026-09-01", "amount": "250000"}],
    )


@pytest.fixture
def cash_session(school):
    """The school's main register, open since 1 September 2026 with an empty float."""
    from apps.cashregister import services as cash

    session = cash.open_session(cash.default_register(school), opening_balance=Decimal("0"))
    session.opened_at = timezone.make_aware(timezone.datetime(2026, 9, 1, 7, 30))
    session.save(update_fields=["opened_at"])
    return session
