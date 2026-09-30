from pathlib import Path

import pytest
from django.conf import settings
from django.core import mail
from django.core.management import CommandError, call_command

from apps.academics.models import ClassGroup
from apps.accounts.models import Membership
from apps.enrollments.models import Enrollment
from apps.people.models import Student
from apps.schools.models import School


@pytest.mark.django_db
class TestCreateSchool:
    def test_creates_the_school_roles_and_invites_the_director(self, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            call_command("create_school", name="École Test", code="ecoletest", admin_email="Dir@Test.org")
        school = School.objects.get(code="ecoletest")
        assert school.settings.idle_timeout_minutes == 30
        assert school.roles.filter(is_system=True).count() >= 5
        membership = Membership.objects.get(school=school, user__email="dir@test.org")
        assert list(membership.roles.values_list("key", flat=True)) == ["director"]
        assert mail.outbox and mail.outbox[0].to == ["dir@test.org"]

    def test_refuses_a_duplicate_code(self):
        call_command("create_school", name="A", code="dup", admin_email="a@test.org")
        with pytest.raises(CommandError):
            call_command("create_school", name="B", code="dup", admin_email="b@test.org")


@pytest.mark.django_db
class TestSeedDemo:
    def test_refuses_to_run_outside_development(self, monkeypatch):
        monkeypatch.delenv("ALLOW_DEMO_SEED", raising=False)
        with pytest.raises(CommandError):
            call_command("seed_demo")

    def test_builds_demo_schools_and_can_run_twice(self, settings):
        settings.DEBUG = True
        call_command("seed_demo", password="Demo-Test-Password-1")
        students = Student.objects.count()
        call_command("seed_demo", password="Demo-Test-Password-1")
        assert School.objects.filter(code__in=["horizon", "brightfuture"]).count() == 2
        assert Student.objects.count() == students > 0
        assert ClassGroup.objects.exists()
        # Every seeded enrolment respects the one-active-enrolment-per-year rule and class capacity.
        for klass in ClassGroup.objects.exclude(capacity=None):
            assert Enrollment.objects.filter(class_group=klass, status="active").count() <= klass.capacity


def test_permission_matrix_doc_is_up_to_date(tmp_path):
    out = tmp_path / "matrix.md"
    call_command("permission_matrix", output=str(out))
    committed = Path(settings.BASE_DIR, "docs", "permission-matrix.md").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == committed, (
        "Run: python manage.py permission_matrix --output docs/permission-matrix.md"
    )
