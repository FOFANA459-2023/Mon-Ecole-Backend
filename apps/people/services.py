from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from apps.audit import services as audit
from apps.core.files import IMAGE_TYPES, MAX_PHOTO_BYTES, validate_upload
from apps.core.sequences import next_number

from .models import Guardian, StaffMember, Student, StudentGuardian

STUDENT_FIELDS = [
    "first_name",
    "last_name",
    "gender",
    "date_of_birth",
    "place_of_birth",
    "nationality",
    "phone",
    "email",
    "address",
    "notes",
]
GUARDIAN_FIELDS = ["first_name", "last_name", "phone", "alt_phone", "email", "address", "occupation"]
STAFF_FIELDS = [
    "first_name",
    "last_name",
    "gender",
    "date_of_birth",
    "phone",
    "email",
    "address",
    "staff_type",
    "position",
    "qualification",
    "specialization",
    "employment_date",
]


def _numbering_year(school) -> int:
    from apps.academics.services import current_year

    year = current_year(school)
    return year.start_date.year if year else timezone.localdate().year


def next_student_number(school) -> str:
    return next_number(
        school, "student", school.settings.student_number_prefix, _numbering_year(school), width=5
    )


def next_employee_number(school) -> str:
    return next_number(
        school, "employee", school.settings.employee_number_prefix, _numbering_year(school), width=4
    )


# --- students ---------------------------------------------------------------------------------


@transaction.atomic
def create_student(school, *, data: dict, request=None) -> Student:
    number = (data.pop("student_number", "") or "").strip() or next_student_number(school)
    if Student.objects.filter(school=school, student_number__iexact=number).exists():
        raise ValidationError({"student_number": [_("This student number is already used.")]})
    student = Student.objects.create(
        school=school, student_number=number, created_by=getattr(request, "user", None), **data
    )
    audit.record(
        "create",
        request=request,
        school=school,
        instance=student,
        module="students",
        summary=f"Student created: {student.full_name} ({student.student_number})",
        new=audit.snapshot(student, ["student_number", *STUDENT_FIELDS]),
    )
    return student


@transaction.atomic
def update_student(student: Student, *, data: dict, request=None) -> Student:
    old = audit.snapshot(student, STUDENT_FIELDS)
    for field, value in data.items():
        setattr(student, field, value)
    student.save()
    old_changed, new_changed = audit.diff(old, audit.snapshot(student, STUDENT_FIELDS))
    if new_changed:
        audit.record(
            "update",
            request=request,
            instance=student,
            module="students",
            summary=f"Student updated: {student.full_name}",
            old=old_changed,
            new=new_changed,
        )
    return student


@transaction.atomic
def archive_student(student: Student, *, request=None) -> Student:
    if student.enrollments.filter(status="active").exists():
        raise ValidationError(_("This student is still enrolled in a class. Withdraw them first."))
    student.status = Student.Status.ARCHIVED
    student.archived_at = timezone.now()
    student.save(update_fields=["status", "archived_at", "updated_at"])
    audit.record(
        "archive",
        request=request,
        instance=student,
        module="students",
        summary=f"Student archived: {student.full_name}",
    )
    return student


@transaction.atomic
def restore_student(student: Student, *, request=None) -> Student:
    student.status = Student.Status.ACTIVE
    student.archived_at = None
    student.save(update_fields=["status", "archived_at", "updated_at"])
    audit.record(
        "restore",
        request=request,
        instance=student,
        module="students",
        summary=f"Student restored: {student.full_name}",
    )
    return student


def set_photo(instance, file, *, module: str, request=None):
    validate_upload(file, allowed_types=IMAGE_TYPES, max_bytes=MAX_PHOTO_BYTES)
    if instance.photo:
        instance.photo.delete(save=False)
    instance.photo = file
    instance.save(update_fields=["photo", "updated_at"])
    audit.record(
        "update",
        request=request,
        instance=instance,
        module=module,
        summary=f"Photo updated: {instance.full_name}",
    )
    return instance


# --- guardians --------------------------------------------------------------------------------


def _unset_other_primaries(link: StudentGuardian) -> None:
    if link.is_primary:
        StudentGuardian.objects.filter(student=link.student, is_primary=True).exclude(pk=link.pk).update(
            is_primary=False
        )


