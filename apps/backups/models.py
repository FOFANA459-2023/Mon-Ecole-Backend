"""Backups of the whole database (ToR §26): automatic every few hours, or on demand by the platform owner.

A backup holds every school's data, so only the platform owner lists and downloads them; directors only
see when the last one succeeded.
"""

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Backup(TimeStampedModel):
    class Kind(models.TextChoices):
        AUTOMATIC = "automatic", "Automatic"
        MANUAL = "manual", "Manual"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired (file deleted)"

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.AUTOMATIC)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    # Not a school file: under backups/ in the private bucket, never served by URL.
    file = models.FileField(upload_to="backups/", blank=True)
    size = models.PositiveBigIntegerField(default=0)
    engine = models.CharField(max_length=20, blank=True, help_text="postgresql or sqlite")
    error = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"{self.kind} backup #{self.pk} ({self.status})"
