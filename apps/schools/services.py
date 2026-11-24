from django.apps import apps
from django.db import transaction
from django.db.models import ProtectedError, RestrictedError

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
    "absence_alert_threshold",
    "absence_alert_days",
    "parent_email_payments",
    "parent_email_absences",
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


def school_models():
    """(model, lookup) for every table that belongs to a school: directly (a `school` foreign key), or
    through a parent that does (payment allocations, invoice lines...)."""
    direct = []
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            if field.is_relation and field.related_model is School:
                direct.append((model, field.name))
    tenant_models = {model for model, _ in direct}
    children = []
    for model in apps.get_models():
        if model in tenant_models or model is School:
            continue
        for field in model._meta.concrete_fields:
            if field.is_relation and field.many_to_one and field.related_model in tenant_models:
                lookup = next(name for m, name in direct if m is field.related_model)
                children.append((model, f"{field.name}__{lookup}"))
    return children + direct


class EraseIncomplete(Exception):
    pass


@transaction.atomic
def erase_school(school: School) -> dict[str, int]:
    """Delete a school and every row that belongs to it. Accounts stay (people may belong to other schools).

    Tenant tables protect each other (payments protect invoices, grades protect enrolments...), so rows are
    deleted in passes until nothing is left; each pass removes whatever no longer has a protector.
    """
    removed: dict[str, int] = {}
    models = school_models()
    for _ in range(40):
        remaining = 0
        for model, field in models:
            rows = model._base_manager.filter(**{field: school})
            if not rows.exists():
                continue
            try:
                with transaction.atomic():
                    _, per_model = rows.delete()
                for label, count in per_model.items():
                    removed[label] = removed.get(label, 0) + count
            except (ProtectedError, RestrictedError):
                remaining += 1
        if remaining == 0:
            break
    else:
        raise EraseIncomplete(f"Could not remove everything from {school.code}.")
    school.delete()
    return removed
