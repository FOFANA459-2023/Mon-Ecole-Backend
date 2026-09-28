from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class Enrollment(TenantScopedModel):
    """A student's place in a class for one academic year. A student keeps one row per year (and per
    class change), which is what makes their academic history."""

    class Kind(models.TextChoices):
        NEW = "new", _("New student")
        RE_ENROLMENT = "re_enrolment", _("Re-enrolment")
        TRANSFER_IN = "transfer_in", _("Transfer from another school")

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        CLASS_CHANGED = "class_changed", _("Moved to another class")
        WITHDRAWN = "withdrawn", _("Left the school")
        COMPLETED = "completed", _("Year completed")
        CANCELLED = "cancelled", _("Cancelled")

    student = models.ForeignKey("people.Student", on_delete=models.PROTECT, related_name="enrollments")
    academic_year = models.ForeignKey(
        "academics.AcademicYear", on_delete=models.PROTECT, related_name="enrollments"
    )
    class_group = models.ForeignKey(
        "academics.ClassGroup", on_delete=models.PROTECT, related_name="enrollments"
    )
    enrollment_date = models.DateField()
    kind = models.CharField(max_length=15, choices=Kind.choices, default=Kind.NEW)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE)
    previous_school = models.CharField(max_length=200, blank=True)
    ended_on = models.DateField(null=True, blank=True)
    end_reason = models.CharField(max_length=255, blank=True)
    transfer_to = models.CharField(max_length=200, blank=True, help_text="School the student left for.")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-academic_year__start_date", "-enrollment_date", "-id"]
        verbose_name = "enrolment"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year"],
                condition=Q(status="active"),
                name="uniq_active_enrollment_per_year",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "academic_year", "status"], name="enrol_school_year_status_idx"),
            models.Index(fields=["class_group", "status"], name="enrol_class_status_idx"),
        ]

    def __str__(self):
        return f"{self.student} — {self.class_group} ({self.academic_year})"
