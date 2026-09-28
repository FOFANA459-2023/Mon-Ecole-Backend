from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel


class AcademicYear(TenantScopedModel):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CLOSED = "closed", _("Closed")

    name = models.CharField(max_length=20, help_text="e.g. 2026-2027")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "academic year"
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="uniq_year_school_name"),
            models.UniqueConstraint(
                fields=["school"], condition=Q(is_current=True), name="uniq_current_year"
            ),
            models.CheckConstraint(condition=Q(end_date__gt=F("start_date")), name="year_end_after_start"),
        ]

    def __str__(self):
        return self.name


class Term(TenantScopedModel):
    academic_year = models.ForeignKey(AcademicYear, on_delete=models.CASCADE, related_name="terms")
    name = models.CharField(max_length=50)
    order = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["academic_year", "order"]
        verbose_name = "term"
        constraints = [
            models.UniqueConstraint(fields=["academic_year", "order"], name="uniq_term_order"),
            models.CheckConstraint(condition=Q(end_date__gt=F("start_date")), name="term_end_after_start"),
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_year})"


class Level(TenantScopedModel):
    """A grade level such as "7ème année" or "Grade 10"; classes belong to a level."""

    class Cycle(models.TextChoices):
        PRESCHOOL = "preschool", _("Preschool")
        PRIMARY = "primary", _("Primary")
        LOWER_SECONDARY = "lower_secondary", _("Lower secondary")
        UPPER_SECONDARY = "upper_secondary", _("Upper secondary")
        OTHER = "other", _("Other")

    name = models.CharField(max_length=50)
    order = models.PositiveSmallIntegerField(default=0, help_text="Position in the school, lowest first.")
    cycle = models.CharField(max_length=20, choices=Cycle.choices, default=Cycle.OTHER)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "level"
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="uniq_level_school_name")]

    def __str__(self):
        return self.name


class ClassGroup(TenantScopedModel):
    """A class (e.g. "7ème A") for one academic year."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        ARCHIVED = "archived", _("Archived")

    academic_year = models.ForeignKey(AcademicYear, on_delete=models.PROTECT, related_name="classes")
    level = models.ForeignKey(Level, on_delete=models.PROTECT, related_name="classes")
    name = models.CharField(max_length=50)
    room = models.CharField(max_length=50, blank=True)
    class_teacher = models.ForeignKey(
        "people.StaffMember",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="homeroom_classes",
    )
    capacity = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        ordering = ["level__order", "name"]
        verbose_name = "class"
        verbose_name_plural = "classes"
        constraints = [models.UniqueConstraint(fields=["academic_year", "name"], name="uniq_class_year_name")]
        indexes = [
            models.Index(fields=["school", "academic_year", "level"], name="class_school_year_level_idx")
        ]

    def __str__(self):
        return self.name


class Subject(TenantScopedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20)
    level = models.ForeignKey(
        Level,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="subjects",
        help_text="Leave empty when the subject is taught at several levels.",
    )
    default_coefficient = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal("1"), validators=[MinValueValidator(Decimal("0.1"))]
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "subject"
        constraints = [models.UniqueConstraint(fields=["school", "code"], name="uniq_subject_school_code")]

    def __str__(self):
        return f"{self.name} ({self.code})"


class ClassSubject(TenantScopedModel):
    """A subject taught in a class: its coefficient there and who teaches it."""

    class_group = models.ForeignKey(ClassGroup, on_delete=models.CASCADE, related_name="class_subjects")
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="class_subjects")
    teacher = models.ForeignKey(
        "people.StaffMember", null=True, blank=True, on_delete=models.SET_NULL, related_name="class_subjects"
    )
    coefficient = models.DecimalField(
        max_digits=4, decimal_places=1, default=Decimal("1"), validators=[MinValueValidator(Decimal("0.1"))]
    )
    weekly_hours = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)

    class Meta:
        ordering = ["subject__name"]
        verbose_name = "class subject"
        constraints = [
            models.UniqueConstraint(fields=["class_group", "subject"], name="uniq_class_subject"),
        ]

    def __str__(self):
        return f"{self.subject.name} — {self.class_group.name}"
