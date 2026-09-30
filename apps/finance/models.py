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


class Payment(TenantScopedModel):
    """Money received for a student. Its number is the receipt number; `created_by` received it.

    Posted payments are never edited or deleted: a mistake is corrected by reversing the payment (it then
    stops counting) and recording the right one. `allocations` say which invoice lines the money paid; the
    part not allocated is the student's credit, used by the next invoice issued to them.
    """

    class Method(models.TextChoices):
        CASH = "cash", _("Cash")
        MOBILE_MONEY = "mobile_money", _("Mobile money")
        BANK_TRANSFER = "bank_transfer", _("Bank transfer or deposit")
        CHEQUE = "cheque", _("Cheque")
        CARD = "card", _("Card")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        POSTED = "posted", _("Posted")
        REVERSED = "reversed", _("Reversed")

    number = models.CharField(max_length=30)
    student = models.ForeignKey("people.Student", on_delete=models.PROTECT, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, validators=[MinValueValidator(Decimal("0.01"))]
    )
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    reference = models.CharField(
        max_length=100, blank=True, help_text="Mobile money transaction ID, bank slip or cheque number."
    )
    payer_name = models.CharField(max_length=150, blank=True, help_text="Who brought the money.")
    note = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reversal_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "payment"
        constraints = [
            models.UniqueConstraint(fields=["school", "number"], name="uniq_payment_number"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payment_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["school", "date"], name="payment_school_date_idx"),
            models.Index(fields=["student", "status"], name="payment_student_status_idx"),
        ]

    def __str__(self):
        return self.number


class PaymentAllocation(TimeStampedModel):
    """The part of a payment that pays one invoice line. Allocations of reversed payments no longer count."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, related_name="allocations")
    amount = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)

    class Meta:
        ordering = ["payment", "invoice_line__due_date", "id"]
        verbose_name = "payment allocation"
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="allocation_amount_positive")]

    def __str__(self):
        return f"{self.payment.number} → {self.invoice_line_id}: {self.amount}"


class Expense(TenantScopedModel):
    """Money the school spent. Never edited or deleted: a mistake is cancelled (with a reason) and recorded
    again. An expense paid in cash leaves the open cash session. `created_by` recorded it."""

    class Category(models.TextChoices):
        SALARIES = "salaries", _("Salaries and allowances")
        RENT = "rent", _("Rent")
        UTILITIES = "utilities", _("Water, electricity, internet")
        SUPPLIES = "supplies", _("Office and teaching supplies")
        MAINTENANCE = "maintenance", _("Repairs and maintenance")
        TRANSPORT = "transport", _("Transport and fuel")
        FOOD = "food", _("Food and canteen")
        EVENTS = "events", _("Exams and events")
        TAXES = "taxes", _("Taxes and fees")
        OTHER = "other", _("Other")

    class Status(models.TextChoices):
        RECORDED = "recorded", _("Recorded")
        CANCELLED = "cancelled", _("Cancelled")

    number = models.CharField(max_length=30)
    date = models.DateField()
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    amount = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, validators=[MinValueValidator(Decimal("0.01"))]
    )
    method = models.CharField(max_length=20, choices=Payment.Method.choices, default=Payment.Method.CASH)
    payee = models.CharField(max_length=150, blank=True, help_text="Who was paid.")
    reference = models.CharField(max_length=100, blank=True, help_text="Invoice, slip or cheque number.")
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECORDED)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "expense"
        constraints = [
            models.UniqueConstraint(fields=["school", "number"], name="uniq_expense_number"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="expense_amount_positive"),
        ]
        indexes = [models.Index(fields=["school", "date"], name="expense_school_date_idx")]

    def __str__(self):
        return self.number


class Refund(TenantScopedModel):
    """Credit given back to a student's family. It can never be more than the credit they have. Cancelled,
    never deleted; a cash refund leaves the open cash session. `created_by` paid it out."""

    class Status(models.TextChoices):
        POSTED = "posted", _("Posted")
        CANCELLED = "cancelled", _("Cancelled")

    student = models.ForeignKey("people.Student", on_delete=models.PROTECT, related_name="refunds")
    date = models.DateField()
    amount = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, validators=[MinValueValidator(Decimal("0.01"))]
    )
    method = models.CharField(max_length=20, choices=Payment.Method.choices, default=Payment.Method.CASH)
    reference = models.CharField(max_length=100, blank=True)
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.POSTED)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "refund"
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="refund_amount_positive")]
        indexes = [models.Index(fields=["student", "status"], name="refund_student_status_idx")]

    def __str__(self):
        return f"{self.student} — {self.amount}"
