from django.conf import settings
from django.db import models


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