@transaction.atomic
def link_guardian(
    student: Student,
    *,
    guardian: Guardian | None = None,
    guardian_data: dict | None = None,
    relationship: str = StudentGuardian.Relationship.GUARDIAN,
    is_primary: bool = False,
    is_financial_contact: bool = False,
    request=None,
) -> StudentGuardian:
    school = student.school
    if guardian is None:
        guardian = Guardian.objects.create(
            school=school, created_by=getattr(request, "user", None), **(guardian_data or {})
        )
    if StudentGuardian.objects.filter(student=student, guardian=guardian).exists():
        raise ValidationError(_("This guardian is already linked to the student."))
    first_link = not student.guardian_links.exists()
    link = StudentGuardian.objects.create(
        school=school,
        student=student,
        guardian=guardian,
        relationship=relationship,
        is_primary=is_primary or first_link,
        is_financial_contact=is_financial_contact or first_link,
        created_by=getattr(request, "user", None),
    )
    _unset_other_primaries(link)
    audit.record(
        "create",
        request=request,
        school=school,
        instance=link,
        module="students",
        summary=f"Guardian {guardian.full_name} linked to {student.full_name} ({relationship})",
    )
    return link


@transaction.atomic
def update_guardian_link(
    link: StudentGuardian, *, data: dict, guardian_data: dict, request=None
) -> StudentGuardian:
    guardian = link.guardian
    old = {
        **audit.snapshot(link, ["relationship", "is_primary", "is_financial_contact"]),
        **audit.snapshot(guardian, GUARDIAN_FIELDS),
    }
    for field, value in data.items():
        setattr(link, field, value)
    link.save()
    _unset_other_primaries(link)
    for field, value in guardian_data.items():
        setattr(guardian, field, value)
    guardian.save()
    new = {
        **audit.snapshot(link, ["relationship", "is_primary", "is_financial_contact"]),
        **audit.snapshot(guardian, GUARDIAN_FIELDS),
    }
    old_changed, new_changed = audit.diff(old, new)
    if new_changed:
        audit.record(
            "update",
            request=request,
            instance=link,
            module="students",
            summary=f"Guardian updated: {guardian.full_name}",
            old=old_changed,
            new=new_changed,
        )
    return link


@transaction.atomic
def unlink_guardian(link: StudentGuardian, *, request=None) -> None:
    audit.record(
        "delete",
        request=request,
        instance=link,
        module="students",
        summary=f"Guardian {link.guardian.full_name} unlinked from {link.student.full_name}",
    )
    student = link.student
    was_primary = link.is_primary
    link.delete()
    if was_primary:
        replacement = student.guardian_links.order_by("id").first()
        if replacement:
            replacement.is_primary = True
            replacement.save(update_fields=["is_primary", "updated_at"])


# --- staff ------------------------------------------------------------------------------------


@transaction.atomic
def create_staff(school, *, data: dict, request=None) -> StaffMember:
    number = (data.pop("employee_number", "") or "").strip() or next_employee_number(school)
    if StaffMember.objects.filter(school=school, employee_number__iexact=number).exists():
        raise ValidationError({"employee_number": [_("This staff number is already used.")]})
    staff = StaffMember.objects.create(
        school=school, employee_number=number, created_by=getattr(request, "user", None), **data
    )
    audit.record(
        "create",
        request=request,
        school=school,
        instance=staff,
        module="staff",
        summary=f"Staff member added: {staff.full_name} ({staff.employee_number})",
        new=audit.snapshot(staff, ["employee_number", *STAFF_FIELDS]),
    )
    return staff


@transaction.atomic
def update_staff(staff: StaffMember, *, data: dict, request=None) -> StaffMember:
    old = audit.snapshot(staff, STAFF_FIELDS)
    for field, value in data.items():
        setattr(staff, field, value)
    staff.save()
    old_changed, new_changed = audit.diff(old, audit.snapshot(staff, STAFF_FIELDS))
    if new_changed:
        audit.record(
            "update",
            request=request,
            instance=staff,
            module="staff",
            summary=f"Staff member updated: {staff.full_name}",
            old=old_changed,
            new=new_changed,
        )
    return staff


@transaction.atomic
def set_staff_status(staff: StaffMember, *, archived: bool, request=None) -> StaffMember:
    staff.status = StaffMember.Status.ARCHIVED if archived else StaffMember.Status.ACTIVE
    staff.archived_at = timezone.now() if archived else None
    staff.save(update_fields=["status", "archived_at", "updated_at"])
    audit.record(
        "archive" if archived else "restore",
        request=request,
        instance=staff,
        module="staff",
        summary=f"Staff member {'archived' if archived else 'restored'}: {staff.full_name}",
    )
    return staff


