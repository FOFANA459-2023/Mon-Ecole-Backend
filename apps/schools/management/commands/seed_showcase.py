"""Create (or remove) five fictional schools with a realistic history, to try Mon École end to end.

    python manage.py seed_showcase              # create the schools that do not exist yet
    python manage.py seed_showcase --delete     # remove them and everything in them
    python manage.py seed_showcase --only essai-kankan

Outside development (DEBUG off) both need --allow-production: the data is fictional and meant to be
removed again. The schools' codes start with "essai-"; nothing else is ever touched. No login account is
created: the platform owner sees every school with Director rights.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.schools.models import School
from apps.schools.services import EraseIncomplete, erase_school

from ._showcase import SHOWCASE_PREFIX, SPECS, build_school


def delete_school(school: School) -> dict[str, int]:
    """Remove one showcase school and every row that belongs to it."""
    if not school.code.startswith(SHOWCASE_PREFIX):
        raise CommandError(f"{school.code} is not a showcase school.")
    try:
        return erase_school(school)
    except EraseIncomplete as error:
        raise CommandError(str(error)) from error


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
