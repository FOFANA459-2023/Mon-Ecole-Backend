"""Taking registers. Everyone starts present; the teacher marks who is absent, late or excused.

A register can be taken (and changed) on the day by the class's teachers. Changing an earlier day needs
`attendance.edit`, and every change is written to the audit log (old → new). No register for a future day.
"""

from datetime import date
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.academics.models import ClassGroup
from apps.academics.scoping import visible_classes
from apps.audit import services as audit
from apps.enrollments.models import Enrollment
from apps.people.models import StaffMember

from .models import AttendanceRecord, ClassRegister, StaffAttendance, Status

ENDED = [Enrollment.Status.CLASS_CHANGED, Enrollment.Status.WITHDRAWN, Enrollment.Status.COMPLETED]


def _user(request):
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


def school_today(school) -> date:
    """Today in the school's time zone (Conakry and Monrovia are on GMT; other schools may not be)."""
    try:
        zone = ZoneInfo(school.timezone)
    except (KeyError, ValueError):
        zone = timezone.get_current_timezone()
    return timezone.localdate(timezone=zone)


def roster(class_group: ClassGroup, day: date, register: ClassRegister | None = None) -> list[Enrollment]:
    """The students in the class that day: enrolled by then and not yet gone, plus anyone already on the
    register."""
    in_class = Q(enrollment_date__lte=day) & (
        Q(status=Enrollment.Status.ACTIVE) | Q(status__in=ENDED, ended_on__gte=day)
    )
    if register is not None:
        in_class |= Q(attendance_records__register=register)
    return list(
        Enrollment.objects.filter(in_class, class_group=class_group)
        .select_related("student")
        .distinct()
        .order_by("student__last_name", "student__first_name", "id")
    )


def can_take(request, class_group: ClassGroup) -> bool:
    """The class's teachers (subject teachers and class teacher), or someone who sees every class."""
    if "attendance.record" not in request.permission_codes:
        return False
    return visible_classes(request, ClassGroup.objects.filter(pk=class_group.pk)).exists()


def can_change(request, class_group: ClassGroup, day: date) -> bool:
    today = school_today(class_group.school)
    if day > today:
        return False
    if day == today:
        return can_take(request, class_group)
    return (
        "attendance.edit" in request.permission_codes
        and visible_classes(request, ClassGroup.objects.filter(pk=class_group.pk)).exists()
    )


def check_day(class_group: ClassGroup, day: date) -> None:
    if day > school_today(class_group.school):
        raise ValidationError({"date": [_("A register cannot be taken for a day that has not come yet.")]})
    year = class_group.academic_year
    if not (year.start_date <= day <= year.end_date):
        raise ValidationError({"date": [_("This day is outside the class's school year.")]})


def _clean(status: str, minutes, note: str) -> tuple[str, int | None, str]:
    return status, (minutes or None) if status == Status.LATE else None, (note or "").strip()


def _describe(record) -> str:
    text = record.status
    if record.minutes_late:
        text += f" ({record.minutes_late} min)"
    return f"{text} — {record.note}" if record.note else text


