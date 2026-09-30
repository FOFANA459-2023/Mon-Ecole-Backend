"""Read-side finance figures. Paid amounts, balances and statuses are computed, never stored."""

from datetime import date
from decimal import Decimal

from django.db.models import (
    Case,
    CharField,
    DecimalField,
    F,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import MONEY_DIGITS, MONEY_PLACES, Invoice, InvoiceLine


class PaymentStatus:
    PAID = "paid"
    PARTIAL = "partial"
    UNPAID = "unpaid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"
    CHOICES = [PAID, PARTIAL, UNPAID, OVERDUE, CANCELLED]


def _money(value) -> Value:
    return Value(
        Decimal(value), output_field=DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    )


def _line_sum(filter_q: Q) -> Coalesce:
    lines = (
        InvoiceLine.objects.filter(filter_q, invoice=OuterRef("pk"))
        .values("invoice")
        .annotate(s=Sum(F("amount") - F("discount")))
        .values("s")
    )
    return Coalesce(
        Subquery(lines, output_field=DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)),
        _money(0),
    )


def with_balances(queryset: QuerySet[Invoice], today: date | None = None) -> QuerySet[Invoice]:
    """Annotate amount_paid, balance, overdue_amount, next_due_date and payment_status.

    Overdue: a line's due date has passed and the payments so far do not cover the lines already due.
    """
    today = today or timezone.localdate()
    next_due = (
        InvoiceLine.objects.filter(invoice=OuterRef("pk"), due_date__gte=today)
        .order_by("due_date")
        .values("due_date")[:1]
    )
    return queryset.annotate(
        # Payments arrive in the next slice; until then nothing is paid.
        amount_paid=_money(0),
        past_due_total=_line_sum(Q(due_date__lt=today)),
        next_due_date=Subquery(next_due),
    ).annotate(
        balance=F("total") - F("amount_paid"),
        overdue_amount=Case(
            When(past_due_total__gt=F("amount_paid"), then=F("past_due_total") - F("amount_paid")),
            default=_money(0),
            output_field=DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES),
        ),
        payment_status=Case(
            When(status=Invoice.Status.CANCELLED, then=Value(PaymentStatus.CANCELLED)),
            When(total__lte=F("amount_paid"), then=Value(PaymentStatus.PAID)),
            When(past_due_total__gt=F("amount_paid"), then=Value(PaymentStatus.OVERDUE)),
            When(amount_paid__gt=0, then=Value(PaymentStatus.PARTIAL)),
            default=Value(PaymentStatus.UNPAID),
            output_field=CharField(),
        ),
    )


def has_payments(invoice: Invoice) -> bool:
    """True once any payment is allocated to the invoice (payments arrive in the next slice)."""
    return False
