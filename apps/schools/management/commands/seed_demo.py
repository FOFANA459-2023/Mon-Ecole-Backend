import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Membership, Role
from apps.schools.models import School
from apps.schools.services import create_school

from ._demo_school_data import seed_school_data

User = get_user_model()

# Local development data only. The password comes from --password or DEMO_PASSWORD.
DEFAULT_DEMO_PASSWORD = "Demo-MonEcole-2026"  # noqa: S105

SCHOOLS = [
    {
        "name": "Groupe Scolaire Horizon",
        "code": "horizon",
        "country": "GN",
        "currency": "GNF",
        "timezone": "Africa/Conakry",
        "default_language": "fr",
        "address": "Kaloum, Conakry",
    },
    {
        "name": "Monrovia Bright Future Academy",
        "code": "brightfuture",
        "country": "LR",
        "currency": "LRD",
        "timezone": "Africa/Monrovia",
        "default_language": "en",
        "address": "Sinkor, Monrovia",
    },
]

# The platform owner: not a member of any school, sees and runs every school.
OWNER = ("admin@monecole.test", "Aïssatou", "Diallo")

# (email, first name, last name, {school code: [role keys]})
USERS = [
    ("directeur@monecole.test", "Mamadou", "Camara", {"horizon": ["director"]}),
    ("principal@monecole.test", "Grace", "Tubman", {"brightfuture": ["director"]}),
    ("secretariat@monecole.test", "Fatoumata", "Bah", {"horizon": ["admin_staff"]}),
    ("comptable@monecole.test", "Ibrahima", "Sow", {"horizon": ["accountant"]}),
    ("enseignant@monecole.test", "Mariama", "Condé", {"horizon": ["teacher"]}),
    ("teacher@monecole.test", "James", "Kollie", {"brightfuture": ["teacher"]}),
]


class Command(BaseCommand):
    help = "Create demo schools and users for local development (never run in production)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD))

    @transaction.atomic
    def handle(self, *args, **opts):
        if not settings.DEBUG and not os.environ.get("ALLOW_DEMO_SEED"):
            raise CommandError("seed_demo only runs with DEBUG=True (or ALLOW_DEMO_SEED=1 for staging).")

        schools = {}
        for spec in SCHOOLS:
            school = School.objects.filter(code=spec["code"]).first()
            if school is None:
                spec = dict(spec)
                school = create_school(name=spec.pop("name"), code=spec.pop("code"), **spec)
                self.stdout.write(f"Created school {school.name}")
            schools[school.code] = school

        def demo_user(email, first, last, **extra):
            user = User.objects.filter(email=email).first()
            if user is None:
                # Demo accounts skip the invitation: already confirmed, with the demo password.
                user = User(
                    username=email.split("@")[0],
                    email=email,
                    first_name=first,
                    last_name=last,
                    email_verified_at=timezone.now(),
                    **extra,
                )
                user.set_password(opts["password"])
                user.save()
                self.stdout.write(f"Created user {email}")
            return user

        demo_user(*OWNER, is_superuser=True, is_staff=True)

        for email, first, last, access in USERS:
            user = demo_user(email, first, last)
            for code, role_keys in access.items():
                membership, _ = Membership.objects.get_or_create(user=user, school=schools[code])
                membership.roles.set(Role.objects.filter(school=schools[code], key__in=role_keys))

        for school in schools.values():
            seed_school_data(school, self.stdout)

        self.stdout.write(
            self.style.SUCCESS(
                "Demo data ready. Sign in with any @monecole.test account (admin@ is the platform owner)."
            )
        )
