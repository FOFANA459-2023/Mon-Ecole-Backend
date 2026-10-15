"""The dashboard's figures (ToR §5): KPIs, charts and alerts for one school year, narrowed by term, level,
class and payment status. Each block is computed only for users allowed to see it, and only over the classes
they see (teachers: their own)."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from apps.academics.models import AcademicYear, ClassGroup, Level, Term
from apps.enrollments.models import Enrollment

ZERO = Decimal("0")
MONEY: DecimalField = DecimalField(max_digits=14, decimal_places=2)
ATTENDANCE_DAYS = 30
SOON_DAYS = 14


@dataclass
class Filters:
    year: AcademicYear
    term: Term | None = None
    level: Level | None = None
    class_group: ClassGroup | None = None
    payment_status: str = ""


def scoped_classes(classes, f: Filters):
    if f.class_group is not None:
        return classes.filter(pk=f.class_group.pk)
    if f.level is not None:
        return classes.filter(level=f.level)
    return classes


def default_term(year: AcademicYear, today: date) -> Term | None:
    """The term running today, else the last one already started, else the first."""
    terms = list(year.terms.order_by("order"))
    current = next((t for t in terms if t.start_date <= today <= t.end_date), None)
    started = [t for t in terms if t.start_date <= today]
    return current or (started[-1] if started else (terms[0] if terms else None))


def by_class(classes) -> list[dict[str, Any]]:
    rows = (
        classes.annotate(count=Count("enrollments", filter=Q(enrollments__status=Enrollment.Status.ACTIVE)))
        .select_related("level")
        .order_by("level__order", "name")
    )
    return [
        {"class_group": c.pk, "class_name": c.name, "count": c.count, "capacity": c.capacity} for c in rows
    ]


# --- Finance ------------------------------------------------------------------------------------------------


def finance(school, classes, f: Filters, today: date) -> dict[str, Any]:
    from apps.finance.models import Invoice, InvoiceLine, Payment
    from apps.finance.selectors import with_balances

    invoices = Invoice.objects.filter(school=school, academic_year=f.year, status=Invoice.Status.ISSUED)
    if f.class_group is not None or f.level is not None:
        invoices = invoices.filter(enrollment__class_group__in=classes)
    balances = with_balances(invoices, today)
    if f.payment_status:
        balances = balances.filter(payment_status=f.payment_status)
    rows = list(
        balances.values(
            "pk",
            "student_id",
            "total",
            "amount_paid",
            "balance",
            "overdue_amount",
            "enrollment__class_group__name",
        )
    )
    expected = sum((r["total"] for r in rows), ZERO)
    collected = sum((r["amount_paid"] for r in rows), ZERO)
    outstanding = sum((max(r["balance"], ZERO) for r in rows), ZERO)
    overdue = sum((r["overdue_amount"] for r in rows), ZERO)
    owing = {r["student_id"] for r in rows if r["balance"] > 0}
    late = {r["student_id"] for r in rows if r["overdue_amount"] > 0}

    per_class: dict[str, dict[str, Any]] = defaultdict(lambda: {"amount": ZERO, "students": set()})
    for r in rows:
        if r["balance"] > 0:
            entry = per_class[r["enrollment__class_group__name"] or "—"]
            entry["amount"] += r["balance"]
            entry["students"].add(r["student_id"])
    outstanding_by_class = sorted(
        (
            {"class_name": name, "amount": v["amount"], "students": len(v["students"])}
            for name, v in per_class.items()
        ),
        key=lambda x: -x["amount"],
    )

    payments = Payment.objects.filter(
        school=school,
        status=Payment.Status.POSTED,
        date__range=(f.year.start_date, min(f.year.end_date, today)),
    )
    if f.class_group is not None or f.level is not None:
        students = Enrollment.objects.filter(class_group__in=classes, academic_year=f.year).values(
            "student_id"
        )
        payments = payments.filter(student_id__in=students)
    months = {
        row["month"].strftime("%Y-%m"): row["amount"]
        for row in payments.annotate(month=TruncMonth("date"))
        .values("month")
        .annotate(amount=Coalesce(Sum("amount"), Value(ZERO), output_field=MONEY))
        .order_by("month")
    }
    by_month = []
    cursor = f.year.start_date.replace(day=1)
    end = min(f.year.end_date, today)
    while cursor <= end:
        key = cursor.strftime("%Y-%m")
        by_month.append({"month": key, "amount": months.get(key, ZERO)})
        cursor = (cursor + timedelta(days=32)).replace(day=1)

    soon = today + timedelta(days=SOON_DAYS)
    upcoming = InvoiceLine.objects.filter(
        invoice__in=invoices, due_date__gte=today, due_date__lte=soon
    ).aggregate(
        count=Count("invoice", distinct=True),
        amount=Coalesce(Sum("amount") - Sum("discount"), Value(ZERO), output_field=MONEY),
    )
    return {
        "currency": school.currency,
        "expected": expected,
        "collected": collected,
        "outstanding": outstanding,
        "overdue": overdue,
        "students_with_balance": len(owing),
        "students_overdue": len(late),
        "collection_rate": (collected * 100 / expected).quantize(Decimal("0.1")) if expected else None,
        "by_month": by_month,
        "outstanding_by_class": outstanding_by_class[:12],
        "upcoming_deadlines": {
            "invoices": upcoming["count"],
            "amount": upcoming["amount"],
            "days": SOON_DAYS,
        },
    }


# --- Attendance ---------------------------------------------------------------------------------------------


def attendance(classes, f: Filters, today: date) -> dict[str, Any]:
    from apps.attendance.models import AttendanceRecord, Status

    if f.term is not None:
        start, end = f.term.start_date, min(f.term.end_date, today)
    else:
        start, end = today - timedelta(days=ATTENDANCE_DAYS - 1), today
    rows = (
        AttendanceRecord.objects.filter(register__class_group__in=classes, register__date__range=(start, end))
        .values("register__date")
        .annotate(
            present=Count("pk", filter=Q(status=Status.PRESENT)),
            absent=Count("pk", filter=Q(status=Status.ABSENT)),
            late=Count("pk", filter=Q(status=Status.LATE)),
            excused=Count("pk", filter=Q(status=Status.EXCUSED)),
        )
        .order_by("register__date")
    )
    days = []
    totals: Counter = Counter()
    for row in rows:
        marked = row["present"] + row["absent"] + row["late"] + row["excused"]
        totals.update({k: row[k] for k in ("present", "absent", "late", "excused")})
        days.append(
            {
                "date": row["register__date"],
                "present": row["present"],
                "absent": row["absent"],
                "late": row["late"],
                "excused": row["excused"],
                "rate": _rate(row["present"] + row["late"], marked),
            }
        )
    marked = sum(totals.values())
    return {
        "date_from": start,
        "date_to": end,
        "days": days,
        "rate": _rate(totals["present"] + totals["late"], marked),
        **{k: totals[k] for k in ("present", "absent", "late", "excused")},
    }


def _rate(part: int, whole: int) -> Decimal | None:
    return (Decimal(100 * part) / whole).quantize(Decimal("0.1")) if whole else None


# --- Performance --------------------------------------------------------------------------------------------


def performance(classes, term: Term | None) -> dict[str, Any] | None:
    """Published results of the term: the spread of averages over the honours bands, and each class."""
    from apps.assessments.selectors import class_results

    if term is None:
        return None
    bands: Counter = Counter()
    labels: list[tuple[Decimal, str]] = []
    rows = []
    counted = passed = 0
    pass_label = ""
    for class_group in classes.filter(academic_year=term.academic_year).select_related("level", "school"):
        results = class_results(class_group, term, published_only=True)
        scale = results["scale"]
        if not labels:
            labels = sorted(
                ((Decimal(str(m["min"])), m["label"]) for m in scale.mentions or []), reverse=True
            )
            pass_label = f"< {Decimal(scale.pass_mark).normalize():f}"
        averages = [s["average"] for s in results["students"] if s["average"] is not None]
        if not averages:
            continue
        stats = results["stats"]
        counted += stats["counted"]
        passed += stats["passed"]
        rows.append(
            {
                "class_name": class_group.name,
                "average": stats["average"],
                "pass_rate": _rate(stats["passed"], stats["counted"]),
                "counted": stats["counted"],
            }
        )
        for value in averages:
            band = next((label for minimum, label in labels if value >= minimum), None)
            if band is None:
                band = "" if value >= scale.pass_mark else pass_label
            bands[band] += 1
    distribution = [{"label": label, "count": bands.get(label, 0)} for _, label in labels]
    if bands.get(""):
        distribution.append({"label": "", "count": bands[""]})  # passed, below every honours band
    if pass_label:
        distribution.append({"label": pass_label, "count": bands.get(pass_label, 0), "below_pass": True})
    rows.sort(key=lambda r: -r["average"])
    return {
        "term": {"id": term.pk, "name": term.name},
        "counted": counted,
        "passed": passed,
        "pass_rate": _rate(passed, counted),
        "distribution": distribution,
        "by_class": rows,
    }


# --- Alerts -------------------------------------------------------------------------------------------------


def missing_grades(school, classes, term: Term | None) -> dict[str, Any] | None:
    from apps.assessments.models import Gradebook

    if term is None:
        return None
    rows = (
        Gradebook.objects.filter(school=school, term=term, class_subject__class_group__in=classes)
        .values("status")
        .annotate(n=Count("pk"))
    )
    counts = {row["status"]: row["n"] for row in rows}
    return {
        "term": {"id": term.pk, "name": term.name, "end_date": term.end_date},
        "open": counts.get("open", 0),
        "submitted": counts.get("submitted", 0),
        "published": counts.get("published", 0),
    }


def upcoming_exams(school, classes, today: date) -> list[dict[str, Any]]:
    from apps.administration.models import CalendarEvent

    levels = classes.values("level")
    events = (
        CalendarEvent.objects.filter(
            school=school,
            kind=CalendarEvent.Kind.EXAM,
            end_date__gte=today,
            start_date__lte=today + timedelta(days=30),
        )
        .filter(Q(levels__isnull=True) | Q(levels__in=levels))
        .distinct()
        .order_by("start_date")[:5]
    )
    return [
        {"id": e.pk, "title": e.title, "start_date": e.start_date, "end_date": e.end_date} for e in events
    ]


def latest_announcements(request) -> list[dict[str, Any]]:
    from apps.administration.api import visible_announcements
    from apps.administration.models import Announcement

    items = visible_announcements(request, Announcement.objects.filter(school=request.school))[:3]
    return [
        {"id": a.pk, "title": a.title, "priority": a.priority, "pinned": a.pinned, "publish_at": a.publish_at}
        for a in items
    ]


def today_for(school) -> date:
    from apps.attendance.services import school_today

    return school_today(school) if school else timezone.localdate()
