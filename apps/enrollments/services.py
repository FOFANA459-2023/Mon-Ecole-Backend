from datetime import date

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from apps.academics.models import AcademicYear, ClassGroup
from apps.audit import services as audit
from apps.people import services as people_services
from apps.people.models import Student

from .models import Enrollment

ACTIVE = Enrollment.Status.ACTIVE


def _check_class_open(class_group: ClassGroup) -> None:
    if class_group.status != ClassGroup.Status.ACTIVE:
        raise ValidationError({"class_group": [_("This class is archived.")]})
    if class_group.academic_year.status != AcademicYear.Status.OPEN:
        raise ValidationError({"class_group": [_("This academic year is closed.")]})


def _check_capacity(class_group: ClassGroup, adding: int = 1) -> None:
    if class_group.capacity is None:
        return
    # Lock the class row so two desks cannot fill the last place at the same moment.
    ClassGroup.objects.select_for_update().filter(pk=class_group.pk).first()
    enrolled = Enrollment.objects.filter(class_group=class_group, status=ACTIVE).count()
    if enrolled + adding > class_group.capacity:
        raise ValidationError(
            {
                "class_group": [
                    _("%(name)s is full (%(cap)d places).")
                    % {"name": class_group.name, "cap": class_group.capacity}
                ]
            }
        )


def _record(action: str, enrollment: Enrollment, summary: str, request=None, **values) -> None:
    audit.record(
        action,
        request=request,
        school=enrollment.school,
        instance=enrollment,
        module="enrollments",
        summary=summary,
        **values,
    )


@transaction.atomic
def enroll(
    student: Student,
    class_group: ClassGroup,
    *,
    enrollment_date: date | None = None,
    kind: str = Enrollment.Kind.NEW,
    previous_school: str = "",
    notes: str = "",
    request=None,
) -> Enrollment:
    if student.school_id != class_group.school_id:
        raise ValidationError(_("The student and the class belong to different schools."))
    if student.status != Student.Status.ACTIVE:
        raise ValidationError(_("This student is archived. Restore them before enrolling."))
    _check_class_open(class_group)
    existing = Enrollment.objects.filter(
        student=student, academic_year=class_group.academic_year, status=ACTIVE
    )
    if existing.exists():
        raise ValidationError(
            _("%(student)s is already enrolled for %(year)s.")
            % {"student": student.full_name, "year": class_group.academic_year.name}
        )
    _check_capacity(class_group)
    enrollment = Enrollment.objects.create(
        school=student.school,
        student=student,
        academic_year=class_group.academic_year,
        class_group=class_group,
        enrollment_date=enrollment_date or timezone.localdate(),
        kind=kind,
        previous_school=previous_school,
        notes=notes,
        created_by=getattr(request, "user", None),
    )
    _record(
        "create",
        enrollment,
        f"{student.full_name} enrolled in {class_group.name} ({class_group.academic_year.name})",
        request,
        new={"class": class_group.name, "year": class_group.academic_year.name, "kind": kind},
    )
    return enrollment


@transaction.atomic
def register_student(
    school,
    *,
    class_group: ClassGroup,
    student: Student | None = None,
    student_data: dict | None = None,
    guardians: list[dict] | None = None,
    enrollment_date: date | None = None,
    kind: str = Enrollment.Kind.NEW,
    previous_school: str = "",
    notes: str = "",
    request=None,
) -> Enrollment:
    """The enrolment desk in one step: create (or reuse) the student, link guardians, enrol."""
    if student is None:
        student = people_services.create_student(school, data=student_data or {}, request=request)
    for item in guardians or []:
        people_services.link_guardian(
            student,
            guardian=item.get("guardian"),
            guardian_data=item.get("guardian_data"),
            request=request,
            **item.get("link", {}),
        )
    return enroll(
        student,
        class_group,
        enrollment_date=enrollment_date,
        kind=kind,
        previous_school=previous_school,
        notes=notes,
        request=request,
    )


def _require_active(enrollment: Enrollment) -> None:
    if enrollment.status != ACTIVE:
        raise ValidationError(_("Only an active enrolment can be changed."))


