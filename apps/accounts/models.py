from django.contrib.auth.models import AbstractUser, UserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

from .permissions_registry import ALL_CODES, SUPER_ADMIN


class User(AbstractUser):
    class Language(models.TextChoices):
        FRENCH = "fr", _("French")
        ENGLISH = "en", _("English")

    email = models.EmailField(_("email address"))
    phone = models.CharField(max_length=30, blank=True)
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.FRENCH)
    must_change_password = models.BooleanField(default=False)

    REQUIRED_FIELDS = ["email"]

    objects = UserManager()

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="uniq_user_email_ci")]

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return self.get_full_name() or self.email


class Role(TimeStampedModel):
    """A named set of permission codes, defined per school."""

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="roles")
    key = models.SlugField(max_length=40, blank=True, help_text="Set for built-in roles.")
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    is_system = models.BooleanField(default=False)
    permissions = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="uniq_role_school_name"),
            models.UniqueConstraint(
                fields=["school", "key"], condition=~models.Q(key=""), name="uniq_role_school_key"
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_super_admin(self) -> bool:
        return self.key == SUPER_ADMIN

    def clean(self):
        unknown = set(self.permissions) - ALL_CODES
        if unknown:
            raise ValidationError({"permissions": f"Unknown permission codes: {', '.join(sorted(unknown))}"})


class Membership(TimeStampedModel):
    """Links a user to a school, with the roles they hold there."""

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="memberships")
    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="memberships")
    roles = models.ManyToManyField(Role, related_name="memberships", blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["user__last_name", "user__first_name"]
        constraints = [models.UniqueConstraint(fields=["user", "school"], name="uniq_membership_user_school")]

    def __str__(self):
        return f"{self.user} @ {self.school}"

    def permission_codes(self) -> frozenset[str]:
        codes: set[str] = set()
        for role in self.roles.all():
            codes.update(ALL_CODES if role.is_super_admin else role.permissions)
        return frozenset(codes & ALL_CODES)
