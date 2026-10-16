"""What happens in the school, turned into notifications: marks submitted / sent back / published, new
absenteeism alerts, announcements, exams coming up, payments overdue — and, when the school turns them on,
emails to parents about payments and absences."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from apps.core.pdf import format_date
from apps.finance.pdf import format_money

from .messages import render
from .models import Kind, OutboundEmail
from .services import notify, queue_email, staff_user, users_with_permission

EXAM_NOTICE_DAYS = 7
PAYMENT_NOTICE_DAYS = 3
# Installments that fell due this many days ago or less still get their "overdue" email if one was missed.
OVERDUE_WINDOW_DAYS = 7


def _school_settings(school):
    return getattr(school, "settings", None)


# --- Marks --------------------------------------------------------------------------------------------------


def gradebook_changed(gradebook, action: str, actor=None, note: str = "") -> None:
    """After submit / return / publish / reopen (see assessments.services._transition)."""
    class_subject = gradebook.class_subject
    school = gradebook.school
    params = {
        "subject": class_subject.subject.name,
        "class_name": class_subject.class_group.name,
        "term": gradebook.term.name,
        "teacher": class_subject.teacher.full_name if class_subject.teacher else "",
        "reason": note,
    }
    link = f"/assessments/{gradebook.pk}"
    key = f"gradebook:{gradebook.pk}:{action}:{timezone.now():%Y%m%d%H%M%S}"
    teacher = staff_user(class_subject.teacher)
    if action == "submit":
        notify(
            school,
            users_with_permission(school, "grades.publish"),
            Kind.GRADES_SUBMITTED,
            params=params,
            link=link,
            key=key,
            exclude=actor,
        )
    elif action in ("return", "reopen"):
        notify(
            school,
            [teacher],
            Kind.GRADES_SENT_BACK,
            message="grades_sent_back" if action == "return" else "grades_reopened",
            params=params,
            link=link,
            key=key,
            exclude=actor,
        )
    elif action == "publish":
        class_teacher = staff_user(class_subject.class_group.class_teacher)
        notify(
            school,
            [teacher, class_teacher],
            Kind.GRADES_PUBLISHED,
            params=params,
            link=f"/results?term={gradebook.term_id}&class={class_subject.class_group_id}",
            key=key,
            exclude=actor,
        )


# --- Attendance ---------------------------------------------------------------------------------------------


def absence_alerts_raised(school, alerts: list, day: date) -> None:
    """One notification per person for the evening's new alerts: class teachers about their classes, those
    who correct registers (attendance.edit) about the whole school."""
    if not alerts:
        return
    users = {user.pk: user for user in users_with_permission(school, "attendance.edit")}
    counts = dict.fromkeys(users, len(alerts))
    editors = set(users)
    for alert in alerts:
        teacher = staff_user(alert.enrollment.class_group.class_teacher)
        if teacher is None or teacher.pk in editors:
            continue
        users[teacher.pk] = teacher
        counts[teacher.pk] = counts.get(teacher.pk, 0) + 1
    for user_id, user in users.items():
        notify(
            school,
            [user],
            Kind.ABSENCE_ALERT,
            params={"count": counts[user_id]},
            link="/attendance/alerts",
            key=f"absences:{day.isoformat()}",
        )


def parent_absence_emails(school, day: date) -> int:
    """Email the main contact of every student marked absent (not excused) that day."""
    from apps.attendance.models import AttendanceRecord, Status

    settings = _school_settings(school)
    if settings is None or not settings.parent_email_absences:
        return 0
    records = AttendanceRecord.objects.filter(
        register__school=school, register__date=day, status=Status.ABSENT
    ).select_related("enrollment__student", "register__class_group")
    sent = 0
    for record in records:
        student = record.enrollment.student
        to = guardian_email(student, financial=False)
        if not to:
            continue
        subject, message = render(
            "parent_absence",
            school.default_language,
            school=school.name,
            student=student.full_name,
            class_name=record.register.class_group.name,
            date=format_date(day, school),
        )
        if queue_email(
            school,
            to=to,
            kind="parent_absence",
            subject=subject,
            message=message,
            key=f"absence:{record.pk}",
            audience=OutboundEmail.Audience.PARENT,
        ):
            sent += 1
    return sent


# --- Announcements ------------------------------------------------------------------------------------------


def announcement_recipients(announcement) -> list:
    from apps.accounts.models import Membership

    memberships = Membership.objects.filter(
        school=announcement.school, is_active=True, user__is_active=True
    ).select_related("user")
    role_ids = list(announcement.roles.values_list("pk", flat=True))
    if role_ids:
        memberships = memberships.filter(roles__in=role_ids).distinct()
    return [m.user for m in memberships]


def dispatch_announcement(announcement) -> int:
    """Notify the audience of a published announcement, once."""
    from apps.administration.models import Announcement

    if announcement.notified_at is not None or announcement.publish_at > timezone.now():
        return 0
    if not Announcement.objects.filter(pk=announcement.pk, notified_at__isnull=True).update(
        notified_at=timezone.now()
    ):
        return 0  # another worker got there first
    important = announcement.priority == Announcement.Priority.IMPORTANT
    excerpt = announcement.body if len(announcement.body) <= 300 else announcement.body[:297] + "…"
    return notify(
        announcement.school,
        announcement_recipients(announcement),
        Kind.ANNOUNCEMENT_IMPORTANT if important else Kind.ANNOUNCEMENT,
        message="announcement",
        params={"title": announcement.title, "excerpt": excerpt},
        link="/",
        key=f"announcement:{announcement.pk}",
        exclude=announcement.created_by,
    )


def dispatch_due_announcements() -> int:
    from apps.administration.models import Announcement

    due = (
        Announcement.objects.filter(notified_at__isnull=True, publish_at__lte=timezone.now())
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=timezone.localdate()))
        .select_related("school", "created_by")
    )
    return sum(dispatch_announcement(a) for a in due)


# --- Every morning: exams, payments -------------------------------------------------------------------------


def exam_reminders(school, today: date) -> int:
    """A week before an exam, tell the teachers of the levels concerned (or every teacher)."""
    from apps.academics.models import ClassGroup
    from apps.administration.models import CalendarEvent
    from apps.people.models import StaffMember

    events = CalendarEvent.objects.filter(
        school=school,
        kind=CalendarEvent.Kind.EXAM,
        reminded_at__isnull=True,
        start_date__gte=today,
        start_date__lte=today + timedelta(days=EXAM_NOTICE_DAYS),
    ).prefetch_related("levels")
    done = 0
    for event in events:
        levels = list(event.levels.all())
        teachers = StaffMember.objects.filter(school=school, status="active", user__isnull=False)
        if levels:
            classes = ClassGroup.objects.filter(
                school=school,
                level__in=levels,
                academic_year__start_date__lte=event.start_date,
                academic_year__end_date__gte=event.start_date,
            )
            teachers = teachers.filter(
                Q(class_subjects__class_group__in=classes) | Q(homeroom_classes__in=classes)
            )
        else:
            teachers = teachers.filter(staff_type=StaffMember.StaffType.TEACHER)
        users = [s.user for s in teachers.select_related("user").distinct()]

        def params(language, event=event, levels=levels):
            whole = "Tout l'établissement" if language == "fr" else "Whole school"
            return {
                "title": event.title,
                "date": format_date(event.start_date, school),
                "levels": ", ".join(level.name for level in levels) or whole,
            }

        notify(
            school,
            users,
            Kind.EXAM_SOON,
            params=params,
            link="/administration/calendar",
            key=f"exam:{event.pk}",
        )
        event.reminded_at = timezone.now()
        event.save(update_fields=["reminded_at", "updated_at"])
        done += 1
    return done


def guardian_email(student, *, financial: bool) -> str:
    """The parent to write to: the financial (money) or main contact, else any guardian with an email."""
    from apps.people.models import StudentGuardian

    order = (
        ["-is_financial_contact", "-is_primary"] if financial else ["-is_primary", "-is_financial_contact"]
    )
    links = (
        StudentGuardian.objects.filter(student=student).exclude(guardian__email="").select_related("guardian")
    )
    link = links.order_by(*order, "id").first()
    return link.guardian.email if link else ""


def _due_by(invoice, day: date) -> Decimal:
    """What should have been paid on the invoice by `day` and is not."""
    due = sum(
        (line.amount - line.discount for line in invoice.lines.all() if line.due_date <= day), Decimal(0)
    )
    return max(Decimal(0), due - invoice.amount_paid)


def payment_reminders(school, today: date) -> dict[str, int]:
    """Staff: one digest of the invoices that fell overdue. Parents (when enabled): a reminder a few days
    before an installment, and a message once it is overdue."""
    from apps.finance.models import Invoice, InvoiceLine
    from apps.finance.selectors import with_balances

    outcome = {"overdue": 0, "parent_due": 0, "parent_overdue": 0}
    yesterday = today - timedelta(days=1)
    lines = InvoiceLine.objects.filter(
        invoice__school=school,
        invoice__status=Invoice.Status.ISSUED,
        due_date__gte=today - timedelta(days=OVERDUE_WINDOW_DAYS),
        due_date__lte=today + timedelta(days=PAYMENT_NOTICE_DAYS),
    ).values_list("invoice_id", "due_date")
    by_invoice: dict[int, set[date]] = defaultdict(set)
    for invoice_id, due_date in lines:
        by_invoice[invoice_id].add(due_date)
    if not by_invoice:
        return outcome
    invoices = with_balances(Invoice.objects.filter(pk__in=by_invoice), today).select_related("student")
    invoices = invoices.prefetch_related("lines")
    settings = _school_settings(school)
    to_parents = settings is not None and settings.parent_email_payments
    newly_overdue = 0
    for invoice in invoices:
        for due_date in sorted(by_invoice[invoice.pk]):
            amount = _due_by(invoice, due_date)
            if amount <= 0:
                continue
            if due_date == yesterday:
                newly_overdue += 1
            if not to_parents:
                continue
            overdue = due_date < today
            to = guardian_email(invoice.student, financial=True)
            if not to:
                continue
            kind = "parent_payment_overdue" if overdue else "parent_payment_due"
            subject, message = render(
                kind,
                school.default_language,
                school=school.name,
                student=invoice.student.full_name,
                invoice=invoice.number,
                date=format_date(due_date, school),
                amount=format_money(amount, school.currency, school.default_language),
            )
            if queue_email(
                school,
                to=to,
                kind=kind,
                subject=subject,
                message=message,
                key=f"{kind}:{invoice.pk}:{due_date.isoformat()}",
                audience=OutboundEmail.Audience.PARENT,
            ):
                outcome["parent_overdue" if overdue else "parent_due"] += 1
    if newly_overdue:
        notify(
            school,
            users_with_permission(school, "finance.view"),
            Kind.PAYMENTS_OVERDUE,
            params={"count": newly_overdue},
            link="/finance/invoices?status=overdue",
            key=f"overdue:{today.isoformat()}",
        )
        outcome["overdue"] = newly_overdue
    return outcome


def morning_round(today: date | None = None) -> None:
    from apps.attendance.services import school_today
    from apps.schools.models import School

    for school in School.objects.filter(status=School.Status.ACTIVE).select_related("settings"):
        day = today or school_today(school)
        exam_reminders(school, day)
        payment_reminders(school, day)
