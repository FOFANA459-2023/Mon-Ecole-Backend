"""What the attendance screens and reports show, computed from the registers."""

import calendar
from datetime import date
from typing import Any

from django.db.models import Count, Q

from apps.academics.models import ClassGroup
from apps.academics.scoping import visible_classes
from apps.enrollments.models import Enrollment

from .models import AttendanceRecord, ClassRegister, Status
from .services import can_change, can_take, roster

COUNTED = [Status.ABSENT, Status.LATE, Status.EXCUSED]


def _name(user) -> str:
    return (user.get_full_name() or user.email) if user else ""


def _counts(queryset) -> dict[str, int]:
    return queryset.aggregate(
        present=Count("pk", filter=Q(status=Status.PRESENT)),
        absent=Count("pk", filter=Q(status=Status.ABSENT)),
        late=Count("pk", filter=Q(status=Status.LATE)),
        excused=Count("pk", filter=Q(status=Status.EXCUSED)),
    )


def day_overview(request, day: date) -> list[dict[str, Any]]:
    """Every class the user sees that is in session that day, with its register (or none yet)."""
    classes = visible_classes(
        request,
        ClassGroup.objects.filter(
            school=request.school,
            status=ClassGroup.Status.ACTIVE,
            academic_year__start_date__lte=day,
            academic_year__end_date__gte=day,
        ).select_related("level", "academic_year", "class_teacher"),
    ).order_by("level__order", "name")
    registers = {
        r.class_group_id: r
        for r in ClassRegister.objects.filter(school=request.school, date=day).select_related(
            "created_by", "updated_by"
        )
    }
    counts = {
        row["register__class_group"]: row
        for row in AttendanceRecord.objects.filter(register__school=request.school, register__date=day)
        .values("register__class_group")
        .annotate(
            present=Count("pk", filter=Q(status=Status.PRESENT)),
            absent=Count("pk", filter=Q(status=Status.ABSENT)),
            late=Count("pk", filter=Q(status=Status.LATE)),
            excused=Count("pk", filter=Q(status=Status.EXCUSED)),
        )
    }
    students = dict(
        Enrollment.objects.filter(class_group__in=classes, status=Enrollment.Status.ACTIVE)
        .values_list("class_group")
        .annotate(n=Count("pk"))
        .values_list("class_group", "n")
    )
    rows = []
    for class_group in classes:
        register = registers.get(class_group.pk)
        tally = counts.get(class_group.pk, {})
        rows.append(
            {
                "class_group": class_group.pk,
                "class_name": class_group.name,
                "level_name": class_group.level.name,
                "student_count": students.get(class_group.pk, 0),
                "register": register.pk if register else None,
                "taken_by_name": _name(register.created_by) if register else "",
                "taken_at": register.created_at if register else None,
                "present": tally.get("present", 0),
                "absent": tally.get("absent", 0),
                "late": tally.get("late", 0),
                "excused": tally.get("excused", 0),
                "can_take": can_change(request, class_group, day),
            }
        )
    return rows


def register_sheet(request, class_group: ClassGroup, day: date) -> dict[str, Any]:
    """The register for one class and day: what was recorded; students not marked yet have no status."""
    register = (
        ClassRegister.objects.filter(class_group=class_group, date=day)
        .select_related("created_by", "updated_by")
        .first()
    )
    records = {r.enrollment_id: r for r in register.records.all()} if register is not None else {}
    students = []
    for enrollment in roster(class_group, day, register):
        record = records.get(enrollment.pk)
        students.append(
            {
                "enrollment": enrollment.pk,
                "student": enrollment.student_id,
                "student_name": enrollment.student.full_name,
                "student_number": enrollment.student.student_number,
                "status": record.status if record else None,
                "minutes_late": record.minutes_late if record else None,
                "note": record.note if record else "",
            }
        )
    return {
        "class_group": class_group.pk,
        "class_name": class_group.name,
        "date": day,
        "register": register.pk if register else None,
        "taken_by_name": _name(register.created_by) if register else "",
        "taken_at": register.created_at if register else None,
        "updated_by_name": _name(register.updated_by) if register else "",
        "updated_at": register.updated_at if register and register.updated_by_id else None,
        "can_edit": can_change(request, class_group, day),
        "can_take_today": can_take(request, class_group),
        "students": students,
    }


