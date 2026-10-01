"""Create (or remove) five fictional schools with a realistic history, to try Mon École end to end.

    python manage.py seed_showcase              # create the schools that do not exist yet
    python manage.py seed_showcase --delete     # remove them and everything in them
    python manage.py seed_showcase --only essai-kankan

Outside development (DEBUG off) both need --allow-production: the data is fictional and meant to be
removed again. The schools' codes start with "essai-"; nothing else is ever touched. No login account is
created: the platform owner sees every school with Director rights.
"""

import time

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError

from apps.schools.models import School

from ._showcase import SHOWCASE_PREFIX, SPECS, build_school


def _school_models():
    """(model, lookup) for every table that belongs to a school: directly (a `school` foreign key), or
    through a parent that does (payment allocations, invoice lines...)."""
    direct = []
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model is School:
                direct.append((model, field.name))
    tenant_models = {model for model, _ in direct}
    children = []
    for model in apps.get_models():
        if model in tenant_models or model is School:
            continue
        for field in model._meta.concrete_fields:
            if field.is_relation and field.many_to_one and field.related_model in tenant_models:
                lookup = next(name for m, name in direct if m is field.related_model)
                children.append((model, f"{field.name}__{lookup}"))
    return children + direct


@transaction.atomic
def delete_school(school: School) -> dict[str, int]:
    """Remove one showcase school and every row that belongs to it.

    Tenant tables protect each other (payments protect invoices, grades protect enrolments...), so rows are
    deleted in passes until nothing is left; each pass removes whatever no longer has a protector.
    """
    if not school.code.startswith(SHOWCASE_PREFIX):
        raise CommandError(f"{school.code} is not a showcase school.")
    removed: dict[str, int] = {}
    models = _school_models()
    for _ in range(40):
        remaining = 0
        for model, field in models:
            rows = model._base_manager.filter(**{field: school})
            if not rows.exists():
                continue
            try:
                with transaction.atomic():
                    _, per_model = rows.delete()
                for label, count in per_model.items():
                    removed[label] = removed.get(label, 0) + count
            except (ProtectedError, RestrictedError):
                remaining += 1
        if remaining == 0:
            break
    else:
        raise CommandError(f"Could not remove everything from {school.code}.")
    school.delete()
    return removed


class Command(BaseCommand):
    help = "Create or remove five fictional schools with a realistic history (codes starting with 'essai-')."

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Remove the showcase schools instead.")
        parser.add_argument("--only", nargs="*", default=None, help="Only these school codes.")
        parser.add_argument(
            "--allow-production", action="store_true", help="Required when DEBUG is off (fictional data)."
        )

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["allow_production"]:
            raise CommandError("Outside development, add --allow-production (this data is fictional).")
        specs = [s for s in SPECS if not opts["only"] or s.code in opts["only"]]
        if opts["delete"]:
            schools = School.objects.filter(code__startswith=SHOWCASE_PREFIX)
            if opts["only"]:
                schools = schools.filter(code__in=opts["only"])
            for school in schools:
                started = time.monotonic()
                removed = sum(delete_school(school).values())
                seconds = time.monotonic() - started
                self.stdout.write(f"Removed {school.name} ({removed} rows, {seconds:.0f} s)")
            self.stdout.write(self.style.SUCCESS("Showcase schools removed."))
            return
        for spec in specs:
            if School.objects.filter(code=spec.code).exists():
                self.stdout.write(f"{spec.name} already exists: skipped")
                continue
            started = time.monotonic()
            counts = build_school(spec, self.stdout)
            summary = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
            self.stdout.write(
                f"Created {spec.name} in {time.monotonic() - started:.0f} s"
                + (f" — {summary}" if summary else "")
            )
        self.stdout.write(self.style.SUCCESS("Showcase schools ready. Open them from the platform area."))
