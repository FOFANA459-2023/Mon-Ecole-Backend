from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel
from apps.finance.models import MONEY_DIGITS, MONEY_PLACES


class CashRegister(TenantScopedModel):
    """A cash box: the school's main till, or a second one at another desk."""

    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "cash register"
        constraints = [models.UniqueConstraint(fields=["school", "name"], name="uniq_cash_register_name")]

    def __str__(self):
        return self.name


class CashSession(TenantScopedModel):
    """One opening of a register, usually a day: from the float at opening to the cash counted at closing.

    `created_by` opened it. While it is open, the expected cash is opening + money in − money out; closing
    stores that figure, what was counted and the difference, and no movement can be added afterwards.
    """

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CLOSED = "closed", _("Closed")

    register = models.ForeignKey(CashRegister, on_delete=models.PROTECT, related_name="sessions")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    opened_at = models.DateTimeField(default=timezone.now)
    opening_balance = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, validators=[MinValueValidator(Decimal("0"))]
    )
    opening_note = models.CharField(max_length=255, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    expected_closing = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, null=True, blank=True
    )
    counted_closing = models.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, null=True, blank=True
    )
    difference = models.DecimalField(
        max_digits=MONEY_DIGITS,
        decimal_places=MONEY_PLACES,
        null=True,
        blank=True,
        help_text="Counted − expected: positive is a surplus, negative a shortage.",
    )
    closing_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-opened_at", "-id"]
        verbose_name = "cash session"
        constraints = [
            models.UniqueConstraint(
                fields=["register"], condition=Q(status="open"), name="one_open_session_per_register"
            )
        ]
        indexes = [models.Index(fields=["school", "status"], name="cash_session_school_status_idx")]

    def __str__(self):
        return f"{self.register} — {self.opened_at:%Y-%m-%d}"


class CashMovement(TenantScopedModel):
    """Cash that came into or left a session. Never edited or deleted: a mistake is undone by the opposite
    movement (reversing a payment, cancelling an expense or a refund)."""

    class Direction(models.TextChoices):
        IN = "in", _("Money in")
        OUT = "out", _("Money out")

    class Source(models.TextChoices):
        PAYMENT = "payment", _("Payment")
        PAYMENT_REVERSAL = "payment_reversal", _("Payment reversed")
        EXPENSE = "expense", _("Expense")
        EXPENSE_CANCELLATION = "expense_cancellation", _("Expense cancelled")
        REFUND = "refund", _("Refund")
        REFUND_CANCELLATION = "refund_cancellation", _("Refund cancelled")
        MANUAL = "manual", _("Other movement")

    session = models.ForeignKey(CashSession, on_delete=models.PROTECT, related_name="movements")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    source = models.CharField(max_length=25, choices=Source.choices, default=Source.MANUAL)
    amount = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    description = models.CharField(max_length=255)
    payment = models.ForeignKey(
        "finance.Payment", null=True, blank=True, on_delete=models.PROTECT, related_name="cash_movements"
    )
    expense = models.ForeignKey(
        "finance.Expense", null=True, blank=True, on_delete=models.PROTECT, related_name="cash_movements"
    )
    refund = models.ForeignKey(
        "finance.Refund", null=True, blank=True, on_delete=models.PROTECT, related_name="cash_movements"
    )

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = "cash movement"
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="cash_movement_amount_positive")
        ]

    def __str__(self):
        return f"{self.get_direction_display()} {self.amount}: {self.description}"
