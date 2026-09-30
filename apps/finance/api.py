from django.db.models import Prefetch
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.pdf import pdf_response
from apps.core.viewsets import TenantModelViewSet

from . import services
from .models import FeeCategory, FeeSchedule, Invoice, InvoiceLine, StudentDiscount
from .pdf import invoice_pdf
from .selectors import PaymentStatus, with_balances
from .serializers import (
    CancelInvoiceSerializer,
    FeeCategorySerializer,
    FeeScheduleSerializer,
    GenerateInvoicesResultSerializer,
    GenerateInvoicesSerializer,
    InvoiceListSerializer,
    InvoiceSerializer,
    ManualInvoiceSerializer,
    StudentDiscountSerializer,
)

VIEW = ["finance.view"]
MANAGE_FEES = ["finance.fees.manage"]


class FeeCategoryViewSet(TenantModelViewSet):
    queryset = FeeCategory.objects.all()
    serializer_class = FeeCategorySerializer
    pagination_class = None
    audit_module = "finance"
    filterset_fields = ["is_active", "kind"]
    required_permissions = {"list": VIEW, "retrieve": VIEW, "*": MANAGE_FEES}


class FeeScheduleViewSet(TenantModelViewSet):
    queryset = FeeSchedule.objects.select_related("academic_year", "level", "category")
    serializer_class = FeeScheduleSerializer
    pagination_class = None
    audit_module = "finance"
    filterset_fields = ["academic_year", "level", "category", "applies_to"]
    required_permissions = {"list": VIEW, "retrieve": VIEW, "*": MANAGE_FEES}


class StudentDiscountViewSet(TenantModelViewSet):
    queryset = StudentDiscount.objects.select_related("student", "academic_year", "category")
    serializer_class = StudentDiscountSerializer
    audit_module = "finance"
    filterset_fields = ["student", "academic_year", "category", "reason", "is_active"]
    search_fields = ["student__first_name", "student__last_name", "student__student_number", "note"]
    ordering_fields = ["student__last_name", "value", "created_at"]
    required_permissions = {"list": VIEW, "retrieve": VIEW, "*": MANAGE_FEES}

    def describe(self, instance) -> str:
        return f"{instance.student.full_name} ({instance.academic_year.name})"


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("payment_status", OpenApiTypes.STR, enum=PaymentStatus.CHOICES),
            OpenApiParameter("class_group", OpenApiTypes.INT),
        ]
    )
)
class InvoiceViewSet(TenantModelViewSet):
    """Invoices are issued (from fee schedules or by hand) and cancelled, never edited or deleted."""

    queryset = Invoice.objects.select_related("student", "academic_year", "enrollment__class_group")
    serializer_class = InvoiceSerializer
    audit_module = "finance"
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = {
        "academic_year": ["exact"],
        "student": ["exact"],
        "status": ["exact"],
        "source": ["exact"],
        "issue_date": ["gte", "lte"],
    }
    search_fields = ["number", "student__first_name", "student__last_name", "student__student_number"]
    ordering_fields = ["issue_date", "number", "total", "balance", "student__last_name", "next_due_date"]
    ordering = ["-issue_date", "-id"]
    required_permissions = {
        "list": VIEW,
        "retrieve": VIEW,
        "pdf": VIEW,
        "create": ["finance.invoice.create"],
        "generate": ["finance.invoice.create"],
        "cancel": ["finance.invoice.cancel"],
    }

    def get_queryset(self):
        queryset = with_balances(super().get_queryset())
        if self.action in ("retrieve", "pdf", "cancel", "create"):
            queryset = queryset.prefetch_related(
                Prefetch("lines", queryset=InvoiceLine.objects.select_related("category"))
            )
        params = self.request.query_params
        if params.get("payment_status"):
            if params["payment_status"] not in PaymentStatus.CHOICES:
                raise ValidationError({"payment_status": ["Unknown payment status."]})
            queryset = queryset.filter(payment_status=params["payment_status"])
        if params.get("class_group"):
            queryset = queryset.filter(enrollment__class_group=params["class_group"])
        return queryset

    def get_serializer_class(self):
        return InvoiceListSerializer if self.action == "list" else InvoiceSerializer

    def _out(self, invoice, code=status.HTTP_200_OK):
        invoice = self.get_queryset().get(pk=invoice.pk)
        return Response(InvoiceSerializer(invoice, context=self.get_serializer_context()).data, status=code)

    @extend_schema(request=ManualInvoiceSerializer, responses={201: InvoiceSerializer})
    def create(self, request, *args, **kwargs):
        serializer = ManualInvoiceSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        invoice = services.create_manual_invoice(
            data["student"],
            academic_year=data["academic_year"],
            lines=[dict(line) for line in data["lines"]],
            issue_date=data.get("issue_date"),
            notes=data["notes"],
            request=request,
        )
        return self._out(invoice, status.HTTP_201_CREATED)

    @extend_schema(request=GenerateInvoicesSerializer, responses=GenerateInvoicesResultSerializer)
    @action(detail=False, methods=["post"])
    def generate(self, request):
        """Issue the enrolment fees to every active student (of a class, or of the year) who has none yet."""
        serializer = GenerateInvoicesSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        result = services.generate_invoices(
            request.school,
            academic_year=data["academic_year"],
            class_group=data.get("class_group"),
            request=request,
        )
        return Response(result)

    @extend_schema(request=CancelInvoiceSerializer, responses=InvoiceSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = CancelInvoiceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invoice = services.cancel_invoice(
            self.get_object(), reason=serializer.validated_data["reason"], request=request
        )
        return self._out(invoice)

    @extend_schema(responses={(200, "application/pdf"): bytes})
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        invoice = self.get_object()
        return pdf_response(invoice_pdf(invoice), f"{invoice.number}.pdf")
