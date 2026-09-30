from datetime import date
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.academics.models import AcademicYear, ClassGroup, Level
from apps.cashregister.models import CashSession
from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.people.models import Student

from .models import (
    MONEY_DIGITS,
    MONEY_PLACES,
    Expense,
    FeeCategory,
    FeeSchedule,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
    Refund,
    StudentDiscount,
)
from .money import ZERO, to_money
from .selectors import PaymentStatus

CASH_SESSION_HELP = (
    "Cash only: the open session the money goes through. Leave it out when one register is open."
)


def _currency(serializer) -> str:
    return serializer.context["request"].school.currency


class FeeCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeCategory
        fields = ["id", "name", "kind", "description", "order", "is_active"]

    def validate_name(self, value):
        school = self.context["request"].school
        clash = FeeCategory.objects.filter(school=school, name__iexact=value.strip())
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("This fee category already exists."))
        return value.strip()


class InstallmentSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")  # type: ignore[assignment]  # "label" is also a Field attribute
    due_date = serializers.DateField()
    amount = serializers.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, min_value=Decimal("0.01")
    )


class FeeScheduleSerializer(serializers.ModelSerializer):
    academic_year = TenantPrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    level = TenantPrimaryKeyRelatedField(queryset=Level.objects.all())
    category = TenantPrimaryKeyRelatedField(queryset=FeeCategory.objects.all())
    installments = InstallmentSerializer(many=True, required=False)
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    level_name = serializers.CharField(source="level.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = FeeSchedule
        fields = [
            "id",
            "academic_year",
            "academic_year_name",
            "level",
            "level_name",
            "category",
            "category_name",
            "applies_to",
            "amount",
            "installments",
        ]

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        year, level, category, applies_to = (
            get("academic_year"),
            get("level"),
            get("category"),
            get("applies_to"),
        )
        currency = _currency(self)
        amount = to_money(get("amount"), currency)
        attrs["amount"] = amount

        clash = FeeSchedule.objects.filter(
            academic_year=year,
            level=level,
            category=category,
            applies_to=applies_to or FeeSchedule.AppliesTo.ALL,
        )
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("This level already has this fee for this year."))

        if "installments" in attrs or self.instance is None:
            items = attrs.get("installments") or [
                {"label": "", "due_date": year.start_date, "amount": amount}
            ]
        elif "amount" in attrs:
            items = [
                dict(item, due_date=date.fromisoformat(item["due_date"]))
                for item in self.instance.installments
            ]
        else:
            items = None
        if items is not None:
            if len(items) > 12:
                raise serializers.ValidationError({"installments": [_("Use at most 12 installments.")]})
            items = sorted(items, key=lambda item: item["due_date"])
            total = sum((to_money(item["amount"], currency) for item in items), ZERO)
            if total != amount:
                raise serializers.ValidationError(
                    {
                        "installments": [
                            _("The installments must add up to the fee amount (%(amount)s).")
                            % {"amount": amount}
                        ]
                    }
                )
            attrs["installments"] = [
                {
                    "label": item.get("label", ""),
                    "due_date": item["due_date"].isoformat(),
                    "amount": str(to_money(item["amount"], currency)),
                }
                for item in items
            ]
        return attrs


class StudentDiscountSerializer(serializers.ModelSerializer):
    student = TenantPrimaryKeyRelatedField(queryset=Student.objects.all())
    academic_year = TenantPrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    category = TenantPrimaryKeyRelatedField(
        queryset=FeeCategory.objects.all(), allow_null=True, required=False
    )
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = StudentDiscount
        fields = [
            "id",
            "student",
            "student_name",
            "student_number",
            "academic_year",
            "academic_year_name",
            "category",
            "category_name",
            "kind",
            "value",
            "reason",
            "note",
            "is_active",
        ]

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        kind, value, category = get("kind") or StudentDiscount.Kind.PERCENT, get("value"), get("category")
        if kind == StudentDiscount.Kind.PERCENT and value is not None and value > 100:
            raise serializers.ValidationError({"value": [_("A percentage cannot be more than 100.")]})
        if kind == StudentDiscount.Kind.FIXED:
            if category is None:
                raise serializers.ValidationError(
                    {"category": [_("Choose the fee category a fixed discount applies to.")]}
                )
            attrs["value"] = to_money(value, _currency(self))
        if get("is_active") is not False:
            clash = StudentDiscount.objects.filter(
                student=get("student"), academic_year=get("academic_year"), is_active=True
            )
            clash = clash.filter(category=category) if category else clash.filter(category__isnull=True)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError(
                    _("This student already has an active discount for this fee in this year.")
                )
        return attrs


