import datetime
import decimal
import uuid

from django.db import models

from apps.core.context import get_request_context

from .models import AuditLog

SENSITIVE_FIELDS = {"password", "last_login"}


def _json_safe(value):
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal | uuid.UUID):
        return str(value)
    if isinstance(value, models.Model):
        return value.pk
    if hasattr(value, "name") and hasattr(value, "storage"):  # FieldFile
        return value.name or None
    return value


def snapshot(instance, fields: list[str] | None = None) -> dict:
    """Plain-JSON copy of a model's concrete field values, for audit old/new comparisons."""
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in SENSITIVE_FIELDS or (fields is not None and field.name not in fields):
            continue
        key = field.attname if isinstance(field, models.ForeignKey) else field.name
        data[field.name] = _json_safe(getattr(instance, key))
    return data


def diff(old: dict, new: dict) -> tuple[dict, dict]:
    changed = {k for k in set(old) | set(new) if not k.endswith("updated_at") and old.get(k) != new.get(k)}
    return {k: old.get(k) for k in changed}, {k: new.get(k) for k in changed}


def record(
    action: str,
    *,
    request=None,
    school=None,
    user=None,
    instance=None,
    module: str = "",
    entity_type: str | None = None,
    entity_id=None,
    summary: str = "",
    old: dict | None = None,
    new: dict | None = None,
) -> AuditLog:
    if request is not None:
        school = school or getattr(request, "school", None)
        request_user = getattr(request, "user", None)
        if user is None and request_user is not None and request_user.is_authenticated:
            user = request_user
    if instance is not None:
        entity_type = entity_type or instance._meta.label_lower
        entity_id = entity_id if entity_id is not None else instance.pk
        module = module or instance._meta.app_label
    ctx = get_request_context()
    return AuditLog.objects.create(
        school=school,
        user=user,
        action=action,
        module=module,
        entity_type=entity_type or "",
        entity_id="" if entity_id is None else str(entity_id),
        summary=summary[:255],
        old_values=old,
        new_values=new,
        ip=ctx.ip,
        user_agent=ctx.user_agent,
        request_id=ctx.request_id if ctx.request_id != "-" else "",
    )
