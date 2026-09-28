from django.db import transaction

from apps.audit import services as audit

from .models import School, SchoolSettings

SCHOOL_FIELDS = [
    "name",
    "registration_number",
    "address",
    "phone",
    "email",
    "website",
    "country",
    "timezone",
    "currency",
    "default_language",
]
SETTINGS_FIELDS = [
    "idle_timeout_minutes",
    "student_number_prefix",
    "employee_number_prefix",
    "invoice_prefix",
    "receipt_prefix",
    "ai_enabled",
]


@transaction.atomic
def create_school(*, name: str, code: str, created_by=None, **fields) -> School:
    from apps.accounts.services import seed_system_roles

    school = School.objects.create(name=name, code=code, **fields)
    SchoolSettings.objects.create(school=school)
    seed_system_roles(school)
    audit.record(
        "create",
        school=school,
        user=created_by,
        instance=school,
        module="schools",
        summary=f"School created: {school.name}",
        new=audit.snapshot(school),
    )
    return school


@transaction.atomic
def update_school(school: School, *, data: dict, settings_data: dict, request=None) -> School:
    school_settings = school.settings
    old = {
        **audit.snapshot(school),
        **{f"settings.{k}": v for k, v in audit.snapshot(school_settings).items()},
    }

    for field, value in data.items():
        setattr(school, field, value)
    school.save()
    for field, value in settings_data.items():
        setattr(school_settings, field, value)
    school_settings.save()

    new = {
        **audit.snapshot(school),
        **{f"settings.{k}": v for k, v in audit.snapshot(school_settings).items()},
    }
    old_changed, new_changed = audit.diff(old, new)
    if new_changed:
        audit.record(
            "update",
            request=request,
            school=school,
            instance=school,
            module="settings",
            summary="School profile updated",
            old=old_changed,
            new=new_changed,
        )
    return school
