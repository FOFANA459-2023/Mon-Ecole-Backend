"""Read-side finance figures. Paid amounts, balances, credit and statuses are computed, never stored.

The helpers that annotate return `QuerySet[Any]` / `list[Any]`: mypy cannot see annotated attributes such as
`balance`, so typing them as plain models would flag every use.
"""

from datetime import date
from decimal import Decimal
from typing import Any

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

from .models import MONEY_DIGITS, MONEY_PLACES, Invoice, InvoiceLine, Payment, PaymentAllocation
from .money import ZERO

MONEY_FIELD: DecimalField = DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)


class PaymentStatus:
    PAID = "paid"
    PARTIAL = "partial"
    UNPAID = "unpaid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"
    CHOICES = [PAID, PARTIAL, UNPAID, OVERDUE, CANCELLED]


def _money(value) -> Value:
    return Value(Decimal(value), output_field=MONEY_FIELD)


def _sum_subquery(queryset: QuerySet, group_by: str, expression) -> Coalesce:
    """The sum of `expression` over `queryset` (already filtered on OuterRef), or 0."""
    sums = queryset.values(group_by).annotate(s=Sum(expression)).values("s")
    return Coalesce(Subquery(sums, output_field=MONEY_FIELD), _money(0))


def _line_sum(filter_q: Q) -> Coalesce:
    lines = InvoiceLine.objects.filter(filter_q, invoice=OuterRef("pk"))
    return _sum_subquery(lines, "invoice", F("amount") - F("discount"))


def counted_allocations() -> QuerySet[PaymentAllocation]:
    """Allocations that count: those of payments that were not reversed."""
    return PaymentAllocation.objects.filter(payment__status=Payment.Status.POSTED)


def with_balances(queryset: QuerySet[Invoice], today: date | None = None) -> QuerySet[Any]:
    """Annotate amount_paid, balance, overdue_amount, next_due_date and payment_status.

    Overdue: a line's due date has passed and the payments so far do not cover the lines already due.
    """
    today = today or timezone.localdate()
    next_due = (
        InvoiceLine.objects.filter(invoice=OuterRef("pk"), due_date__gte=today)
        .order_by("due_date")
        .values("due_date")[:1]
    )
    paid = _sum_subquery(
        counted_allocations().filter(invoice_line__invoice=OuterRef("pk")), "invoice_line__invoice", "amount"
    )
    return queryset.annotate(
        amount_paid=paid,
        past_due_total=_line_sum(Q(due_date__lt=today)),
        next_due_date=Subquery(next_due),
    ).annotate(
        balance=F("total") - F("amount_paid"),
        overdue_amount=Case(
            When(past_due_total__gt=F("amount_paid"), then=F("past_due_total") - F("amount_paid")),
            default=_money(0),
            output_field=MONEY_FIELD,
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


def with_line_balances(queryset: QuerySet[InvoiceLine]) -> QuerySet[Any]:
    """Annotate each invoice line with what has been paid on it and what is left (`paid`, `balance`)."""
    paid = _sum_subquery(counted_allocations().filter(invoice_line=OuterRef("pk")), "invoice_line", "amount")
    return queryset.annotate(paid=paid).annotate(balance=F("amount") - F("discount") - F("paid"))


def open_lines(student) -> list[Any]:
    """The student's unpaid invoice lines, oldest due first: the order payments are allocated in."""
    lines = with_line_balances(
        InvoiceLine.objects.filter(invoice__student=student, invoice__status=Invoice.Status.ISSUED)
    )
    return list(
        lines.filter(balance__gt=0)
        .select_related("invoice", "category")
        .order_by("due_date", "invoice__issue_date", "invoice_id", "order", "id")
    )


def with_allocated(queryset: QuerySet[Payment]) -> QuerySet[Any]:
    """Annotate `allocated` (paid to invoice lines) and `unallocated` (credit left; 0 once reversed)."""
    allocated = _sum_subquery(PaymentAllocation.objects.filter(payment=OuterRef("pk")), "payment", "amount")
    return queryset.annotate(allocated=allocated).annotate(
        unallocated=Case(
            When(status=Payment.Status.POSTED, then=F("amount") - F("allocated")),
            default=_money(0),
            output_field=MONEY_FIELD,
        )
    )


def student_credit(student) -> Decimal:
    """Money the student paid that no invoice line has used yet."""
    received = Payment.objects.filter(student=student, status=Payment.Status.POSTED).aggregate(
        s=Sum("amount")
    )["s"]
    used = counted_allocations().filter(payment__student=student).aggregate(s=Sum("amount"))["s"]
    return (received or ZERO) - (used or ZERO)


def student_account(student, today: date | None = None) -> dict:
    """What a student owes across their issued invoices, what is overdue, and their credit."""
    # A student has a handful of invoices: adding them up here is simpler than aggregating the annotations.
    invoices = list(
        with_balances(Invoice.objects.filter(student=student, status=Invoice.Status.ISSUED), today)
    )
    return {
        "invoiced": sum((invoice.total for invoice in invoices), ZERO),
        "paid": sum((invoice.amount_paid for invoice in invoices), ZERO),
        "balance": sum((invoice.balance for invoice in invoices), ZERO),
        "overdue": sum((invoice.overdue_amount for invoice in invoices), ZERO),
        "credit": student_credit(student),
        "open_lines": open_lines(student),
    }


def has_payments(invoice: Invoice) -> bool:
    """True once a payment that was not reversed is allocated to the invoice."""
    return counted_allocations().filter(invoice_line__invoice=invoice).exists()
