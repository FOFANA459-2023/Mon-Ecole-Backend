from datetime import date
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from rest_framework.exceptions import ValidationError

from apps.academics.models import AcademicYear, ClassGroup
from apps.audit import services as audit
from apps.core.sequences import next_number
from apps.enrollments.models import Enrollment
from apps.people.models import Student

from .models import (
    FeeCategory,
    FeeSchedule,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
    StudentDiscount,
)
from .money import ZERO, split, to_money
from .selectors import has_payments, open_lines, with_allocated

NEW_KINDS = {Enrollment.Kind.NEW, Enrollment.Kind.TRANSFER_IN}
INSTALLMENT_LABELS = {"fr": "tranche {n}/{total}", "en": "installment {n} of {total}"}


def _currency(school) -> str:
    return school.currency


def _invoice_prefix(school) -> str:
    settings = getattr(school, "settings", None)
    return getattr(settings, "invoice_prefix", "") or "INV"


def _receipt_prefix(school) -> str:
    settings = getattr(school, "settings", None)
    return getattr(settings, "receipt_prefix", "") or "REC"


def _user(request):
    user = getattr(request, "user", None)
    return user if user is not None and user.is_authenticated else None


# --- Fee schedules and discounts --------------------------------------------------------------------------


def schedules_for(enrollment: Enrollment) -> list[FeeSchedule]:
    """The fee schedules that apply to this enrolment: its year and level, and new or returning students."""
    audience = FeeSchedule.AppliesTo.NEW if enrollment.kind in NEW_KINDS else FeeSchedule.AppliesTo.RETURNING
    return list(
        FeeSchedule.objects.filter(
            school=enrollment.school,
            academic_year=enrollment.academic_year,
            level=enrollment.class_group.level,
            category__is_active=True,
            applies_to__in=[FeeSchedule.AppliesTo.ALL, audience],
        ).select_related("category")
    )


def discount_amount(
    student: Student, academic_year: AcademicYear, category: FeeCategory, gross: Decimal
) -> Decimal:
    """The student's discount on `gross` of one category (a category discount beats an all-category one)."""
    discounts = StudentDiscount.objects.filter(student=student, academic_year=academic_year, is_active=True)
    discount = discounts.filter(category=category).first() or discounts.filter(category__isnull=True).first()
    if discount is None:
        return ZERO
    currency = _currency(student.school)
    if discount.kind == StudentDiscount.Kind.PERCENT:
        return min(to_money(gross * discount.value / 100, currency), gross)
    return min(to_money(discount.value, currency), gross)


def _schedule_lines(enrollment: Enrollment, schedule: FeeSchedule) -> list[dict]:
    currency = _currency(enrollment.school)
    installments = schedule.installments or [
        {"label": "", "due_date": enrollment.enrollment_date.isoformat(), "amount": str(schedule.amount)}
    ]
    amounts = [to_money(Decimal(str(item["amount"])), currency) for item in installments]
    gross = sum(amounts, ZERO)
    discounts = split(
        discount_amount(enrollment.student, enrollment.academic_year, schedule.category, gross),
        amounts,
        currency,
    )
    # Invoices are read by parents: installment labels follow the school's language, not the user's.
    template = INSTALLMENT_LABELS.get(enrollment.school.default_language, INSTALLMENT_LABELS["fr"])
    lines = []
    for index, (item, amount, discount) in enumerate(zip(installments, amounts, discounts, strict=True), 1):
        label = item.get("label") or (
            template.format(n=index, total=len(installments)) if len(installments) > 1 else ""
        )
        lines.append(
            {
                "category": schedule.category,
                "description": f"{schedule.category.name} — {label}" if label else schedule.category.name,
                "due_date": date.fromisoformat(str(item["due_date"])),
                "amount": amount,
                "discount": discount,
            }
        )
    return lines


# --- Invoices ----------------------------------------------------------------------------------------------


