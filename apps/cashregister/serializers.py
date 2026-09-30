from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.finance.models import MONEY_DIGITS, MONEY_PLACES

from . import services
from .models import CashMovement, CashRegister, CashSession


def _money_field(**kwargs):
    return serializers.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, **kwargs)


class OpenSessionBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    opened_at = serializers.DateTimeField()
    opened_by_name = serializers.CharField(allow_null=True)
    opening_balance = _money_field()
    expected = _money_field(help_text="Cash that should be in the register now.")


class CashRegisterSerializer(serializers.ModelSerializer):
    open_session = serializers.SerializerMethodField()
    last_counted = serializers.SerializerMethodField()

    class Meta:
        model = CashRegister
        fields = ["id", "name", "is_active", "open_session", "last_counted"]

    def validate_name(self, value):
        school = self.context["request"].school
        clash = CashRegister.objects.filter(school=school, name__iexact=value.strip())
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("A register already has this name."))
        return value.strip()

    def validate_is_active(self, value):
        is_open = (
            self.instance is not None
            and self.instance.sessions.filter(status=CashSession.Status.OPEN).exists()
        )
        if not value and is_open:
            raise serializers.ValidationError(_("Close this register before putting it out of use."))
        return value

    @extend_schema_field(OpenSessionBriefSerializer(allow_null=True))
    def get_open_session(self, obj) -> dict | None:
        session = obj.sessions.filter(status=CashSession.Status.OPEN).select_related("created_by").first()
        if session is None:
            return None
        return OpenSessionBriefSerializer(
            {
                "id": session.pk,
                "opened_at": session.opened_at,
                "opened_by_name": session.created_by.full_name if session.created_by_id else None,
                "opening_balance": session.opening_balance,
                "expected": services.totals(session)["expected"],
            }
        ).data

    @extend_schema_field(_money_field(help_text="Cash counted at the last closing: the next float."))
    def get_last_counted(self, obj) -> str:
        return _money_field().to_representation(services.last_counted(obj))


class CashMovementSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)
    payment_number = serializers.CharField(source="payment.number", read_only=True, default=None)
    expense_number = serializers.CharField(source="expense.number", read_only=True, default=None)
    student = serializers.SerializerMethodField()

    class Meta:
        model = CashMovement
        fields = [
            "id",
            "created_at",
            "direction",
            "source",
            "amount",
            "description",
            "created_by_name",
            "payment",
            "payment_number",
            "expense",
            "expense_number",
            "refund",
            "student",
        ]
        read_only_fields = fields

    def get_student(self, obj) -> int | None:
        """The student a payment or a refund was for."""
        if obj.payment_id:
            return obj.payment.student_id
        if obj.refund_id:
            return obj.refund.student_id
        return None


class SourceTotalSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=CashMovement.Source.choices)  # type: ignore[assignment]  # "source" is also a Field attribute
    direction = serializers.ChoiceField(choices=CashMovement.Direction.choices)
    count = serializers.IntegerField()
    total = _money_field()


class CashSessionSerializer(serializers.ModelSerializer):
    """A session with its running totals (the queryset is annotated with money_in and money_out)."""

    register_name = serializers.CharField(source="register.name", read_only=True)
    opened_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)
    closed_by_name = serializers.CharField(source="closed_by.full_name", read_only=True, default=None)
    money_in = _money_field(read_only=True)
    money_out = _money_field(read_only=True)
    expected = _money_field(read_only=True, help_text="Opening + money in − money out.")

    class Meta:
        model = CashSession
        fields = [
            "id",
            "register",
            "register_name",
            "status",
            "opened_at",
            "opened_by_name",
            "opening_balance",
            "opening_note",
            "money_in",
            "money_out",
            "expected",
            "closed_at",
            "closed_by_name",
            "expected_closing",
            "counted_closing",
            "difference",
            "closing_note",
        ]
        read_only_fields = fields


class CashSessionDetailSerializer(CashSessionSerializer):
    by_source = serializers.SerializerMethodField()
    movements = CashMovementSerializer(many=True, read_only=True)

    class Meta(CashSessionSerializer.Meta):
        fields = [*CashSessionSerializer.Meta.fields, "by_source", "movements"]
        read_only_fields = fields

    @extend_schema_field(SourceTotalSerializer(many=True))
    def get_by_source(self, obj) -> list[dict]:
        rows: dict[tuple[str, str], dict] = {}
        for movement in obj.movements.all():
            row = rows.setdefault(
                (movement.source, movement.direction),
                {"source": movement.source, "direction": movement.direction, "count": 0, "total": Decimal(0)},
            )
            row["count"] += 1
            row["total"] += movement.amount
        return list(SourceTotalSerializer(list(rows.values()), many=True).data)


class OpenSessionSerializer(serializers.Serializer):
    register = TenantPrimaryKeyRelatedField(queryset=CashRegister.objects.all())
    opening_balance = _money_field(
        required=False, allow_null=True, help_text="Defaults to the cash counted at the last closing."
    )
    note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class CloseSessionSerializer(serializers.Serializer):
    counted_closing = _money_field(min_value=Decimal("0"))
    closing_note = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class ManualMovementSerializer(serializers.Serializer):
    direction = serializers.ChoiceField(choices=CashMovement.Direction.choices)
    amount = _money_field(min_value=Decimal("0.01"))
    description = serializers.CharField(max_length=255)
