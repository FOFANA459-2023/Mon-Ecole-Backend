from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.db import transaction
from django.utils import translation
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.audit import services as audit
from apps.core.tasks import send_email_task

from .models import Membership, Role
from .permissions_registry import ALL_CODES, SUPER_ADMIN, SYSTEM_ROLES

User = get_user_model()

USER_FIELDS = ["first_name", "last_name", "phone", "language"]


def seed_system_roles(school) -> None:
    """Create or refresh the built-in roles for a school."""
    for key, spec in SYSTEM_ROLES.items():
        Role.objects.update_or_create(
            school=school,
            key=key,
            defaults={
                "name": spec["name"],
                "description": spec["description"],
                "is_system": True,
                "permissions": spec["permissions"],
            },
        )


def revoke_refresh_tokens(user) -> None:
    """Log the user out of every device by blacklisting all their refresh tokens."""
    for token in OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True):
        BlacklistedToken.objects.get_or_create(token=token)


def password_setup_link(user) -> str:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"


def send_password_email(user, *, invitation: bool, school=None) -> None:
    link = password_setup_link(user)
    # Written in the recipient's language, not the language of whoever triggered it.
    with translation.override(user.language):
        if invitation:
            subject = _("Your Mon École account")
            message = _(
                "Hello {name},\n\nAn account has been created for you on Mon École{school}.\n"
                "Choose your password with this link (valid for 3 days):\n{link}\n"
            ).format(name=user.full_name, school=f" ({school.name})" if school else "", link=link)
        else:
            subject = _("Reset your Mon École password")
            message = _(
                "Hello {name},\n\nUse this link to choose a new password (valid for 3 days):\n{link}\n\n"
                "If you did not ask for this, you can ignore this email."
            ).format(name=user.full_name, link=link)
    transaction.on_commit(lambda: send_email_task.delay(user.email, str(subject), str(message)))


def _ensure_super_admin_remains(membership, *, new_roles=None, deactivating=False) -> None:
    """Refuse changes that would leave a school without any active Super Administrator."""
    loses_admin = deactivating or (new_roles is not None and not any(r.key == SUPER_ADMIN for r in new_roles))
    if not loses_admin:
        return
    admins = Membership.objects.filter(
        school=membership.school, is_active=True, roles__key=SUPER_ADMIN
    ).distinct()
    if admins.filter(pk=membership.pk).exists() and not admins.exclude(pk=membership.pk).exists():
        raise ValidationError(_("A school must keep at least one active Super Administrator."))


def _roles_summary(roles) -> list[str]:
    return sorted(r.name for r in roles)


@transaction.atomic
def add_member(
    school,
    *,
    email: str,
    roles: list[Role],
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    language: str = "",
    password: str | None = None,
    request=None,
) -> Membership:
    email = email.strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    created_user = user is None
    if user is None:
        user = User(
            username=email,
            email=email,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            language=language or school.default_language,
        )
        if password:
            user.set_password(password)
            user.must_change_password = True
        else:
            user.set_unusable_password()
        user.save()

    membership, created_membership = Membership.objects.get_or_create(user=user, school=school)
    if not created_membership and membership.is_active:
        raise ValidationError({"email": _("This person already has access to this school.")})
    membership.is_active = True
    membership.save()
    membership.roles.set(roles)

    audit.record(
        "create",
        request=request,
        school=school,
        instance=membership,
        module="users",
        summary=f"Access granted to {user.email}",
        new={"email": user.email, "roles": _roles_summary(roles), "new_account": created_user},
    )
    if created_user and not password:
        send_password_email(user, invitation=True, school=school)
    return membership


@transaction.atomic
def update_member(
    membership: Membership,
    *,
    roles: list[Role] | None = None,
    is_active: bool | None = None,
    user_data: dict | None = None,
    request=None,
) -> Membership:
    acting_user = getattr(request, "user", None)
    if is_active is False and acting_user is not None and membership.user_id == acting_user.pk:
        raise ValidationError({"is_active": _("You cannot remove your own access.")})
    _ensure_super_admin_remains(membership, new_roles=roles, deactivating=is_active is False)

    user = membership.user
    old = {"roles": _roles_summary(membership.roles.all()), "is_active": membership.is_active}
    old.update({f: getattr(user, f) for f in USER_FIELDS})

    if user_data:
        for field, value in user_data.items():
            setattr(user, field, value)
        user.save()
    if roles is not None:
        membership.roles.set(roles)
    if is_active is not None:
        membership.is_active = is_active
        membership.save()

    new = {"roles": _roles_summary(membership.roles.all()), "is_active": membership.is_active}
    new.update({f: getattr(user, f) for f in USER_FIELDS})
    old_changed, new_changed = audit.diff(old, new)
    if new_changed:
        audit.record(
            "update",
            request=request,
            school=membership.school,
            instance=membership,
            module="users",
            summary=f"Access updated for {user.email}",
            old=old_changed,
            new=new_changed,
        )
    return membership


def validate_role_permissions(codes: list[str]) -> list[str]:
    unknown = set(codes) - ALL_CODES
    if unknown:
        raise ValidationError({"permissions": _("Unknown permissions: %s") % ", ".join(sorted(unknown))})
    return sorted(set(codes))


@transaction.atomic
def save_role(school, *, role: Role | None, data: dict, request=None) -> Role:
    if role is not None and role.is_super_admin and "permissions" in data:
        data = {k: v for k, v in data.items() if k != "permissions"}
    if "permissions" in data:
        data["permissions"] = validate_role_permissions(data["permissions"])

    if role is None:
        role = Role.objects.create(school=school, **data)
        audit.record(
            "create",
            request=request,
            instance=role,
            module="users",
            summary=f"Role created: {role.name}",
            new=audit.snapshot(role, ["name", "description", "permissions"]),
        )
        return role

    old = audit.snapshot(role, ["name", "description", "permissions"])
    for field, value in data.items():
        setattr(role, field, value)
    role.save()
    old_changed, new_changed = audit.diff(old, audit.snapshot(role, ["name", "description", "permissions"]))
    if new_changed:
        audit.record(
            "update",
            request=request,
            instance=role,
            module="users",
            summary=f"Role updated: {role.name}",
            old=old_changed,
            new=new_changed,
        )
    return role


@transaction.atomic
def delete_role(role: Role, *, request=None) -> None:
    if role.is_system:
        raise ValidationError(_("Built-in roles cannot be deleted."))
    if role.memberships.filter(is_active=True).exists():
        raise ValidationError(_("Remove this role from all users before deleting it."))
    audit.record(
        "delete",
        request=request,
        instance=role,
        module="users",
        summary=f"Role deleted: {role.name}",
        old=audit.snapshot(role, ["name", "description", "permissions"]),
    )
    role.delete()
