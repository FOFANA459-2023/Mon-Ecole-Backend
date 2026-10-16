"""Administration (ToR §16): school departments, announcements and the school calendar.

Official documents are school-owned `documents.Document` rows; administrative reports live in apps.reports.
"""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class Department(TenantScopedModel):
    """A group of staff (Sciences, Languages, Administration...), optionally with a head."""

    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    head = models.ForeignKey(
        "people.StaffMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="headed_departments",
    )

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="uniq_department_name")]

    def __str__(self):
        return self.name


class Announcement(TenantScopedModel):
    """A message from the school to its staff: everyone, or only some roles. Shown from `publish_at`
    until `expires_on` (included); pinned ones stay on top."""

    class Priority(models.TextChoices):
        NORMAL = "normal", _("Normal")
        IMPORTANT = "important", _("Important")

    title = models.CharField(max_length=150)
    body = models.TextField(max_length=5000)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    roles = models.ManyToManyField(
        "accounts.Role", blank=True, related_name="announcements", help_text="Empty = everyone in the school."
    )
    publish_at = models.DateTimeField(default=timezone.now)
    expires_on = models.DateField(null=True, blank=True)
    pinned = models.BooleanField(default=False)
    # When its audience was notified (once, when the publication time has come).
    notified_at = models.DateTimeField(null=True, blank=True, editable=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-pinned", "-publish_at", "-id"]
        indexes = [models.Index(fields=["school", "publish_at"], name="announcement_publish_idx")]

    def __str__(self):
        return self.title


class CalendarEvent(TenantScopedModel):
    """A date in the school year: exams, holidays, meetings, events, deadlines. For the whole school or
    some levels only."""

    class Kind(models.TextChoices):
        EXAM = "exam", _("Exam")
        HOLIDAY = "holiday", _("Holiday")
        MEETING = "meeting", _("Meeting")
        EVENT = "event", _("School event")
        DEADLINE = "deadline", _("Deadline")

    title = models.CharField(max_length=150)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.EVENT)
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    location = models.CharField(max_length=150, blank=True)
    description = models.TextField(max_length=2000, blank=True)
    levels = models.ManyToManyField(
        "academics.Level", blank=True, related_name="calendar_events", help_text="Empty = the whole school."
    )
    # When the teachers were reminded of this exam (a week before).
    reminded_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["start_date", F("start_time").asc(nulls_first=True), "id"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__gte=F("start_date")), name="event_ends_after_start")
        ]
        indexes = [models.Index(fields=["school", "start_date"], name="event_school_start_idx")]

    def __str__(self):
        return f"{self.title} ({self.start_date})"
