from django.conf import settings
from django.db import models

from .files import SchoolUploadPath


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TenantQuerySet(models.QuerySet):
    def for_school(self, school):
        return self.filter(school=school)


class TenantScopedModel(TimeStampedModel):
    """Base for every record that belongs to one school. The school is always set server-side."""

    school = models.ForeignKey("schools.School", on_delete=models.PROTECT, related_name="+")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True


class Job(TenantScopedModel):
    """Work too long for one request (report cards for a whole school...), run by Celery.

    The screen polls the job for its progress and downloads the result file when it is done. Only the user
    who started a job sees it; result files are deleted after a few days (the job row stays as a record).
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    params = models.JSONField(default=dict, blank=True)
    total = models.PositiveIntegerField(default=0)
    done = models.PositiveIntegerField(default=0)
    result = models.FileField(upload_to=SchoolUploadPath("jobs"), blank=True)
    result_name = models.CharField(max_length=200, blank=True)
    # What was done, as counts the screen words in the user's language ({"cards": 34, "new": 3}).
    outcome = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["school", "created_by", "-created_at"], name="job_owner_idx")]

    def __str__(self):
        return f"{self.kind} #{self.pk} ({self.status})"


class Sequence(models.Model):
    """Per-school, per-year counters for human-readable numbers (students, invoices, receipts...)."""

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="sequences")
    key = models.CharField(max_length=40)
    year = models.PositiveIntegerField()
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["school", "key", "year"], name="uniq_sequence_school_key_year")
        ]

    def __str__(self):
        return f"{self.school_id}:{self.key}:{self.year}={self.last_value}"
