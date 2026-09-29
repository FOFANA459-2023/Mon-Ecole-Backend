"""Money arithmetic: every amount is a Decimal rounded to the school currency's smallest unit."""

from decimal import ROUND_HALF_UP, Decimal

# ISO 4217 currencies without minor units that schools here may use (Guinean franc, CFA francs).
ZERO_DECIMAL_CURRENCIES = frozenset({"GNF", "XOF", "XAF", "RWF", "BIF", "DJF", "KMF", "UGX"})

ZERO = Decimal("0")


def minor_places(currency: str) -> int:
    return 0 if (currency or "").upper() in ZERO_DECIMAL_CURRENCIES else 2


def to_money(value, currency: str) -> Decimal:
    """Round half-up to the currency's precision (GNF: whole francs; LRD, USD: cents)."""
    step = Decimal(1).scaleb(-minor_places(currency))
    return Decimal(value).quantize(step, rounding=ROUND_HALF_UP)


def split(total: Decimal, weights: list[Decimal], currency: str) -> list[Decimal]:
    """Share `total` in proportion to `weights`, rounded, with the rounding remainder on the last share.

    The shares always add up to `total` exactly.
    """
    if not weights:
        return []
    weight_sum = sum(weights, ZERO)
    if weight_sum == 0:
        return [ZERO] * (len(weights) - 1) + [to_money(total, currency)]
    shares = [to_money(total * w / weight_sum, currency) for w in weights[:-1]]
    return [*shares, to_money(total, currency) - sum(shares, ZERO)]
