"""The calculation engine: pure functions over Decimals, no database.

A student's subject mark for a term is worked out from the teacher's own rules:

1. Each assessment the student has a score on counts as score / max (a fraction of 1). An excused student is
   left out. A missing score is left out too, unless the teacher counts missing marks as zero — and then only
   for assessments the teacher has started marking (an assessment nobody has a mark on yet never counts).
2. Each category combines its assessments: the weighted average of their fractions ("average"), or the
   points earned over the points possible ("total").
3. The categories are combined by their weights. A category without any counted assessment for the student
   is left out and the other weights share its place, so a term in progress still gives a fair mark.
4. The fraction is reported on the school's scale (out of 20, 10, 100...) and rounded half-up.

The overall average is the coefficient-weighted average of the (rounded) subject marks, as on a report card.
"""

from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
AVERAGE = "average"
TOTAL = "total"
EXCLUDE = "exclude"
COUNT_AS_ZERO = "zero"
COMPETITION = "competition"
DENSE = "dense"


@dataclass(frozen=True)
class Category:
    id: int
    weight: Decimal
    method: str = AVERAGE


@dataclass(frozen=True)
class Item:
    """An assessment: its category, what it is marked out of and its weight within the category."""

    id: int
    category_id: int
    max_score: Decimal
    weight: Decimal = Decimal("1")


@dataclass(frozen=True)
class Mark:
    score: Decimal | None = None
    excused: bool = False


@dataclass(frozen=True)
class Scale:
    max_mark: Decimal = Decimal("20")
    pass_mark: Decimal = Decimal("10")
    decimals: int = 2
    rank_method: str = COMPETITION


def round_mark(value: Decimal, decimals: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def counted_scores(
    items: Iterable[Item], marks: Mapping[int, Mark], *, missing_policy: str, started: set[int]
) -> dict[int, Decimal]:
    """The score each assessment counts for, for one student (assessments that do not count are absent)."""
    counted = {}
    for item in items:
        mark = marks.get(item.id, Mark())
        if mark.excused:
            continue
        if mark.score is not None:
            counted[item.id] = mark.score
        elif missing_policy == COUNT_AS_ZERO and item.id in started:
            counted[item.id] = ZERO
    return counted


def category_fraction(
    category: Category, items: list[Item], counted: Mapping[int, Decimal]
) -> Decimal | None:
    """The category's result as a fraction of 1, or None when nothing in it counts yet."""
    scored = [item for item in items if item.category_id == category.id and item.id in counted]
    if not scored:
        return None
    if category.method == TOTAL:
        return sum((counted[i.id] for i in scored), ZERO) / sum((i.max_score for i in scored), ZERO)
    weights = sum((i.weight for i in scored), ZERO)
    return sum((counted[i.id] / i.max_score * i.weight for i in scored), ZERO) / weights


def subject_fraction(
    categories: list[Category],
    items: list[Item],
    marks: Mapping[int, Mark],
    *,
    missing_policy: str = EXCLUDE,
    started: set[int] | None = None,
) -> Decimal | None:
    """One student's result in one subject as a fraction of 1, or None when nothing counts yet."""
    counted = counted_scores(items, marks, missing_policy=missing_policy, started=started or set())
    parts = [
        (fraction, category.weight)
        for category in categories
        if (fraction := category_fraction(category, items, counted)) is not None
    ]
    weights = sum((weight for _, weight in parts), ZERO)
    if not parts or weights == 0:
        return None
    return sum((fraction * weight for fraction, weight in parts), ZERO) / weights


def to_scale(fraction: Decimal | None, scale: Scale) -> Decimal | None:
    return None if fraction is None else round_mark(fraction * scale.max_mark, scale.decimals)


def ranks[K: Hashable](values: Mapping[K, Decimal | None], method: str = COMPETITION) -> dict[K, int]:
    """Rank by value, highest first; equal values share a rank. Keys without a value get no rank."""
    ordered = sorted(((v, k) for k, v in values.items() if v is not None), key=lambda pair: -pair[0])
    result: dict[K, int] = {}
    previous = None
    rank = 0
    for position, (value, key) in enumerate(ordered, 1):
        if value != previous:
            rank = rank + 1 if method == DENSE else position
            previous = value
        result[key] = rank
    return result


def overall_average(marks: Iterable[tuple[Decimal | None, Decimal]], decimals: int) -> Decimal | None:
    """Coefficient-weighted average of (mark, coefficient) pairs; subjects without a mark are left out."""
    pairs = [(mark, coefficient) for mark, coefficient in marks if mark is not None]
    coefficients = sum((c for _, c in pairs), ZERO)
    if not pairs or coefficients == 0:
        return None
    return round_mark(sum((m * c for m, c in pairs), ZERO) / coefficients, decimals)


@dataclass(frozen=True)
class Stats:
    average: Decimal | None
    lowest: Decimal | None
    highest: Decimal | None
    passed: int
    counted: int


def stats(values: Iterable[Decimal | None], scale: Scale) -> Stats:
    present = [v for v in values if v is not None]
    if not present:
        return Stats(None, None, None, 0, 0)
    return Stats(
        average=round_mark(sum(present, ZERO) / len(present), scale.decimals),
        lowest=min(present),
        highest=max(present),
        passed=sum(1 for v in present if v >= scale.pass_mark),
        counted=len(present),
    )
