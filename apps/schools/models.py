from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


def school_logo_path(instance, filename):
    return f"schools/{instance.pk or 'new'}/branding/{filename}"


class School(TimeStampedModel):
    """The tenant: every school-owned record points back to one School."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")

    class Language(models.TextChoices):
        FRENCH = "fr", _("French")
        ENGLISH = "en", _("English")

    name = models.CharField(max_length=200)
    code = models.SlugField(max_length=30, unique=True)
    registration_number = models.CharField(max_length=60, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    logo = models.FileField(upload_to=school_logo_path, blank=True)
    country = models.CharField(max_length=2, default="GN")
    timezone = models.CharField(max_length=50, default="Africa/Conakry")
    currency = models.CharField(max_length=3, default="GNF")
    default_language = models.CharField(max_length=2, choices=Language.choices, default=Language.FRENCH)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SchoolSettings(TimeStampedModel):
    school = models.OneToOneField(School, on_delete=models.CASCADE, related_name="settings")
    idle_timeout_minutes = models.PositiveSmallIntegerField(default=30)
    student_number_prefix = models.CharField(max_length=10, default="STU")
    employee_number_prefix = models.CharField(max_length=10, default="EMP")
    invoice_prefix = models.CharField(max_length=10, default="INV")
    receipt_prefix = models.CharField(max_length=10, default="REC")
    ai_enabled = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "school settings"

    def __str__(self):
        return f"Settings for {self.school}"
