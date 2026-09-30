from typing import Any

from django.db.models import DecimalField, F, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce

from apps.finance.models import MONEY_DIGITS, MONEY_PLACES

from .models import CashMovement, CashSession

MONEY: DecimalField = DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)


def _movement_sum(direction: str) -> Coalesce:
    return Coalesce(
        Sum("movements__amount", filter=Q(movements__direction=direction)), Value(0, output_field=MONEY)
    )


def with_totals(queryset: QuerySet[CashSession]) -> QuerySet[Any]:
    """Annotate money_in, money_out and expected (opening + in − out) on each session."""
    return queryset.annotate(
        money_in=_movement_sum(CashMovement.Direction.IN), money_out=_movement_sum(CashMovement.Direction.OUT)
    ).annotate(expected=F("opening_balance") + F("money_in") - F("money_out"))