def _create_invoice(
    school,
    *,
    student: Student,
    academic_year: AcademicYear,
    lines: list[dict],
    source: str,
    enrollment: Enrollment | None = None,
    issue_date: date | None = None,
    notes: str = "",
    request=None,
) -> Invoice:
    currency = _currency(school)
    issue_date = issue_date or timezone.localdate()
    lines = sorted(lines, key=lambda line: line["due_date"])
    for line in lines:
        line["amount"] = to_money(line["amount"], currency)
        line["discount"] = to_money(line.get("discount") or ZERO, currency)
        if line["discount"] > line["amount"]:
            raise ValidationError({"lines": [_("A discount cannot be larger than the amount.")]})
    subtotal = sum((line["amount"] for line in lines), ZERO)
    discount_total = sum((line["discount"] for line in lines), ZERO)
    invoice = Invoice.objects.create(
        school=school,
        number=next_number(school, "invoice", _invoice_prefix(school), issue_date.year),
        student=student,
        enrollment=enrollment,
        academic_year=academic_year,
        issue_date=issue_date,
        source=source,
        subtotal=subtotal,
        discount_total=discount_total,
        total=subtotal - discount_total,
        notes=notes,
        created_by=getattr(request, "user", None),
    )
    InvoiceLine.objects.bulk_create(
        InvoiceLine(invoice=invoice, order=index, **line) for index, line in enumerate(lines)
    )
    audit.record(
        "create",
        request=request,
        school=school,
        instance=invoice,
        module="finance",
        summary=f"Invoice {invoice.number} issued to {student.full_name}: {invoice.total} {currency}",
        new={"number": invoice.number, "total": str(invoice.total), "source": source, "lines": len(lines)},
    )
    apply_credit(student, request=request)
    return invoice


def enrolment_invoice_exists(student: Student, academic_year: AcademicYear) -> bool:
    return Invoice.objects.filter(
        student=student,
        academic_year=academic_year,
        source=Invoice.Source.ENROLMENT,
        status=Invoice.Status.ISSUED,
    ).exists()


@transaction.atomic
def invoice_enrollment(
    enrollment: Enrollment, *, issue_date: date | None = None, request=None
) -> Invoice | None:
    """Issue the year's fees for an enrolment from its level's fee schedules.

    Returns None when the student already has this year's enrolment invoice (e.g. after a class change)
    or when no fee schedule applies.
    """
    if enrollment.status != Enrollment.Status.ACTIVE:
        return None
    if enrolment_invoice_exists(enrollment.student, enrollment.academic_year):
        return None
    lines = [line for schedule in schedules_for(enrollment) for line in _schedule_lines(enrollment, schedule)]
    if not lines:
        return None
    return _create_invoice(
        enrollment.school,
        student=enrollment.student,
        academic_year=enrollment.academic_year,
        enrollment=enrollment,
        lines=lines,
        source=Invoice.Source.ENROLMENT,
        issue_date=issue_date,
        request=request,
    )


@transaction.atomic
def generate_invoices(
    school, *, academic_year: AcademicYear, class_group: ClassGroup | None = None, request=None
) -> dict:
    """Issue enrolment invoices to every active student of a class (or of the whole year) who has none."""
    enrollments = Enrollment.objects.filter(
        school=school, academic_year=academic_year, status=Enrollment.Status.ACTIVE
    ).select_related("student", "academic_year", "class_group__level", "school")
    if class_group is not None:
        enrollments = enrollments.filter(class_group=class_group)
    created = skipped = without_fees = 0
    for enrollment in enrollments:
        if enrolment_invoice_exists(enrollment.student, enrollment.academic_year):
            skipped += 1
        elif invoice_enrollment(enrollment, request=request):
            created += 1
        else:
            without_fees += 1
    return {"created": created, "skipped": skipped, "without_fees": without_fees}