def _money_field(**kwargs):
    return serializers.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, **kwargs)


class InvoiceLineSerializer(serializers.ModelSerializer):
    """A line with what is paid on it (the queryset is annotated by selectors.with_line_balances)."""

    category_name = serializers.CharField(source="category.name", read_only=True)
    net = _money_field(read_only=True)
    paid = _money_field(read_only=True)
    balance = _money_field(read_only=True)

    class Meta:
        model = InvoiceLine
        fields = [
            "id",
            "category",
            "category_name",
            "description",
            "due_date",
            "amount",
            "discount",
            "net",
            "paid",
            "balance",
        ]


class InvoicePaymentSerializer(serializers.Serializer):
    """A payment as seen from one invoice: how much of it went to this invoice."""

    id = serializers.IntegerField()
    number = serializers.CharField()
    date = serializers.DateField()
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    status = serializers.ChoiceField(choices=Payment.Status.choices)
    amount = _money_field(help_text="The part of the payment allocated to this invoice.")


class InvoiceSerializer(serializers.ModelSerializer):
    """An invoice with its computed figures (the queryset is annotated by selectors.with_balances)."""

    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    class_name = serializers.CharField(source="enrollment.class_group.name", read_only=True, default=None)
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    amount_paid = serializers.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, read_only=True
    )
    balance = serializers.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, read_only=True)
    overdue_amount = serializers.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, read_only=True
    )
    next_due_date = serializers.DateField(read_only=True, allow_null=True)
    payment_status = serializers.ChoiceField(choices=PaymentStatus.CHOICES, read_only=True)
    lines = InvoiceLineSerializer(many=True, read_only=True)
    payments = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "student",
            "student_name",
            "student_number",
            "enrollment",
            "class_name",
            "academic_year",
            "academic_year_name",
            "issue_date",
            "source",
            "status",
            "subtotal",
            "discount_total",
            "total",
            "amount_paid",
            "balance",
            "overdue_amount",
            "next_due_date",
            "payment_status",
            "notes",
            "cancelled_at",
            "cancel_reason",
            "lines",
            "payments",
        ]
        read_only_fields = fields

    @extend_schema_field(InvoicePaymentSerializer(many=True))
    def get_payments(self, obj) -> list[dict]:
        """Every payment allocated to the invoice, reversed ones included (they no longer count)."""
        payments: dict[int, dict] = {}
        allocations = (
            PaymentAllocation.objects.filter(invoice_line__invoice=obj)
            .select_related("payment")
            .order_by("payment__date", "payment_id")
        )
        for allocation in allocations:
            payment = allocation.payment
            entry = payments.setdefault(
                payment.pk,
                {
                    "id": payment.pk,
                    "number": payment.number,
                    "date": payment.date,
                    "method": payment.method,
                    "status": payment.status,
                    "amount": ZERO,
                },
            )
            entry["amount"] += allocation.amount
        return list(InvoicePaymentSerializer(list(payments.values()), many=True).data)


class InvoiceListSerializer(InvoiceSerializer):
    class Meta(InvoiceSerializer.Meta):
        fields = [f for f in InvoiceSerializer.Meta.fields if f not in ("lines", "payments")]
        read_only_fields = fields


class ManualInvoiceLineSerializer(serializers.Serializer):
    category = TenantPrimaryKeyRelatedField(queryset=FeeCategory.objects.all())
    description = serializers.CharField(max_length=200)
    due_date = serializers.DateField()
    amount = serializers.DecimalField(
        max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, min_value=Decimal("0.01")
    )
    discount = serializers.DecimalField(
        max_digits=MONEY_DIGITS,
        decimal_places=MONEY_PLACES,
        min_value=Decimal("0"),
        required=False,
        default=Decimal("0"),
    )

    def validate(self, attrs):
        if attrs["discount"] > attrs["amount"]:
            raise serializers.ValidationError(
                {"discount": [_("A discount cannot be larger than the amount.")]}
            )
        return attrs


