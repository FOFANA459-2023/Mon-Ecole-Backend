"""Grades as teachers really give them.

Schools here have no common grading rules: each teacher decides how many quizzes, tests, assignments and
exams to give, what to call them and how much each counts. So the rules live in the teacher's gradebook
(one subject, one class, one term): the teacher names their own categories ("Interrogations", "Quiz",
"1st Period Test"...), weighs them, and adds as many assessments as they like, each marked out of any number.

The school only sets how marks are reported (out of 20, 10 or 100, the pass mark, rounding, ties) so that
subjects can be added up into an overall average and a rank.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel

SCORE_DIGITS = 7
SCORE_PLACES = 2


class GradingScale(TenantScopedModel):
    """How marks are reported: for the whole school (no level) or for one level that differs from it."""

    class RankMethod(models.TextChoices):
        COMPETITION = "competition", _("Ties share a rank and the next rank is skipped (1, 1, 3)")
        DENSE = "dense", _("Ties share a rank and no rank is skipped (1, 1, 2)")

    level = models.OneToOneField(
        "academics.Level",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="grading_scale",
        help_text="Empty = the school's default scale.",
    )
    max_mark = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("20"),
        validators=[MinValueValidator(Decimal("1"))],
        help_text="Marks are reported out of this number, e.g. 20, 10 or 100.",
    )
    pass_mark = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10"))
    decimals = models.PositiveSmallIntegerField(default=2, validators=[MaxValueValidator(3)])
    rank_method = models.CharField(max_length=12, choices=RankMethod.choices, default=RankMethod.COMPETITION)

    class Meta:
        ordering = ["level__order", "id"]
        verbose_name = "grading scale"
        constraints = [
            models.UniqueConstraint(
                fields=["school"], condition=Q(level__isnull=True), name="uniq_school_default_scale"
            ),
            models.CheckConstraint(condition=Q(pass_mark__lte=F("max_mark")), name="scale_pass_within_max"),
            models.CheckConstraint(condition=Q(pass_mark__gte=0), name="scale_pass_not_negative"),
        ]

    def __str__(self):
        return f"/{self.max_mark} ({self.level or 'school'})"


class Gradebook(TenantScopedModel):
    """One subject of one class for one term: the teacher's grading rules, assessments and marks.

    In progress → submitted (by the teacher) → published (by the school). Published marks are locked until
    someone allowed to reopens them, with a reason.
    """

    class Status(models.TextChoices):
        OPEN = "open", _("In progress")
        SUBMITTED = "submitted", _("Submitted")
        PUBLISHED = "published", _("Published")

    class MissingPolicy(models.TextChoices):
        EXCLUDE = "exclude", _("Leave missing marks out of the average")
        ZERO = "zero", _("Count missing marks as zero")

    class_subject = models.ForeignKey(
        "academics.ClassSubject", on_delete=models.PROTECT, related_name="gradebooks"
    )
    term = models.ForeignKey("academics.Term", on_delete=models.PROTECT, related_name="gradebooks")
    missing_policy = models.CharField(
        max_length=10, choices=MissingPolicy.choices, default=MissingPolicy.EXCLUDE
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status_note = models.CharField(
        max_length=255, blank=True, help_text="Why the marks were sent back or reopened (latest)."
    )

    class Meta:
        ordering = ["term__order", "class_subject__class_group__level__order", "class_subject__subject__name"]
        verbose_name = "gradebook"
        constraints = [
            models.UniqueConstraint(fields=["class_subject", "term"], name="uniq_gradebook_subject_term"),
        ]
        indexes = [models.Index(fields=["school", "term", "status"], name="gradebook_school_term_idx")]

    def __str__(self):
        return f"{self.class_subject} — {self.term.name}"


class GradeCategory(TenantScopedModel):
    """A group of assessments the teacher names and weighs: "Interrogations" ×1, "Composition" ×2...

    Within a category, marks are combined either as the (weighted) average of each assessment's percentage,
    or as the total of points earned over the total possible.
    """

    class Method(models.TextChoices):
        AVERAGE = "average", _("Average of the assessments")
        TOTAL = "total", _("Total of the points")

    gradebook = models.ForeignKey(Gradebook, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=60)
    weight = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("1"), validators=[MinValueValidator(Decimal("0.01"))]
    )
    method = models.CharField(max_length=10, choices=Method.choices, default=Method.AVERAGE)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "grade category"
        verbose_name_plural = "grade categories"
        constraints = [models.UniqueConstraint(fields=["gradebook", "name"], name="uniq_category_name")]

    def __str__(self):
        return self.name


class Assessment(TenantScopedModel):
    """Anything the teacher marks — a quiz, a homework, a test, an exam — named however they like."""

    gradebook = models.ForeignKey(Gradebook, on_delete=models.CASCADE, related_name="assessments")
    category = models.ForeignKey(GradeCategory, on_delete=models.PROTECT, related_name="assessments")
    name = models.CharField(max_length=100)
    date = models.DateField(null=True, blank=True)
    max_score = models.DecimalField(
        max_digits=SCORE_DIGITS,
        decimal_places=SCORE_PLACES,
        default=Decimal("20"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Marked out of this number.",
    )
    weight = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="How much it counts inside its category (average method only).",
    )

    class Meta:
        ordering = [F("date").asc(nulls_last=True), "id"]
        verbose_name = "assessment"
        constraints = [models.CheckConstraint(condition=Q(max_score__gt=0), name="assessment_max_positive")]

    def __str__(self):
        return self.name


class Grade(TenantScopedModel):
    """One student's mark on one assessment. No score = not marked yet; excused = left out for them."""

    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="grades")
    enrollment = models.ForeignKey("enrollments.Enrollment", on_delete=models.PROTECT, related_name="grades")
    score = models.DecimalField(
        max_digits=SCORE_DIGITS,
        decimal_places=SCORE_PLACES,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    excused = models.BooleanField(default=False)
    comment = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["assessment", "enrollment"]
        verbose_name = "grade"
        constraints = [
            models.UniqueConstraint(fields=["assessment", "enrollment"], name="uniq_grade_per_assessment"),
            models.CheckConstraint(
                condition=Q(excused=False) | Q(score__isnull=True), name="grade_excused_has_no_score"
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0) | Q(score__isnull=True), name="grade_not_negative"
            ),
        ]

    def __str__(self):
        return f"{self.enrollment_id}: {self.score}"
