from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotFound

from .exceptions import SchoolRequired

SCHOOL_HEADER = "X-School-ID"


@dataclass(frozen=True)
class SchoolAccess:
    school: object
    membership: object | None
    permissions: frozenset[str]


def resolve_school_access(request) -> SchoolAccess:
    """Work out which school this request acts on and what the user may do there.

    The school comes from the X-School-ID header and must match one of the user's active
    memberships. Anything else is reported as "not found" so other schools' existence never leaks.
    """
    cached = getattr(request, "_school_access", None)
    if cached is not None:
        return cached

    from apps.accounts.models import Membership
    from apps.accounts.permissions_registry import ALL_CODES
    from apps.schools.models import School

    user = request.user
    memberships = (
        Membership.objects.filter(user=user, is_active=True, school__status=School.Status.ACTIVE)
        .select_related("school")
        .prefetch_related("roles")
    )
    raw = request.headers.get(SCHOOL_HEADER, "").strip()

    if raw:
        if not raw.isdigit():
            raise NotFound(_("School not found."))
        membership = memberships.filter(school_id=int(raw)).first()
        if membership is not None:
            access = SchoolAccess(membership.school, membership, membership.permission_codes())
        elif user.is_superuser:
            school = School.objects.filter(pk=int(raw), status=School.Status.ACTIVE).first()
            if school is None:
                raise NotFound(_("School not found."))
            access = SchoolAccess(school, None, ALL_CODES)
        else:
            raise NotFound(_("School not found."))
    else:
        candidates = list(memberships[:2])
        if len(candidates) != 1:
            raise SchoolRequired()
        membership = candidates[0]
        access = SchoolAccess(membership.school, membership, membership.permission_codes())

    request._school_access = access
    request.school = access.school
    request.membership = access.membership
    request.permission_codes = access.permissions
    return access