class ManualInvoiceSerializer(serializers.Serializer):
    student = TenantPrimaryKeyRelatedField(queryset=Student.objects.all())
    academic_year = TenantPrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    issue_date = serializers.DateField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")
    lines = ManualInvoiceLineSerializer(many=True, allow_empty=False)

    def validate_lines(self, value):
        if len(value) > 50:
            raise serializers.ValidationError(_("Use at most 50 lines."))
        return value


class GenerateInvoicesSerializer(serializers.Serializer):
    academic_year = TenantPrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    class_group = TenantPrimaryKeyRelatedField(
        queryset=ClassGroup.objects.all(), required=False, allow_null=True
    )

    def validate(self, attrs):
        class_group = attrs.get("class_group")
        if class_group and class_group.academic_year_id != attrs["academic_year"].pk:
            raise serializers.ValidationError({"class_group": [_("Choose a class of this academic year.")]})
        return attrs


class GenerateInvoicesResultSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    skipped = serializers.IntegerField(help_text="Students who already had this year's enrolment invoice.")
    without_fees = serializers.IntegerField(help_text="Students whose level has no fees set up for the year.")


class CancelInvoiceSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


# --- Payments ----------------------------------------------------------------------------------------------


@extend_schema_field(OpenApiTypes.INT)
class SchoolInvoiceLineField(serializers.PrimaryKeyRelatedField):
    """An invoice line of the current school (another school's line reads as "does not exist")."""

    def get_queryset(self):
        school = getattr(self.context.get("request"), "school", None)
        if school is None:
            return InvoiceLine.objects.none()
        return InvoiceLine.objects.filter(invoice__school=school)


class PaymentAllocationSerializer(serializers.ModelSerializer):
    invoice = serializers.IntegerField(source="invoice_line.invoice_id", read_only=True)
    invoice_number = serializers.CharField(source="invoice_line.invoice.number", read_only=True)
    description = serializers.CharField(source="invoice_line.description", read_only=True)
    due_date = serializers.DateField(source="invoice_line.due_date", read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["id", "invoice_line", "invoice", "invoice_number", "description", "due_date", "amount"]
        read_only_fields = fields


class CashSessionRefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    register_name = serializers.CharField()


def _first_cash_session(obj) -> dict | None:
    """The session of the movement that recorded the money (the prefetched `cash_movements`, oldest first)."""
    movements = sorted(obj.cash_movements.all(), key=lambda m: m.pk)
    if not movements:
        return None
    session = movements[0].session
    return {"id": session.pk, "register_name": session.register.name}


class PaymentSerializer(serializers.ModelSerializer):
    """A payment with how much of it paid invoices (the queryset is annotated by selectors.with_allocated)."""

    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    received_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)
    reversed_by_name = serializers.CharField(source="reversed_by.full_name", read_only=True, default=None)
    allocated = _money_field(read_only=True, help_text="Paid to invoice lines.")
    unallocated = _money_field(read_only=True, help_text="Kept as the student's credit; 0 once reversed.")
    allocations = PaymentAllocationSerializer(many=True, read_only=True)
    cash_session = serializers.SerializerMethodField()

    @extend_schema_field(CashSessionRefSerializer(allow_null=True))
    def get_cash_session(self, obj) -> dict | None:
        """The cash session the money went into (null when it was not cash)."""
        return _first_cash_session(obj)

    class Meta:
        model = Payment
        fields = [
            "id",
            "number",
            "student",
            "student_name",
            "student_number",
            "date",
            "amount",
            "method",
            "reference",
            "payer_name",
            "note",
            "status",
            "allocated",
            "unallocated",
            "received_by_name",
            "created_at",
            "reversed_at",
            "reversed_by_name",
            "reversal_reason",
            "cash_session",
            "allocations",
        ]
        read_only_fields = fields


class PaymentListSerializer(PaymentSerializer):
    class Meta(PaymentSerializer.Meta):
        fields = [f for f in PaymentSerializer.Meta.fields if f not in ("allocations", "cash_session")]
        read_only_fields = fields


class RecordPaymentAllocationSerializer(serializers.Serializer):
    invoice_line = SchoolInvoiceLineField()
    amount = _money_field(min_value=Decimal("0.01"))


