from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.files import SchoolUploadPath
from apps.core.models import TenantScopedModel


class Document(TenantScopedModel):
    """A file attached to a student, a staff member or the school (birth certificate, diploma…)."""

    class OwnerType(models.TextChoices):
        STUDENT = "student", _("Student")
        STAFF = "staff", _("Staff member")
        SCHOOL = "school", _("School")

    class Category(models.TextChoices):
        BIRTH_CERTIFICATE = "birth_certificate", _("Birth certificate")
        ID_PHOTO = "id_photo", _("ID photo")
        PREVIOUS_REPORT = "previous_report", _("Previous report card")
        TRANSFER_CERTIFICATE = "transfer_certificate", _("Transfer certificate")
        MEDICAL = "medical", _("Medical record")
        IDENTITY = "identity", _("Identity document")
        DIPLOMA = "diploma", _("Diploma / certificate")
        CONTRACT = "contract", _("Contract")
        OFFICIAL = "official", _("Official document")
        OTHER = "other", _("Other")

    owner_type = models.CharField(max_length=10, choices=OwnerType.choices)
    owner_id = models.PositiveBigIntegerField()
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.OTHER)
    title = models.CharField(max_length=200)
    file = models.FileField(upload_to=SchoolUploadPath("documents"))
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "document"
        indexes = [models.Index(fields=["school", "owner_type", "owner_id"], name="document_owner_idx")]

    def __str__(self):
        return self.title
