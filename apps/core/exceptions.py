from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import exception_handler


class SchoolRequired(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = _("Select a school to continue.")
    default_code = "school_required"


def _flatten(value, prefix="") -> dict[str, list[str]]:
    if isinstance(value, dict):
        out: dict[str, list[str]] = {}
        for key, sub in value.items():
            out.update(_flatten(sub, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list) and value and isinstance(value[0], dict | list):
        out = {}
        for index, sub in enumerate(value):
            out.update(_flatten(sub, f"{prefix}.{index}"))
        return out
    items = value if isinstance(value, list) else [value]
    return {prefix: [str(item) for item in items]}


def api_exception_handler(exc, context):
    """Return every API error as {code, message, fields} so the frontend has one shape to handle."""
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, ValidationError):
        fields: dict[str, list[str]] = {}
        message = str(_("Please correct the highlighted fields."))
        detail = exc.detail
        if isinstance(detail, dict):
            fields = _flatten(detail)
            non_field = fields.pop("non_field_errors", None)
            if non_field:
                message = non_field[0]
        elif isinstance(detail, list) and detail:
            message = str(detail[0])
        response.data = {"code": "validation_error", "message": message, "fields": fields}
        return response

    data = response.data
    if isinstance(data, dict):
        detail = data.get("detail", "")
        code = data.get("code") or getattr(detail, "code", None) or getattr(exc, "default_code", "error")
    else:
        detail = data
        code = getattr(exc, "default_code", "error")
    response.data = {"code": str(code), "message": str(detail), "fields": {}}
    return response
