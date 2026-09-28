from rest_framework.permissions import BasePermission

from .tenancy import resolve_school_access


def required_permissions_for(view, request) -> list[str] | None:
    """Look up the permission codes a view declares for the current action (or HTTP method).

    Views declare e.g. ``required_permissions = {"list": ["students.view"], "create": [...]}``.
    An empty list means "any member of the school"; a missing entry means "denied".
    """
    mapping = getattr(view, "required_permissions", None)
    if mapping is None:
        return None
    action = getattr(view, "action", None) or request.method.lower()
    if action in mapping:
        return mapping[action]
    return mapping.get("*")


class HasSchoolPermission(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        access = resolve_school_access(request)
        required = required_permissions_for(view, request)
        if required is None:
            return False
        return all(code in access.permissions for code in required)