@transaction.atomic
def grant_staff_access(staff: StaffMember, *, roles, request=None) -> StaffMember:
    """Give a staff member a login for this school (invitation email) and link the two records."""
    from apps.accounts.models import Membership
    from apps.accounts.services import add_member, update_member

    if not staff.email:
        raise ValidationError({"email": [_("Add an email address to this staff member first.")]})
    existing = Membership.objects.filter(school=staff.school, user__email__iexact=staff.email).first()
    if existing is None or not existing.is_active:
        membership = add_member(
            staff.school,
            email=staff.email,
            first_name=staff.first_name,
            last_name=staff.last_name,
            phone=staff.phone,
            roles=roles,
            request=request,
        )
    else:
        membership = update_member(existing, roles=roles, request=request)
    staff.user = membership.user
    staff.save(update_fields=["user", "updated_at"])
    return staff


@transaction.atomic
def set_teaching(
    staff: StaffMember, *, homeroom_classes: list, subjects: list[dict], request=None
) -> StaffMember:
    """Replace what a teacher does this school year: the classes they lead and the subjects they teach in
    each class. A class subject that does not exist yet is created with the subject's default coefficient.
    Another teacher currently on one of these posts is replaced."""
    from apps.academics.models import ClassGroup, ClassSubject
    from apps.academics.services import current_year

    year = current_year(staff.school)
    classes = [c for c in homeroom_classes] + [item["class_group"] for item in subjects]
    if any(c.school_id != staff.school_id for c in classes) or any(
        item["subject"].school_id != staff.school_id for item in subjects
    ):
        raise ValidationError(_("Classes and subjects must belong to this school."))
    if year is not None and any(c.academic_year_id != year.pk for c in classes):
        raise ValidationError(_("Choose classes of the current school year."))

    old = _teaching_summary(staff, year)
    wanted = {(item["class_group"].pk, item["subject"].pk) for item in subjects}
    current = ClassSubject.objects.filter(teacher=staff)
    if year is not None:
        current = current.filter(class_group__academic_year=year)
    for class_subject in current:
        if (class_subject.class_group_id, class_subject.subject_id) not in wanted:
            class_subject.teacher = None
            class_subject.save(update_fields=["teacher", "updated_at"])
    for item in subjects:
        class_subject, _created = ClassSubject.objects.get_or_create(
            class_group=item["class_group"],
            subject=item["subject"],
            defaults={
                "school": staff.school,
                "coefficient": item["subject"].default_coefficient,
                "created_by": getattr(request, "user", None),
            },
        )
        if class_subject.teacher_id != staff.pk:
            class_subject.teacher = staff
            class_subject.save(update_fields=["teacher", "updated_at"])

    homeroom_ids = {c.pk for c in homeroom_classes}
    led = ClassGroup.objects.filter(class_teacher=staff)
    if year is not None:
        led = led.filter(academic_year=year)
    led.exclude(pk__in=homeroom_ids).update(class_teacher=None)
    ClassGroup.objects.filter(pk__in=homeroom_ids).update(class_teacher=staff)

    new = _teaching_summary(staff, year)
    if new != old:
        audit.record(
            "update",
            request=request,
            instance=staff,
            module="staff",
            summary=f"Teaching updated: {staff.full_name}",
            old=old,
            new=new,
        )
    return staff


def _teaching_summary(staff: StaffMember, year) -> dict:
    from apps.academics.models import ClassGroup, ClassSubject

    subjects = ClassSubject.objects.filter(teacher=staff).select_related("class_group", "subject")
    homerooms = ClassGroup.objects.filter(class_teacher=staff)
    if year is not None:
        subjects = subjects.filter(class_group__academic_year=year)
        homerooms = homerooms.filter(academic_year=year)
    return {
        "homeroom": sorted(c.name for c in homerooms),
        "subjects": sorted(f"{cs.subject.name} — {cs.class_group.name}" for cs in subjects),
    }


@transaction.atomic
def add_staff_with_access(
    school, *, data: dict, role, homeroom_classes: list, subjects: list[dict], request=None
) -> StaffMember:
    """The Director's "add a staff member": the staff record, their login (an invitation email with a
    verification link and temporary password) with the chosen role, and for teachers what they teach."""
    staff = create_staff(school, data=data, request=request)
    grant_staff_access(staff, roles=[role], request=request)
    if homeroom_classes or subjects:
        set_teaching(staff, homeroom_classes=homeroom_classes, subjects=subjects, request=request)
    return staff