class RecordPaymentSerializer(serializers.Serializer):
    student = TenantPrimaryKeyRelatedField(queryset=Student.objects.all())
    amount = _money_field(min_value=Decimal("0.01"))
    date = serializers.DateField(required=False, help_text="Defaults to today; never in the future.")
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    payer_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    allocations = RecordPaymentAllocationSerializer(
        many=True,
        required=False,
        help_text="The lines this payment pays. Leave it out to pay the oldest due lines first; "
        "an empty list keeps the whole amount as credit.",
    )
    cash_session = TenantPrimaryKeyRelatedField(
        queryset=CashSession.objects.all(),
        required=False,
        allow_null=True,
        help_text=CASH_SESSION_HELP,
    )

    def validate_allocations(self, value):
        if len(value) > 100:
            raise serializers.ValidationError(_("Use at most 100 lines."))
        return value


class ReversePaymentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class ExpenseSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)
    cancelled_by_name = serializers.CharField(source="cancelled_by.full_name", read_only=True, default=None)
    cash_session = serializers.SerializerMethodField()

    class Meta:
        model = Expense
        fields = [
            "id",
            "number",
            "date",
            "category",
            "amount",
            "method",
            "payee",
            "reference",
            "description",
            "status",
            "recorded_by_name",
            "created_at",
            "cancelled_at",
            "cancelled_by_name",
            "cancel_reason",
            "cash_session",
        ]
        read_only_fields = fields

    @extend_schema_field(CashSessionRefSerializer(allow_null=True))
    def get_cash_session(self, obj) -> dict | None:
        """The cash session the money left (null when it was not paid in cash)."""
        return _first_cash_session(obj)


class RecordExpenseSerializer(serializers.Serializer):
    date = serializers.DateField(required=False, help_text="Defaults to today; never in the future.")
    category = serializers.ChoiceField(choices=Expense.Category.choices)
    amount = _money_field(min_value=Decimal("0.01"))
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    payee = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    description = serializers.CharField(max_length=255)
    cash_session = TenantPrimaryKeyRelatedField(
        queryset=CashSession.objects.all(), required=False, allow_null=True, help_text=CASH_SESSION_HELP
    )


class RefundSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.student_number", read_only=True)
    refunded_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)
    cancelled_by_name = serializers.CharField(source="cancelled_by.full_name", read_only=True, default=None)
    cash_session = serializers.SerializerMethodField()

    class Meta:
        model = Refund
        fields = [
            "id",
            "student",
            "student_name",
            "student_number",
            "date",
            "amount",
            "method",
            "reference",
            "reason",
            "status",
            "refunded_by_name",
            "created_at",
            "cancelled_at",
            "cancelled_by_name",
            "cancel_reason",
            "cash_session",
        ]
        read_only_fields = fields

    @extend_schema_field(CashSessionRefSerializer(allow_null=True))
    def get_cash_session(self, obj) -> dict | None:
        """The cash session the money left (null when it was not refunded in cash)."""
        return _first_cash_session(obj)


class RecordRefundSerializer(serializers.Serializer):
    student = TenantPrimaryKeyRelatedField(queryset=Student.objects.all())
    date = serializers.DateField(required=False, help_text="Defaults to today; never in the future.")
    amount = _money_field(min_value=Decimal("0.01"), help_text="At most the student's credit.")
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    reason = serializers.CharField(max_length=255)
    cash_session = TenantPrimaryKeyRelatedField(
        queryset=CashSession.objects.all(), required=False, allow_null=True, help_text=CASH_SESSION_HELP
    )


class OpenLineSerializer(serializers.ModelSerializer):
    """An unpaid invoice line of a student (annotated by selectors.with_line_balances)."""

    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True)
    net = _money_field(read_only=True)
    paid = _money_field(read_only=True)
    balance = _money_field(read_only=True)
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceLine
        fields = [
            "id",
            "invoice",
            "invoice_number",
            "category_name",
            "description",
            "due_date",
            "net",
            "paid",
            "balance",
            "is_overdue",
        ]
        read_only_fields = fields

    def get_is_overdue(self, obj) -> bool:
        return obj.due_date < timezone.localdate()


class StudentAccountSerializer(serializers.Serializer):
    student = serializers.IntegerField()
    student_name = serializers.CharField()
    student_number = serializers.CharField()
    invoiced = _money_field(help_text="Total of the student's issued invoices.")
    paid = _money_field()
    balance = _money_field(help_text="Still due on issued invoices.")
    overdue = _money_field()
    credit = _money_field(help_text="Paid but not used by any invoice yet.")
    open_lines = OpenLineSerializer(many=True)
