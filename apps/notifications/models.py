"""Notifications (ToR §21): in the app for staff, by email when they ask for it, and — when the school turns
it on — emails to parents about payments and absences.

Every email leaves a trace (`OutboundEmail`): what was sent to whom, and a key so the same reminder is never
sent twice. The month's count is checked against the EmailJS plan's limit.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Kind(models.TextChoices):
    ANNOUNCEMENT = "announcement", _("Announcements")
    ANNOUNCEMENT_IMPORTANT = "announcement_important", _("Important announcements")
    GRADES_SUBMITTED = "grades_submitted", _("Marks submitted for review")
    GRADES_SENT_BACK = "grades_sent_back", _("Marks sent back or reopened")
    GRADES_PUBLISHED = "grades_published", _("Marks published")
    ABSENCE_ALERT = "absence_alert", _("Students absent too often")
    PAYMENTS_OVERDUE = "payments_overdue", _("Payments overdue")
    EXAM_SOON = "exam_soon", _("Upcoming exams")


class Notification(TimeStampedModel):
    """One message in a user's notification list, in their language, for one school."""

    school = models.ForeignKey("schools.School", on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=500, blank=True)
    link = models.CharField(
        max_length=200, blank=True, help_text="A page of the web app, e.g. /assessments/12."
    )
    # The same event never notifies someone twice (e.g. "absences:2026-10-14").
    key = models.CharField(max_length=120, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "user", "key"], condition=~Q(key=""), name="uniq_notification_key"
            )
        ]
        indexes = [models.Index(fields=["user", "school", "read_at"], name="notification_unread_idx")]

    def __str__(self):
        return f"{self.user_id}: {self.title}"


class NotificationPreference(TimeStampedModel):
    """What a user wants for one kind of notification (no row = the kind's default)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preferences"
    )
    kind = models.CharField(max_length=30, choices=Kind.choices)
    in_app = models.BooleanField(default=True)
    email = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "kind"], name="uniq_notification_pref")]

    def __str__(self):
        return f"{self.user_id}: {self.kind}"


class OutboundEmail(TimeStampedModel):
    """An email the platform sent (or tried to): to staff who asked for it, or to a parent."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped (monthly limit reached)"

    class Audience(models.TextChoices):
        STAFF = "staff", "Staff"
        PARENT = "parent", "Parent"

    school = models.ForeignKey("schools.School", null=True, on_delete=models.CASCADE, related_name="+")
    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.STAFF)
    kind = models.CharField(max_length=40)
    to = models.EmailField()
    subject = models.CharField(max_length=200)
    message = models.TextField()
    key = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    error = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "key"], condition=~Q(key=""), name="uniq_outbound_email_key"
            )
        ]
        indexes = [models.Index(fields=["status", "created_at"], name="outbound_email_status_idx")]

    def __str__(self):
        return f"{self.to}: {self.subject}"
