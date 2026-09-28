from django.db import IntegrityError, transaction

from .models import Sequence


def next_value(school, key: str, year: int) -> int:
    """Return the next counter value, safe under concurrent requests (row lock on PostgreSQL)."""
    with transaction.atomic():
        seq = Sequence.objects.select_for_update().filter(school=school, key=key, year=year).first()
        if seq is None:
            try:
                with transaction.atomic():
                    seq = Sequence.objects.create(school=school, key=key, year=year)
            except IntegrityError:
                seq = Sequence.objects.select_for_update().get(school=school, key=key, year=year)
        seq.last_value += 1
        seq.save(update_fields=["last_value"])
        return seq.last_value


def reserve_values(school, key: str, year: int, count: int) -> int:
    """Reserve `count` consecutive values at once (bulk imports); returns the first one."""
    with transaction.atomic():
        seq = Sequence.objects.select_for_update().filter(school=school, key=key, year=year).first()
        if seq is None:
            try:
                with transaction.atomic():
                    seq = Sequence.objects.create(school=school, key=key, year=year)
            except IntegrityError:
                seq = Sequence.objects.select_for_update().get(school=school, key=key, year=year)
        first = seq.last_value + 1
        seq.last_value += count
        seq.save(update_fields=["last_value"])
        return first


def format_number(prefix: str, year: int, value: int, width: int = 6) -> str:
    return f"{prefix}-{year}-{value:0{width}d}"


def next_number(school, key: str, prefix: str, year: int, width: int = 6) -> str:
    return format_number(prefix, year, next_value(school, key, year), width)