@transaction.atomic
def save_register(class_group: ClassGroup, day: date, entries: list[dict], *, request=None) -> ClassRegister:
    """Record the class's register for the day. Students left out of `entries` are present on a new
    register and unchanged on an existing one."""
    check_day(class_group, day)
    if request is not None and not can_change(request, class_group, day):
        if day < school_today(class_group.school) and can_take(request, class_group):
            raise PermissionDenied(_("Only the school management can change the register of an earlier day."))
        raise PermissionDenied(_("Only the teachers of this class can take its register."))
    register, created = ClassRegister.objects.select_for_update().get_or_create(
        school=class_group.school,
        class_group=class_group,
        date=day,
        defaults={"created_by": _user(request)},
    )
    students = {e.pk: e for e in roster(class_group, day, None if created else register)}
    existing = {r.enrollment_id: r for r in register.records.select_for_update()}
    errors: dict[str, list[str]] = {}
    given: dict[int, dict] = {}
    for index, entry in enumerate(entries):
        if entry["enrollment"] not in students:
            errors[str(index)] = [_("This student is not in the class that day.")]
        elif entry["enrollment"] in given:
            errors[str(index)] = [_("This student appears twice.")]
        else:
            given[entry["enrollment"]] = entry
    if errors:
        raise ValidationError({"records": errors})

    old: dict[str, str | None] = {}
    new: dict[str, str | None] = {}
    for pk, enrollment in students.items():
        given_entry = given.get(pk)
        record = existing.get(pk)
        if given_entry is None and record is not None:
            continue
        status, minutes, note = _clean(
            given_entry["status"] if given_entry else Status.PRESENT,
            given_entry.get("minutes_late") if given_entry else None,
            given_entry.get("note", "") if given_entry else "",
        )
        if record is None:
            record = AttendanceRecord(
                school=class_group.school, register=register, enrollment=enrollment, created_by=_user(request)
            )
        elif (record.status, record.minutes_late, record.note) == (status, minutes, note):
            continue
        before = None if record.pk is None else _describe(record)
        record.status, record.minutes_late, record.note = status, minutes, note
        record.save()
        if not created:
            old[str(pk)], new[str(pk)] = before, _describe(record)

    if not created:
        register.updated_by = _user(request)
        register.save(update_fields=["updated_by", "updated_at"])
    counts = {s: register.records.filter(status=s).count() for s in Status.values}
    if created or new:
        audit.record(
            "create" if created else "update",
            request=request,
            school=class_group.school,
            instance=register,
            module="attendance",
            summary=(
                f"Register {'taken' if created else 'changed'}: {class_group.name}, {day.isoformat()} — "
                f"{counts['absent']} absent, {counts['late']} late, {counts['excused']} excused"
            ),
            old=old or None,
            new=new or counts,
        )
    return register


# --- Staff -------------------------------------------------------------------------------------------------


def staff_on(school, day: date):
    """Staff expected at work that day: active, and employed by then (when the date is known)."""
    return (
        StaffMember.objects.filter(school=school, status=StaffMember.Status.ACTIVE)
        .filter(Q(employment_date__isnull=True) | Q(employment_date__lte=day))
        .order_by("last_name", "first_name", "id")
    )


@transaction.atomic
def save_staff_attendance(school, day: date, entries: list[dict], *, request=None) -> int:
    """Record staff presence for a day. Returns how many entries changed (all audited)."""
    if day > school_today(school):
        raise ValidationError({"date": [_("Attendance cannot be recorded for a day that has not come yet.")]})
    staff = {s.pk: s for s in staff_on(school, day)}
    existing = {
        a.staff_id: a for a in StaffAttendance.objects.select_for_update().filter(school=school, date=day)
    }
    errors = {
        str(index): [_("This person is not on the staff that day.")]
        for index, entry in enumerate(entries)
        if entry["staff"] not in staff and entry["staff"] not in existing
    }
    if errors:
        raise ValidationError({"entries": errors})
    old: dict[str, str | None] = {}
    new: dict[str, str | None] = {}
    for entry in entries:
        status, minutes, note = _clean(entry["status"], entry.get("minutes_late"), entry.get("note", ""))
        row = existing.get(entry["staff"])
        if row is None:
            row = StaffAttendance(school=school, staff_id=entry["staff"], date=day, created_by=_user(request))
        elif (row.status, row.minutes_late, row.note) == (status, minutes, note):
            continue
        before = None if row.pk is None else _describe(row)
        row.status, row.minutes_late, row.note, row.updated_by = status, minutes, note, _user(request)
        row.save()
        old[str(entry["staff"])], new[str(entry["staff"])] = before, _describe(row)
    if new:
        audit.record(
            "update",
            request=request,
            school=school,
            module="attendance",
            entity_type="attendance.staffattendance",
            entity_id=day.isoformat(),
            summary=f"Staff attendance for {day.isoformat()}: {len(new)} change(s)",
            old=old,
            new=new,
        )
    return len(new)
