from django.db.models import DecimalField, F, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.pdf import pdf_response
from apps.core.viewsets import TenantModelViewSet
from apps.finance.models import MONEY_DIGITS, MONEY_PLACES

from . import services
from .models import CashMovement, CashRegister, CashSession
from .pdf import session_journal_pdf
from .serializers import (
    CashMovementSerializer,
    CashRegisterSerializer,
    CashSessionDetailSerializer,
    CashSessionSerializer,
    CloseSessionSerializer,
    ManualMovementSerializer,
    OpenSessionSerializer,
)

VIEW = ["cash.view"]
MONEY: DecimalField = DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)


def _movement_sum(direction: str) -> Coalesce:
    return Coalesce(
        Sum("movements__amount", filter=Q(movements__direction=direction)), Value(0, output_field=MONEY)
    )


class CashRegisterViewSet(TenantModelViewSet):
    """The school's cash boxes. A first one ("Caisse principale") exists as soon as the list is read."""

    queryset = CashRegister.objects.all()
    serializer_class = CashRegisterSerializer
    pagination_class = None
    audit_module = "cash"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    required_permissions = {"list": VIEW, "retrieve": VIEW, "*": ["cash.open"]}

    def list(self, request, *args, **kwargs):
        services.default_register(request.school)
        return super().list(request, *args, **kwargs)


class CashSessionViewSet(TenantModelViewSet):
    """Opening, cash movements and closing of a register. Sessions are never edited or deleted."""

    queryset = CashSession.objects.select_related("register", "created_by", "closed_by")
    serializer_class = CashSessionSerializer
    audit_module = "cash"
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = {"register": ["exact"], "status": ["exact"], "opened_at": ["date__gte", "date__lte"]}
    ordering_fields = ["opened_at", "closed_at", "difference"]
    ordering = ["-opened_at", "-id"]
    required_permissions = {
        "list": VIEW,
        "retrieve": VIEW,
        "journal": VIEW,
        "create": ["cash.open"],
        "close": ["cash.close"],
        "movements": ["cash.record"],
    }

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .annotate(
                money_in=_movement_sum(CashMovement.Direction.IN),
                money_out=_movement_sum(CashMovement.Direction.OUT),
            )
            .annotate(expected=F("opening_balance") + F("money_in") - F("money_out"))
        )
        if self.action != "list":
            queryset = queryset.prefetch_related(
                Prefetch(
                    "movements",
                    queryset=CashMovement.objects.select_related(
                        "created_by", "payment", "expense", "refund"
                    ).order_by("created_at", "id"),
                )
            )
        return queryset

    def get_serializer_class(self):
        return CashSessionSerializer if self.action == "list" else CashSessionDetailSerializer

    def _out(self, session, code=status.HTTP_200_OK):
        session = self.get_queryset().get(pk=session.pk)
        return Response(
            CashSessionDetailSerializer(session, context=self.get_serializer_context()).data, status=code
        )

    @extend_schema(request=OpenSessionSerializer, responses={201: CashSessionDetailSerializer})
    def create(self, request, *args, **kwargs):
        serializer = OpenSessionSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        session = services.open_session(
            data["register"], opening_balance=data.get("opening_balance"), note=data["note"], request=request
        )
        return self._out(session, status.HTTP_201_CREATED)

    @extend_schema(request=CloseSessionSerializer, responses=CashSessionDetailSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        serializer = CloseSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        session = services.close_session(
            self.get_object(), counted=data["counted_closing"], note=data["closing_note"], request=request
        )
        return self._out(session)

    @extend_schema(request=ManualMovementSerializer, responses={201: CashMovementSerializer})
    @action(detail=True, methods=["post"])
    def movements(self, request, pk=None):
        """Cash in or out that is not a payment, an expense or a refund: a bank deposit, a float top-up..."""
        serializer = ManualMovementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        movement = services.record_movement(
            self.get_object(),
            direction=data["direction"],
            amount=data["amount"],
            description=data["description"],
            request=request,
        )
        return Response(CashMovementSerializer(movement).data, status=status.HTTP_201_CREATED)

    @extend_schema(responses={(200, "application/pdf"): bytes})
    @action(detail=True, methods=["get"])
    def journal(self, request, pk=None):
        session = self.get_object()
        return pdf_response(
            session_journal_pdf(session), f"journal-{session.opened_at:%Y-%m-%d}-{session.pk}.pdf"
        )
