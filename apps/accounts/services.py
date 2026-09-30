import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.db import transaction
from django.utils import timezone, translation
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.audit import services as audit
from apps.core.tasks import send_email_task

from .models import Membership, Role
from .permissions_registry import ALL_CODES, DIRECTOR, SYSTEM_ROLES

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


def send_password_email(user) -> None:
    """ "Forgot password": a link to choose a new password."""
    link = password_setup_link(user)
    # Written in the recipient's language, not the language of whoever triggered it.
    with translation.override(user.language):
        subject = _("Reset your Mon École password")
        message = _(
            "Hello {name},\n\nUse this link to choose a new password (valid for 3 days):\n{link}\n\n"
            "If you did not ask for this, you can ignore this email."
        ).format(name=user.full_name, link=link)
    transaction.on_commit(lambda: send_email_task.delay(user.email, str(subject), str(message)))


# --- invitations: verification link + temporary password ------------------------------------------

INVITATION_VALIDITY = timedelta(days=7)
_VERIFY_SALT = "accounts.verify-email"
# No look-alike characters (0/O, 1/l/I): the password is read from an email and typed by hand.
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"


def generate_temporary_password() -> str:
    """e.g. "Kp7m-X2qr-9Tzw": 12 random characters, easy to copy."""
    return "-".join("".join(secrets.choice(_TEMP_ALPHABET) for _ in range(4)) for _ in range(3))


def _verification_fingerprint(user) -> str:
    # Tied to the current password hash: a new invitation (new temporary password) voids older links.
    return salted_hmac(_VERIFY_SALT, f"{user.pk}:{user.email}:{user.password}").hexdigest()[:24]


def email_verification_token(user) -> str:
    return signing.dumps({"u": user.pk, "f": _verification_fingerprint(user)}, salt=_VERIFY_SALT)


def user_for_verification_token(token: str):
    """The user a verification link belongs to, or None if it is invalid, replaced or older than 7 days."""
    try:
        data = signing.loads(token, salt=_VERIFY_SALT, max_age=INVITATION_VALIDITY)
    except signing.BadSignature:
        return None
    user = User.objects.filter(pk=data.get("u"), is_active=True).first()
    if user is None or not constant_time_compare(str(data.get("f", "")), _verification_fingerprint(user)):
        return None
    return user


def is_activated(user) -> bool:
    """The person confirmed their email and replaced the temporary password with their own."""
    return user.email_verified_at is not None and not user.must_change_password


def invitation_status(user) -> str:
    if is_activated(user):
        return "active"
    if user.invitation_expires_at and user.invitation_expires_at < timezone.now():
        return "expired"
    return "pending"


def send_invitation(user, *, school=None) -> None:
    """(Re)issue a temporary password and email it with the verification link. Older links and temporary
    passwords stop working, and the person is signed out everywhere."""
    temporary = generate_temporary_password()
    user.set_password(temporary)
    user.must_change_password = True
    user.email_verified_at = None
    user.invitation_expires_at = timezone.now() + INVITATION_VALIDITY
    user.save(
        update_fields=["password", "must_change_password", "email_verified_at", "invitation_expires_at"]
    )
    revoke_refresh_tokens(user)
    link = f"{settings.FRONTEND_URL}/verify-email?token={email_verification_token(user)}"
    with translation.override(user.language):
        subject = _("Your Mon École account")
        message = _(
            "Hello {name},\n\n"
            "An account has been created for you on Mon École{school}.\n\n"
            "1. Confirm your email address with this link:\n{link}\n\n"
            "2. Then sign in with:\n"
            "   Email: {email}\n"
            "   Temporary password: {password}\n\n"
            "You will then choose your own password. The link and the temporary password are valid "
            "for 7 days.\n"
        ).format(
            name=user.full_name,
            school=f" ({school.name})" if school else "",
            link=link,
            email=user.email,
            password=temporary,
        )
    transaction.on_commit(lambda: send_email_task.delay(user.email, str(subject), str(message)))


def send_access_notice(user, *, school) -> None:
    """An account that is already active was given access to one more school."""
    with translation.override(user.language):
        subject = _("New access on Mon École")
        message = _(
            "Hello {name},\n\nYou now have access to {school} on Mon École. "
            "Sign in with your usual email and password: {url}\n"
        ).format(name=user.full_name, school=school.name, url=settings.FRONTEND_URL)
    transaction.on_commit(lambda: send_email_task.delay(user.email, str(subject), str(message)))


def resend_invitation(membership, *, request=None) -> None:
    user = membership.user
    if is_activated(user):
        raise ValidationError(
            _("This person has already activated their account. They can use Forgot password if needed.")
        )
    send_invitation(user, school=membership.school)
    audit.record(
        "invite_sent",
        request=request,
        school=membership.school,
        instance=membership,
        module="users",
        summary=f"Invitation sent to {user.email}",
    )


# --- directors -------------------------------------------------------------------------------------


def _acting_as_owner(request) -> bool:
    """The platform owner, or the system itself (management commands: no request)."""
    user = getattr(request, "user", None)
    return request is None or bool(user is not None and user.is_superuser)


def _check_director_change(membership, *, new_roles=None, deactivating=False, request=None) -> None:
    """Only the platform owner grants or removes the Director role (or a Director's access), and a school
    always keeps at least one active Director."""
    is_director = membership.roles.filter(key=DIRECTOR).exists()
    becomes_director = any(r.key == DIRECTOR for r in new_roles) if new_roles is not None else is_director
    touches_director = is_director != becomes_director or (is_director and deactivating)
    if touches_director and not _acting_as_owner(request):
        raise ValidationError(
            {"role_ids": [_("Only the platform owner can grant or remove the Director role.")]}
        )
    if is_director and (deactivating or not becomes_director):
        directors = Membership.objects.filter(
            school=membership.school, is_active=True, roles__key=DIRECTOR
        ).distinct()
        if not directors.exclude(pk=membership.pk).exists():
            raise ValidationError(_("A school must keep at least one active Director."))


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
    request=None,
) -> Membership:
    email = email.strip().lower()
    if any(r.key == DIRECTOR for r in roles) and not _acting_as_owner(request):
        raise ValidationError(
            {"role_ids": [_("Only the platform owner can grant or remove the Director role.")]}
        )
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
    if created_user or not is_activated(user):
        send_invitation(user, school=school)
    else:
        send_access_notice(user, school=school)
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
    _check_director_change(membership, new_roles=roles, deactivating=is_active is False, request=request)

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
    if role is not None and role.is_director and "permissions" in data:
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
