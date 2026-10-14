"""High-absenteeism alerts (ToR §5 "High absenteeism", §21 "Absences").

Every evening each school's registers are checked: a student with at least the school's number of
unexcused absences ("absent", not "excused") within its period gets an alert, open until someone follows it
up. Absences already counted by an alert that was followed up are not counted again, so the same absences
never raise a second alert. An open alert follows the registers: its count is updated, and it is cleared if
corrected registers bring the absences under the number.
"""

from collections import defaultdict
from datetime import date, timedelta

from django.db import transaction
from django.db.models import Max

from apps.audit import services as audit
from apps.enrollments.models import Enrollment
from apps.schools.models import School

from .models import AbsenceAlert, AttendanceRecord, Status
from .services import school_today

DEFAULT_THRESHOLD = 3
DEFAULT_DAYS = 30


def rule(school) -> tuple[int, int]:
    """(absences, days) for the school; 0 absences = no alerts."""
    settings = getattr(school, "settings", None)
    if settings is None:
        return DEFAULT_THRESHOLD, DEFAULT_DAYS
    return settings.absence_alert_threshold, settings.absence_alert_days


@transaction.atomic
def check_school(school, today: date | None = None) -> dict[str, int]:
    """Raise, update or clear the school's alerts. Returns how many were raised, updated and cleared."""
    threshold, days = rule(school)
    today = today or school_today(school)
    outcome = {"raised": 0, "updated": 0, "cleared": 0}
    open_alerts = {
        a.enrollment_id: a
        for a in AbsenceAlert.objects.select_for_update().filter(
            school=school, status=AbsenceAlert.AlertStatus.OPEN
        )
    }
    if threshold == 0:
        counted: dict[int, list[date]] = {}
    else:
        start = today - timedelta(days=days - 1)
        # Absences up to the last follow-up were dealt with then.
        followed = dict(
            AbsenceAlert.objects.filter(school=school, status=AbsenceAlert.AlertStatus.FOLLOWED_UP)
            .values_list("enrollment")
            .annotate(last=Max("last_absence"))
        )
        counted = defaultdict(list)
        records = AttendanceRecord.objects.filter(
            register__school=school,
            register__date__range=(start, today),
            status=Status.ABSENT,
            enrollment__status=Enrollment.Status.ACTIVE,
        ).values_list("enrollment_id", "register__date")
        for enrollment_id, day in records:
            if enrollment_id not in followed or day > followed[enrollment_id]:
                counted[enrollment_id].append(day)

    for enrollment_id, dates in counted.items():
        if len(dates) < threshold:
            continue
        alert = open_alerts.pop(enrollment_id, None)
        values = {"absences": len(dates), "since": min(dates), "last_absence": max(dates)}
        if alert is None:
            AbsenceAlert.objects.create(school=school, enrollment_id=enrollment_id, raised_on=today, **values)
            outcome["raised"] += 1
        elif any(getattr(alert, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(alert, key, value)
            alert.save(update_fields=[*values, "updated_at"])
            outcome["updated"] += 1
    # Open alerts left over no longer reach the number (registers corrected, student left, rule changed).
    for alert in open_alerts.values():
        alert.status, alert.closed_on = AbsenceAlert.AlertStatus.CLEARED, today
        alert.save(update_fields=["status", "closed_on", "updated_at"])
        outcome["cleared"] += 1
    return outcome


def check_all_schools() -> int:
    raised = 0
    for school in School.objects.filter(status=School.Status.ACTIVE).select_related("settings"):
        raised += check_school(school)["raised"]
    return raised


def follow_up(alert: AbsenceAlert, *, note: str, request) -> AbsenceAlert:
    alert.status = AbsenceAlert.AlertStatus.FOLLOWED_UP
    alert.closed_on = school_today(alert.school)
    alert.closed_by = request.user
    alert.note = note
    alert.save(update_fields=["status", "closed_on", "closed_by", "note", "updated_at"])
    audit.record(
        "update",
        request=request,
        instance=alert,
        module="attendance",
        summary=f"Absence alert followed up: {alert.enrollment.student.full_name}, {alert.absences} absences",
        new={"status": alert.status, "note": note},
    )
    return alert
