from datetime import date, timedelta

from django.db import transaction
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from apps.audit import services as audit

from .models import AcademicYear, Term


def current_year(school) -> AcademicYear | None:
    return AcademicYear.objects.filter(school=school, is_current=True).first()


def _term_names(school, count: int) -> list[str]:
    french = school.default_language == "fr"
    if count == 3:
        label = "Trimestre" if french else "Term"
    elif count == 2:
        label = "Semestre" if french else "Semester"
    else:
        label = "Période" if french else "Period"
    return [f"{label} {n}" for n in range(1, count + 1)]


def split_terms(start: date, end: date, count: int) -> list[tuple[date, date]]:
    """Split a school year into `count` consecutive periods of roughly equal length."""
    total = (end - start).days + 1
    periods = []
    cursor = start
    for index in range(count):
        length = total // count + (1 if index < total % count else 0)
        period_end = cursor + timedelta(days=length - 1)
        periods.append((cursor, period_end))
        cursor = period_end + timedelta(days=1)
    return periods


@transaction.atomic
def create_academic_year(
    school, *, name: str, start_date: date, end_date: date, term_count: int = 0, request=None
) -> AcademicYear:
    if end_date <= start_date:
        raise ValidationError({"end_date": [_("The end date must be after the start date.")]})
    make_current = not AcademicYear.objects.filter(school=school).exists()
    year = AcademicYear.objects.create(
        school=school,
        name=name,
        start_date=start_date,
        end_date=end_date,
        is_current=make_current,
        created_by=getattr(request, "user", None),
    )
    if term_count:
        for order, ((term_start, term_end), term_name) in enumerate(
            zip(split_terms(start_date, end_date, term_count), _term_names(school, term_count), strict=True),
            1,
        ):
            Term.objects.create(
                school=school,
                academic_year=year,
                name=term_name,
                order=order,
                start_date=term_start,
                end_date=term_end,
            )
    audit.record(
        "create",
        request=request,
        school=school,
        instance=year,
        module="academics",
        summary=f"Academic year created: {year.name}",
        new={**audit.snapshot(year, ["name", "start_date", "end_date", "is_current"]), "terms": term_count},
    )
    return year


@transaction.atomic
def set_current_year(year: AcademicYear, *, request=None) -> AcademicYear:
    previous = current_year(year.school)
    if previous == year:
        return year
    AcademicYear.objects.filter(school=year.school, is_current=True).update(is_current=False)
    year.is_current = True
    year.save(update_fields=["is_current", "updated_at"])
    audit.record(
        "update",
        request=request,
        school=year.school,
        instance=year,
        module="academics",
        summary=f"Current academic year set to {year.name}",
        old={"current_year": previous.name if previous else None},
        new={"current_year": year.name},
    )
    return year