@transaction.atomic
def create_manual_invoice(
    student: Student,
    *,
    academic_year: AcademicYear,
    lines: list[dict],
    issue_date: date | None = None,
    notes: str = "",
    request=None,
) -> Invoice:
    if not lines:
        raise ValidationError({"lines": [_("Add at least one line.")]})
    if student.school_id != academic_year.school_id:
        raise ValidationError(_("The student and the academic year belong to different schools."))
    return _create_invoice(
        student.school,
        student=student,
        academic_year=academic_year,
        lines=lines,
        source=Invoice.Source.MANUAL,
        issue_date=issue_date,
        notes=notes,
        request=request,
    )


@transaction.atomic
def cancel_invoice(invoice: Invoice, *, reason: str, request=None) -> Invoice:
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Invoice.Status.ISSUED:
        raise ValidationError(_("This invoice is already cancelled."))
    if has_payments(invoice):
        raise ValidationError(_("This invoice has payments. Reverse them before cancelling it."))
    invoice.status = Invoice.Status.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = _user(request)
    invoice.cancel_reason = reason
    invoice.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])
    audit.record(
        "cancel",
        request=request,
        school=invoice.school,
        instance=invoice,
        module="finance",
        summary=f"Invoice {invoice.number} cancelled",
        old={"status": Invoice.Status.ISSUED},
        new={"status": Invoice.Status.CANCELLED, "reason": reason},
    )
    return invoice


def cancel_unpaid_enrolment_invoices(enrollment: Enrollment, *, reason: str, request=None) -> int:
    """When an enrolment made by mistake is cancelled, cancel its fees too (unless something was paid)."""
    count = 0
    invoices = Invoice.objects.filter(
        enrollment=enrollment, status=Invoice.Status.ISSUED, source=Invoice.Source.ENROLMENT
    )
    for invoice in invoices:
        if not has_payments(invoice):
            cancel_invoice(invoice, reason=reason, request=request)
            count += 1
    return count


# --- Payments ----------------------------------------------------------------------------------------------


def _lock_student(student: Student) -> None:
    """Payments and credit of one student are allocated one at a time (row lock on PostgreSQL)."""
    Student.objects.select_for_update().filter(pk=student.pk).first()


def _allocate(payment: Payment, lines: list[Any], available: Decimal) -> list[PaymentAllocation]:
    """Pay `lines` in order with up to `available` of the payment; each line's `balance` is kept current."""
    allocations = []
    for line in lines:
        if available <= 0:
            break
        if line.balance <= 0:
            continue
        amount = min(line.balance, available)
        allocations.append(PaymentAllocation(payment=payment, invoice_line=line, amount=amount))
        line.balance -= amount
        available -= amount
    return PaymentAllocation.objects.bulk_create(allocations)


def _chosen_allocations(
    amount: Decimal, chosen: list[dict], lines: list[Any], currency: str
) -> list[tuple[Any, Decimal]]:
    """Check the lines the user chose to pay: unpaid lines of this student, never more than is due or paid."""
    by_id = {line.pk: line for line in lines}
    wanted: dict[int, Decimal] = {}
    for item in chosen:
        line = item["invoice_line"]
        if line.pk not in by_id:
            raise ValidationError({"allocations": [_("Choose unpaid lines of this student's invoices.")]})
        wanted[line.pk] = wanted.get(line.pk, ZERO) + to_money(item["amount"], currency)
    for pk, value in wanted.items():
        line = by_id[pk]
        if value <= 0:
            raise ValidationError({"allocations": [_("Enter an amount greater than zero.")]})
        if value > line.balance:
            raise ValidationError(
                {
                    "allocations": [
                        _("%(line)s: only %(balance)s is still due.")
                        % {"line": line.description, "balance": line.balance}
                    ]
                }
            )
    if sum(wanted.values(), ZERO) > amount:
        raise ValidationError({"allocations": [_("The lines paid add up to more than the payment.")]})
    return [(line, wanted[line.pk]) for line in lines if line.pk in wanted]


