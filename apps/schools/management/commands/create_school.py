from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Role
from apps.accounts.permissions_registry import SUPER_ADMIN
from apps.accounts.services import add_member
from apps.schools.models import School
from apps.schools.services import create_school


class Command(BaseCommand):
    help = "Create a school with its built-in roles and invite its first Super Administrator by email."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--code", required=True, help="Short unique identifier, e.g. 'horizon'.")
        parser.add_argument("--country", default="GN")
        parser.add_argument("--currency", default="GNF")
        parser.add_argument("--timezone", default="Africa/Conakry")
        parser.add_argument("--language", default="fr", choices=["fr", "en"])
        parser.add_argument("--admin-email", required=True)
        parser.add_argument("--admin-first-name", default="")
        parser.add_argument("--admin-last-name", default="")

    @transaction.atomic
    def handle(self, *args, **opts):
        if School.objects.filter(code=opts["code"]).exists():
            raise CommandError(f"A school with code '{opts['code']}' already exists.")
        school = create_school(
            name=opts["name"],
            code=opts["code"],
            country=opts["country"],
            currency=opts["currency"].upper(),
            timezone=opts["timezone"],
            default_language=opts["language"],
        )
        add_member(
            school,
            email=opts["admin_email"],
            first_name=opts["admin_first_name"],
            last_name=opts["admin_last_name"],
            roles=[Role.objects.get(school=school, key=SUPER_ADMIN)],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created '{school.name}' (id {school.pk}); invitation sent to {opts['admin_email']}."
            )
        )
