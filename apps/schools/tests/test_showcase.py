from dataclasses import replace

import pytest
from django.core.management import call_command

from apps.assessments.models import Grade, Gradebook
from apps.attendance.models import AttendanceRecord
from apps.enrollments.models import Enrollment
from apps.finance.models import Payment, Refund
from apps.people.models import Student
from apps.schools.management.commands import seed_showcase
from apps.schools.models import School

pytestmark = pytest.mark.django_db


@pytest.fixture
def small(monkeypatch):
    """The showcase schools with small classes, so the test stays quick."""
    specs = [replace(spec, per_class=min(spec.per_class, 8)) for spec in seed_showcase.SPECS]
    monkeypatch.setattr(seed_showcase, "SPECS", specs)


def test_showcase_schools_are_created_with_their_history_and_removed_cleanly(small, school, make_student):
    kept = make_student()  # a real school's student must survive the clean-up
    call_command("seed_showcase", only=["essai-paynesville", "essai-labe"], allow_production=True)
    hope = School.objects.get(code="essai-paynesville")
    assert hope.currency == "LRD" and hope.default_language == "en"
    assert Enrollment.objects.filter(school=hope, status="completed").exists()  # last year's class moved up
    assert Enrollment.objects.filter(school=hope, status="active").count() > 40
    assert Gradebook.objects.filter(school=hope, status="published").exists()
    assert Grade.objects.filter(school=hope, excused=True).exists()
    assert AttendanceRecord.objects.filter(school=hope, status="absent").exists()
    assert Payment.objects.filter(school=hope, status="reversed").exists()
    assert Refund.objects.filter(school=hope).exists()
    assert Student.objects.filter(school=hope, status="archived").count() == 1
    assert Enrollment.objects.filter(school__code="essai-labe").count() == 6

    # Running it again leaves the existing schools alone.
    call_command("seed_showcase", only=["essai-labe"], allow_production=True)
    assert Enrollment.objects.filter(school__code="essai-labe").count() == 6

    call_command("seed_showcase", delete=True, allow_production=True)
    assert not School.objects.filter(code__startswith="essai-").exists()
    assert Student.objects.filter(pk=kept.pk).exists()


def test_outside_development_an_explicit_flag_is_needed():
    with pytest.raises(Exception, match="allow-production"):
        call_command("seed_showcase")


def test_only_showcase_schools_can_be_deleted(school):
    with pytest.raises(Exception, match="not a showcase school"):
        seed_showcase.delete_school(school)