def class_month(class_group: ClassGroup, year: int, month: int) -> dict[str, Any]:
    """A month of registers for a class: one column per day a register was taken, one row per student."""
    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    registers = list(
        ClassRegister.objects.filter(class_group=class_group, date__range=(first, last)).order_by("date")
    )
    records = AttendanceRecord.objects.filter(register__in=registers).select_related("register")
    by_student: dict[int, dict[str, Any]] = {}
    for record in records:
        by_student.setdefault(record.enrollment_id, {})[record.register.date.isoformat()] = record.status
    enrollments = {
        e.pk: e
        for e in Enrollment.objects.filter(
            Q(pk__in=by_student.keys()) | (Q(status=Enrollment.Status.ACTIVE) & Q(enrollment_date__lte=last)),
            class_group=class_group,
        ).select_related("student")
    }
    rows = []
    for enrollment in sorted(
        enrollments.values(), key=lambda e: (e.student.last_name, e.student.first_name, e.pk)
    ):
        days = by_student.get(enrollment.pk, {})
        rows.append(
            {
                "enrollment": enrollment.pk,
                "student": enrollment.student_id,
                "student_name": enrollment.student.full_name,
                "days": [{"date": d, "status": s} for d, s in sorted(days.items())],
                "present": sum(1 for s in days.values() if s == Status.PRESENT),
                "absent": sum(1 for s in days.values() if s == Status.ABSENT),
                "late": sum(1 for s in days.values() if s == Status.LATE),
                "excused": sum(1 for s in days.values() if s == Status.EXCUSED),
            }
        )
    return {
        "class_group": class_group.pk,
        "class_name": class_group.name,
        "month": f"{year:04d}-{month:02d}",
        "dates": [r.date for r in registers],
        "students": rows,
        "totals": _counts(records),
    }


def absences(
    request, date_from: date, date_to: date, class_group: ClassGroup | None, minimum: int
) -> list[dict]:
    """Students with the most absences over a period (and their lateness), for follow-up."""
    classes = visible_classes(request, ClassGroup.objects.filter(school=request.school))
    if class_group is not None:
        classes = classes.filter(pk=class_group.pk)
    rows = (
        AttendanceRecord.objects.filter(
            register__class_group__in=classes,
            register__date__range=(date_from, date_to),
            status__in=COUNTED,
        )
        .values(
            "enrollment",
            "enrollment__student",
            "enrollment__student__first_name",
            "enrollment__student__last_name",
            "enrollment__student__student_number",
            "register__class_group__name",
        )
        .annotate(
            absent=Count("pk", filter=Q(status=Status.ABSENT)),
            late=Count("pk", filter=Q(status=Status.LATE)),
            excused=Count("pk", filter=Q(status=Status.EXCUSED)),
        )
        .filter(absent__gte=minimum)
        .order_by("-absent", "-late", "enrollment__student__last_name")
    )
    return [
        {
            "enrollment": row["enrollment"],
            "student": row["enrollment__student"],
            "student_name": " ".join(
                (row["enrollment__student__first_name"], row["enrollment__student__last_name"])
            ),
            "student_number": row["enrollment__student__student_number"],
            "class_name": row["register__class_group__name"],
            "absent": row["absent"],
            "late": row["late"],
            "excused": row["excused"],
        }
        for row in rows[:500]
    ]


def student_summary(student, academic_year) -> dict[str, Any]:
    """A student's attendance for a school year: totals and every day they were not simply present."""
    records = AttendanceRecord.objects.filter(
        enrollment__student=student, enrollment__academic_year=academic_year
    ).select_related("register__class_group")
    totals = _counts(records)
    return {
        "academic_year": academic_year.pk,
        "days": sum(totals.values()),
        **totals,
        "events": [
            {
                "date": r.register.date,
                "class_name": r.register.class_group.name,
                "status": r.status,
                "minutes_late": r.minutes_late,
                "note": r.note,
            }
            for r in records.exclude(status=Status.PRESENT).order_by("-register__date")
        ],
    }


def staff_sheet(school, day: date) -> dict[str, Any]:
    """Every member of staff expected that day, with what was recorded (no status when nothing yet)."""
    from .models import StaffAttendance
    from .services import staff_on

    recorded = {a.staff_id: a for a in StaffAttendance.objects.filter(school=school, date=day)}
    staff = list(staff_on(school, day))
    known = {s.pk for s in staff}
    staff += [a.staff for a in recorded.values() if a.staff_id not in known]
    return {
        "date": day,
        "staff": [
            {
                "staff": s.pk,
                "full_name": s.full_name,
                "position": s.position,
                "staff_type": s.staff_type,
                "recorded": s.pk in recorded,
                "status": recorded[s.pk].status if s.pk in recorded else None,
                "minutes_late": recorded[s.pk].minutes_late if s.pk in recorded else None,
                "note": recorded[s.pk].note if s.pk in recorded else "",
            }
            for s in staff
        ],
    }
