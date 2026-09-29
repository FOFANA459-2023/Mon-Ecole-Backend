from datetime import date
from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.academics.models import AcademicYear, ClassGroup, Level
from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.people.models import Student

from .models import (
    MONEY_DIGITS,
    MONEY_PLACES,
    FeeCategory,
    FeeSchedule,
    Invoice,
    InvoiceLine,
    StudentDiscount,
)
from .money import ZERO, to_money
from .selectors import PaymentStatus


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


class InvoiceLineSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    net = serializers.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, read_only=True)

    class Meta:
        model = InvoiceLine
        fields = ["id", "category", "category_name", "description", "due_date", "amount", "discount", "net"]


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
        ]
        read_only_fields = fields


class InvoiceListSerializer(InvoiceSerializer):
    class Meta(InvoiceSerializer.Meta):
        fields = [f for f in InvoiceSerializer.Meta.fields if f != "lines"]
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