@transaction.atomic
def change_class(
    enrollment: Enrollment, new_class: ClassGroup, *, on: date | None = None, reason: str = "", request=None
) -> Enrollment:
    _require_active(enrollment)
    if new_class.academic_year_id != enrollment.academic_year_id:
        raise ValidationError({"class_group": [_("Choose a class in the same academic year.")]})
    if new_class.pk == enrollment.class_group_id:
        raise ValidationError({"class_group": [_("The student is already in this class.")]})
    _check_class_open(new_class)
    _check_capacity(new_class)
    on = on or timezone.localdate()
    enrollment.status = Enrollment.Status.CLASS_CHANGED
    enrollment.ended_on = on
    enrollment.end_reason = reason
    enrollment.save(update_fields=["status", "ended_on", "end_reason", "updated_at"])
    new = Enrollment.objects.create(
        school=enrollment.school,
        student=enrollment.student,
        academic_year=enrollment.academic_year,
        class_group=new_class,
        enrollment_date=on,
        kind=enrollment.kind,
        previous_school=enrollment.previous_school,
        created_by=getattr(request, "user", None),
    )
    _record(
        "update",
        new,
        f"{enrollment.student.full_name} moved from {enrollment.class_group.name} to {new_class.name}",
        request,
        old={"class": enrollment.class_group.name},
        new={"class": new_class.name, "reason": reason},
    )
    return new


@transaction.atomic
def withdraw(
    enrollment: Enrollment, *, on: date | None = None, reason: str = "", transfer_to: str = "", request=None
) -> Enrollment:
    """The student leaves the school (for another school, or for good)."""
    _require_active(enrollment)
    enrollment.status = Enrollment.Status.WITHDRAWN
    enrollment.ended_on = on or timezone.localdate()
    enrollment.end_reason = reason
    enrollment.transfer_to = transfer_to
    enrollment.save(update_fields=["status", "ended_on", "end_reason", "transfer_to", "updated_at"])
    _record(
        "withdraw",
        enrollment,
        f"{enrollment.student.full_name} left {enrollment.class_group.name}"
        + (f" for {transfer_to}" if transfer_to else ""),
        request,
        new={"reason": reason, "transfer_to": transfer_to, "date": enrollment.ended_on.isoformat()},
    )
    return enrollment


@transaction.atomic
def cancel(enrollment: Enrollment, *, reason: str, request=None) -> Enrollment:
    """Undo an enrolment made by mistake."""
    _require_active(enrollment)
    enrollment.status = Enrollment.Status.CANCELLED
    enrollment.ended_on = timezone.localdate()
    enrollment.end_reason = reason
    enrollment.save(update_fields=["status", "ended_on", "end_reason", "updated_at"])
    _record(
        "cancel",
        enrollment,
        f"Enrolment cancelled: {enrollment.student.full_name} ({enrollment.class_group.name})",
        request,
        new={"reason": reason},
    )
    return enrollment


@transaction.atomic
def promote(
    from_class: ClassGroup,
    to_class: ClassGroup,
    *,
    enrollments: list[Enrollment] | None = None,
    on: date | None = None,
    request=None,
) -> dict:
    """End-of-year re-enrolment: close this year's enrolments and enrol the students in next year's class."""
    if to_class.academic_year.start_date <= from_class.academic_year.start_date:
        raise ValidationError({"to_class": [_("Choose a class in a later academic year.")]})
    _check_class_open(to_class)
    selected = (
        list(enrollments)
        if enrollments is not None
        else list(from_class.enrollments.filter(status=ACTIVE).select_related("student"))
    )
    if any(e.class_group_id != from_class.pk or e.status != ACTIVE for e in selected):
        raise ValidationError(_("Some enrolments are not active in the selected class."))

    already = set(
        Enrollment.objects.filter(
            academic_year=to_class.academic_year, status=ACTIVE, student__in=[e.student for e in selected]
        ).values_list("student_id", flat=True)
    )
    to_move = [e for e in selected if e.student_id not in already]
    _check_capacity(to_class, adding=len(to_move))
    on = on or to_class.academic_year.start_date
    for enrollment in to_move:
        enrollment.status = Enrollment.Status.COMPLETED
        enrollment.ended_on = min(from_class.academic_year.end_date, timezone.localdate())
        enrollment.save(update_fields=["status", "ended_on", "updated_at"])
        Enrollment.objects.create(
            school=from_class.school,
            student=enrollment.student,
            academic_year=to_class.academic_year,
            class_group=to_class,
            enrollment_date=on,
            kind=Enrollment.Kind.RE_ENROLMENT,
            created_by=getattr(request, "user", None),
        )
    audit.record(
        "promote",
        request=request,
        school=from_class.school,
        module="enrollments",
        entity_type="academics.classgroup",
        entity_id=to_class.pk,
        summary=f"{len(to_move)} students re-enrolled from {from_class.name} "
        f"({from_class.academic_year.name}) to {to_class.name} ({to_class.academic_year.name})",
        new={"promoted": len(to_move), "skipped": len(already)},
    )
    return {
        "promoted": len(to_move),
        "skipped": [
            {"student_id": e.student_id, "full_name": e.student.full_name}
            for e in selected
            if e.student_id in already
        ],
    }
