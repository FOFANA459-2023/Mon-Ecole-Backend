from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel, TimeStampedModel

MONEY_DIGITS = 14
MONEY_PLACES = 2


class FeeCategory(TenantScopedModel):
    """A kind of fee the school charges: registration, tuition, exams, transport..."""

    class Kind(models.TextChoices):
        REGISTRATION = "registration", _("Registration")
        TUITION = "tuition", _("Tuition")
        EXAM = "exam", _("Exams")
        TRANSPORT = "transport", _("Transport")
        CANTEEN = "canteen", _("Canteen")
        UNIFORM = "uniform", _("Uniform")
        SUPPLIES = "supplies", _("Books and supplies")
        OTHER = "other", _("Other")

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    description = models.CharField(max_length=255, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "fee category"
        verbose_name_plural = "fee categories"
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="uniq_fee_category_name")]

    def __str__(self):
        return self.name


class FeeSchedule(TenantScopedModel):
    """What one level pays for one fee category in one academic year, and when it falls due.

    `installments` is a list of {"label", "due_date", "amount"} whose amounts add up to `amount`.
    Changing a schedule never changes invoices already issued.
    """

    class AppliesTo(models.TextChoices):
        ALL = "all", _("All students")
        NEW = "new", _("New students only")
        RETURNING = "returning", _("Returning students only")

    academic_year = models.ForeignKey(
        "academics.AcademicYear", on_delete=models.PROTECT, related_name="fee_schedules"
    )
    level = models.ForeignKey("academics.Level", on_delete=models.PROTECT, related_name="fee_schedules")
    category = models.ForeignKey(FeeCategory, on_delete=models.PROTECT, related_name="schedules")
    applies_to = models.CharField(max_length=10, choices=AppliesTo.choices, default=AppliesTo.ALL)
    amount = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, validators=[MinValueValidator(Decimal("0.01"))]
    )
    installments = models.JSONField(default=list)

    class Meta:
        ordering = ["academic_year", "level__order", "category__order", "category__name"]
        verbose_name = "fee schedule"
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "level", "category", "applies_to"], name="uniq_fee_schedule"
            )
        ]

    def __str__(self):
        return f"{self.category} — {self.level} ({self.academic_year})"


class StudentDiscount(TenantScopedModel):
    """A reduction a student gets for one academic year: on one fee category, or (percentage only) on all."""

    class Kind(models.TextChoices):
        PERCENT = "percent", _("Percentage")
        FIXED = "fixed", _("Fixed amount")

    class Reason(models.TextChoices):
        SCHOLARSHIP = "scholarship", _("Scholarship")
        SIBLING = "sibling", _("Sibling")
        STAFF_CHILD = "staff_child", _("Staff child")
        HARDSHIP = "hardship", _("Financial hardship")
        OTHER = "other", _("Other")

    student = models.ForeignKey("people.Student", on_delete=models.PROTECT, related_name="discounts")
    academic_year = models.ForeignKey(
        "academics.AcademicYear", on_delete=models.PROTECT, related_name="discounts"
    )
    category = models.ForeignKey(
        FeeCategory,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="discounts",
        help_text="Empty = every fee category (percentage discounts only).",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PERCENT)
    value = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, validators=[MinValueValidator(Decimal("0.01"))]
    )
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.OTHER)
    note = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-academic_year__start_date", "student__last_name", "id"]
        verbose_name = "student discount"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "academic_year", "category"],
                condition=Q(is_active=True, category__isnull=False),
                name="uniq_active_discount_per_category",
            ),
            models.UniqueConstraint(
                fields=["student", "academic_year"],
                condition=Q(is_active=True, category__isnull=True),
                name="uniq_active_discount_all_categories",
            ),
            models.CheckConstraint(
                condition=Q(kind="fixed", category__isnull=False) | Q(kind="percent", value__lte=100),
                name="discount_kind_valid",
            ),
        ]

    def __str__(self):
        return f"{self.student} — {self.get_kind_display()} {self.value}"


class Invoice(TenantScopedModel):
    """What a student owes. Issued invoices are never edited: a mistake is fixed by cancelling and
    issuing a new one. How much is paid and still due is always computed from payments."""

    class Status(models.TextChoices):
        ISSUED = "issued", _("Issued")
        CANCELLED = "cancelled", _("Cancelled")

    class Source(models.TextChoices):
        ENROLMENT = "enrolment", _("Enrolment fees")
        MANUAL = "manual", _("Manual")

    number = models.CharField(max_length=30)
    student = models.ForeignKey("people.Student", on_delete=models.PROTECT, related_name="invoices")
    enrollment = models.ForeignKey(
        "enrollments.Enrollment", null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    academic_year = models.ForeignKey(
        "academics.AcademicYear", on_delete=models.PROTECT, related_name="invoices"
    )
    issue_date = models.DateField()
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ISSUED)
    subtotal = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))
    discount_total = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0")
    )
    total = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))
    notes = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        verbose_name = "invoice"
        constraints = [models.UniqueConstraint(fields=["school", "number"], name="uniq_invoice_number")]
        indexes = [
            models.Index(fields=["school", "academic_year", "status"], name="invoice_school_year_idx"),
            models.Index(fields=["student", "status"], name="invoice_student_status_idx"),
        ]

    def __str__(self):
        return self.number


class InvoiceLine(TimeStampedModel):
    """One amount due on one date: each installment of a fee schedule becomes its own line."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    category = models.ForeignKey(FeeCategory, on_delete=models.PROTECT, related_name="invoice_lines")
    description = models.CharField(max_length=200)
    due_date = models.DateField()
    amount = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, validators=[MinValueValidator(Decimal("0"))]
    )
    discount = models.DecimalField(
        max_digits=MONEY_DIGITS,
        decimal_places=MONEY_PLACES,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["invoice", "due_date", "order", "id"]
        verbose_name = "invoice line"
        constraints = [
            models.CheckConstraint(
                condition=Q(discount__lte=models.F("amount")), name="line_discount_le_amount"
            )
        ]

    def __str__(self):
        return f"{self.invoice.number}: {self.description}"

    @property
    def net(self) -> Decimal:
        return self.amount - self.discount