@transaction.atomic
def record_payment(
    student: Student,
    *,
    amount: Decimal,
    method: str,
    payment_date: date | None = None,
    reference: str = "",
    payer_name: str = "",
    note: str = "",
    allocations: list[dict] | None = None,
    request=None,
) -> Payment:
    """Record money received for a student and allocate it to their unpaid invoice lines.

    Without `allocations` ({"invoice_line", "amount"}), the money pays the oldest due lines first. Whatever is
    left is kept as the student's credit.
    """
    school = student.school
    currency = _currency(school)
    _lock_student(student)
    amount = to_money(amount, currency)
    if amount <= 0:
        raise ValidationError({"amount": [_("Enter an amount greater than zero.")]})
    today = timezone.localdate()
    payment_date = payment_date or today
    if payment_date > today:
        raise ValidationError({"date": [_("A payment cannot be dated in the future.")]})

    lines = open_lines(student)
    chosen = None if allocations is None else _chosen_allocations(amount, allocations, lines, currency)
    payment = Payment.objects.create(
        school=school,
        number=next_number(school, "receipt", _receipt_prefix(school), payment_date.year),
        student=student,
        date=payment_date,
        amount=amount,
        method=method,
        reference=reference.strip(),
        payer_name=payer_name.strip(),
        note=note.strip(),
        created_by=_user(request),
    )
    if chosen is None:
        created = _allocate(payment, lines, amount)
    else:
        created = PaymentAllocation.objects.bulk_create(
            PaymentAllocation(payment=payment, invoice_line=line, amount=value) for line, value in chosen
        )
    allocated = sum((allocation.amount for allocation in created), ZERO)
    audit.record(
        "create",
        request=request,
        school=school,
        instance=payment,
        module="finance",
        summary=f"Payment {payment.number} received for {student.full_name}: {amount} {currency}",
        new={
            "number": payment.number,
            "amount": str(amount),
            "method": method,
            "date": payment_date.isoformat(),
            "allocated": str(allocated),
            "credit": str(amount - allocated),
        },
    )
    return payment


@transaction.atomic
def reverse_payment(payment: Payment, *, reason: str, request=None) -> Payment:
    """Void a payment recorded by mistake: it stops paying its invoice lines and its receipt is cancelled.

    Credit the student has from other payments then pays the lines the reversal reopened.
    """
    payment = Payment.objects.select_for_update().select_related("student__school").get(pk=payment.pk)
    if payment.status != Payment.Status.POSTED:
        raise ValidationError(_("This payment is already reversed."))
    payment.status = Payment.Status.REVERSED
    payment.reversed_at = timezone.now()
    payment.reversed_by = _user(request)
    payment.reversal_reason = reason
    payment.save(update_fields=["status", "reversed_at", "reversed_by", "reversal_reason", "updated_at"])
    audit.record(
        "reverse",
        request=request,
        school=payment.school,
        instance=payment,
        module="finance",
        summary=f"Payment {payment.number} reversed: {payment.amount} {_currency(payment.school)}",
        old={"status": Payment.Status.POSTED},
        new={"status": Payment.Status.REVERSED, "reason": reason},
    )
    apply_credit(payment.student, request=request)
    return payment


@transaction.atomic
def apply_credit(student: Student, *, request=None) -> Decimal:
    """Use the student's credit (money paid but not yet allocated) on their unpaid lines, oldest first."""
    _lock_student(student)
    payments = list(
        with_allocated(Payment.objects.filter(student=student, status=Payment.Status.POSTED))
        .filter(unallocated__gt=0)
        .order_by("date", "id")
    )
    if not payments:
        return ZERO
    lines = open_lines(student)
    used = ZERO
    for payment in payments:
        used += sum((a.amount for a in _allocate(payment, lines, payment.unallocated)), ZERO)
    if used:
        audit.record(
            "apply_credit",
            request=request,
            school=student.school,
            instance=student,
            module="finance",
            summary=f"Credit of {used} {_currency(student.school)} used on {student.full_name}'s invoices",
            new={"amount": str(used)},
        )
    return used
