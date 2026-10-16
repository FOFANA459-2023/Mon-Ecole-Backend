"""Sending notifications: in-app rows, staff emails they asked for, and parent emails.

`notify()` is the one entry point for staff. Each kind has a default (in the app: always; by email: only for
what needs acting on); a user's own `NotificationPreference` overrides it. Emails are logged in
`OutboundEmail` and sent by Celery after the transaction commits.
"""

import logging
from collections.abc import Callable, Iterable

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .messages import render
from .models import Kind, Notification, NotificationPreference, OutboundEmail

logger = logging.getLogger(__name__)

# (in the app, by email) when the user has not chosen.
DEFAULTS: dict[str, tuple[bool, bool]] = {
    Kind.ANNOUNCEMENT: (True, False),
    Kind.ANNOUNCEMENT_IMPORTANT: (True, True),
    Kind.GRADES_SUBMITTED: (True, False),
    Kind.GRADES_SENT_BACK: (True, True),
    Kind.GRADES_PUBLISHED: (True, False),
    Kind.ABSENCE_ALERT: (True, False),
    Kind.PAYMENTS_OVERDUE: (True, False),
    Kind.EXAM_SOON: (True, False),
}


def preferences(user) -> dict[str, tuple[bool, bool]]:
    chosen = {p.kind: (p.in_app, p.email) for p in NotificationPreference.objects.filter(user=user)}
    return {kind: chosen.get(kind, default) for kind, default in DEFAULTS.items()}


def _language(user, school) -> str:
    language = getattr(user, "language", "") or school.default_language
    return language if language in ("fr", "en") else "fr"


def notify(
    school,
    users: Iterable,
    kind: str,
    *,
    message: str | None = None,
    params: Callable[[str], dict] | dict | None = None,
    link: str = "",
    key: str = "",
    exclude=None,
) -> int:
    """Notify each user (once, active accounts only) of an event. `message` names the text in messages.py
    (default: the kind); `params` fills it, or is called with the language when the values depend on it.
    Returns how many in-app notifications were created."""
    message = message or kind
    created = 0
    seen: set[int] = set()
    for user in users:
        if (
            user is None
            or user.pk in seen
            or not user.is_active
            or (exclude is not None and user.pk == exclude.pk)
        ):
            continue
        seen.add(user.pk)
        in_app, by_email = preferences(user)[kind]
        language = _language(user, school)
        values = params(language) if callable(params) else (params or {})
        title, body = render(message, language, **values)
        if in_app:
            try:
                with transaction.atomic():
                    Notification.objects.create(
                        school=school, user=user, kind=kind, title=title, body=body[:500], link=link, key=key
                    )
                created += 1
            except IntegrityError:  # already notified of this event
                continue
        if by_email and user.email:
            url = f"{settings.FRONTEND_URL.rstrip('/')}{link}" if link else settings.FRONTEND_URL
            queue_email(
                school,
                to=user.email,
                kind=kind,
                subject=title,
                message=f"{body}\n\n{url}",
                key=f"{key}:{user.pk}" if key else "",
            )
    return created


def monthly_count(now=None) -> int:
    now = now or timezone.now()
    return OutboundEmail.objects.filter(
        status=OutboundEmail.Status.SENT, sent_at__year=now.year, sent_at__month=now.month
    ).count()


def queue_email(
    school,
    *,
    to: str,
    kind: str,
    subject: str,
    message: str,
    key: str = "",
    audience: str = OutboundEmail.Audience.STAFF,
) -> OutboundEmail | None:
    """Log the email and send it once the transaction commits. A key already used is not sent again."""
    if key and OutboundEmail.objects.filter(school=school, key=key).exists():
        return None
    try:
        with transaction.atomic():
            email = OutboundEmail.objects.create(
                school=school,
                audience=audience,
                kind=kind,
                to=to,
                subject=subject[:200],
                message=message,
                key=key,
            )
    except IntegrityError:
        return None
    from .tasks import send_outbound_email

    transaction.on_commit(lambda: send_outbound_email.delay(email.pk))
    return email


def send(email_id: int) -> None:
    """Send a logged email, unless this month's EmailJS allowance is used up."""
    from apps.core.email import send_email

    email = OutboundEmail.objects.filter(pk=email_id, status=OutboundEmail.Status.QUEUED).first()
    if email is None:
        return
    limit = settings.EMAILJS_MONTHLY_LIMIT
    sent = monthly_count()
    if limit and sent >= limit:
        email.status, email.error = OutboundEmail.Status.SKIPPED, "Monthly email limit reached"
        email.save(update_fields=["status", "error", "updated_at"])
        logger.warning("Email %s skipped: the monthly limit of %s emails is reached", email.pk, limit)
        return
    if limit and sent + 1 == int(limit * 0.8):
        logger.warning("80 %% of the monthly email limit (%s) is used", limit)
    try:
        send_email(to=email.to, subject=email.subject, message=email.message)
    except Exception as error:  # the provider's message, without secrets
        email.status, email.error = OutboundEmail.Status.FAILED, str(error)[:255]
        email.save(update_fields=["status", "error", "updated_at"])
        logger.exception("Email %s failed", email.pk)
        return
    email.status, email.sent_at = OutboundEmail.Status.SENT, timezone.now()
    email.save(update_fields=["status", "sent_at", "updated_at"])


# --- Who to tell --------------------------------------------------------------------------------------------


def users_with_permission(school, code: str) -> list:
    """Active members of the school holding a permission (directors hold them all)."""
    from apps.accounts.models import Membership

    memberships = (
        Membership.objects.filter(school=school, is_active=True, user__is_active=True)
        .select_related("user")
        .prefetch_related("roles")
    )
    return [m.user for m in memberships if code in m.permission_codes()]


def staff_user(staff):
    return staff.user if staff is not None and staff.user_id else None
