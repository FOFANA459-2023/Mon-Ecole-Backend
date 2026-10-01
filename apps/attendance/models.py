"""Daily registers: one per class per school day, taken from a phone by the class's teacher, plus a daily
register of staff presence."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class Status(models.TextChoices):
    PRESENT = "present", _("Present")
    ABSENT = "absent", _("Absent")
    LATE = "late", _("Late")
    EXCUSED = "excused", _("Excused absence")


class ClassRegister(TenantScopedModel):
    """The register of one class on one day. `created_by` took it; later changes are in the audit log."""

    class_group = models.ForeignKey(
        "academics.ClassGroup", on_delete=models.PROTECT, related_name="registers"
    )
    date = models.DateField()
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-date", "class_group__level__order", "class_group__name"]
        verbose_name = "class register"
        constraints = [
            models.UniqueConstraint(fields=["class_group", "date"], name="uniq_register_class_date")
        ]
        indexes = [models.Index(fields=["school", "date"], name="register_school_date_idx")]

    def __str__(self):
        return f"{self.class_group} — {self.date}"


class AttendanceRecord(TenantScopedModel):
    """One student on one register."""

    register = models.ForeignKey(ClassRegister, on_delete=models.CASCADE, related_name="records")
    enrollment = models.ForeignKey(
        "enrollments.Enrollment", on_delete=models.PROTECT, related_name="attendance_records"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PRESENT)
    minutes_late = models.PositiveSmallIntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["register", "enrollment"]
        verbose_name = "attendance record"
        constraints = [
            models.UniqueConstraint(fields=["register", "enrollment"], name="uniq_record_per_register"),
            models.CheckConstraint(
                condition=Q(status="late") | Q(minutes_late__isnull=True),
                name="record_minutes_only_when_late",
            ),
        ]
        indexes = [models.Index(fields=["enrollment", "status"], name="record_enrollment_status_idx")]

    def __str__(self):
        return f"{self.enrollment_id}: {self.status}"


class StaffAttendance(TenantScopedModel):
    """One staff member on one day."""

    class StaffStatus(models.TextChoices):
        PRESENT = "present", _("Present")
        ABSENT = "absent", _("Absent")
        LATE = "late", _("Late")
        EXCUSED = "excused", _("Excused absence")
        LEAVE = "leave", _("On leave")

    staff = models.ForeignKey("people.StaffMember", on_delete=models.PROTECT, related_name="attendance")
    date = models.DateField()
    status = models.CharField(max_length=10, choices=StaffStatus.choices, default=StaffStatus.PRESENT)
    minutes_late = models.PositiveSmallIntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-date", "staff__last_name", "staff__first_name"]
        verbose_name = "staff attendance"
        verbose_name_plural = "staff attendance"
        constraints = [
            models.UniqueConstraint(fields=["staff", "date"], name="uniq_staff_attendance_day"),
            models.CheckConstraint(
                condition=Q(status="late") | Q(minutes_late__isnull=True), name="staff_minutes_only_when_late"
            ),
        ]
        indexes = [models.Index(fields=["school", "date"], name="staff_att_school_date_idx")]

    def __str__(self):
        return f"{self.staff} — {self.date}: {self.status}"
