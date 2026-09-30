from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import AcademicYear, ClassGroup
from apps.academics.services import current_year
from apps.audit import services as audit
from apps.cashregister.models import CashRegister
from apps.core.pdf import pdf_response
from apps.core.permissions import HasSchoolPermission
from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.finance.models import Expense, Payment

from .finance import BUILDERS, Params
from .render import to_csv, to_json, to_pdf, to_xlsx

EXPORTS = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}
MAX_DAYS = 731


class FinanceReportParamsSerializer(serializers.Serializer):
    date_from = serializers.DateField(
        required=False, help_text="Defaults to the first day of date_to's month."
    )
    date_to = serializers.DateField(required=False, help_text="Defaults to today.")
    academic_year = TenantPrimaryKeyRelatedField(
        queryset=AcademicYear.objects.all(), required=False, allow_null=True
    )
    class_group = TenantPrimaryKeyRelatedField(
        queryset=ClassGroup.objects.all(), required=False, allow_null=True
    )
    register = TenantPrimaryKeyRelatedField(
        queryset=CashRegister.objects.all(), required=False, allow_null=True
    )
    method = serializers.ChoiceField(choices=Payment.Method.choices, required=False, allow_blank=True)
    category = serializers.ChoiceField(choices=Expense.Category.choices, required=False, allow_blank=True)
    overdue_only = serializers.BooleanField(required=False, default=False)
    export = serializers.ChoiceField(choices=["pdf", "xlsx", "csv"], required=False, allow_blank=True)

    def validate(self, attrs):
        date_to = attrs.get("date_to") or timezone.localdate()
        date_from = attrs.get("date_from") or date_to.replace(day=1)
        if date_from > date_to:
            raise serializers.ValidationError({"date_from": [_("The start must be on or before the end.")]})
        if date_to - date_from > timedelta(days=MAX_DAYS):
            raise serializers.ValidationError({"date_from": [_("Choose a period of at most two years.")]})
        attrs.update(date_from=date_from, date_to=date_to)
        return attrs


class FinanceReportView(APIView):
    """Finance reports: payments, outstanding, cash, expenses and summary.

    JSON for the screen (long sections are cut to 1 000 rows); `export=pdf|xlsx|csv` downloads the whole
    report and needs finance.export. Labels follow the user's language.
    """

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": ["finance.view"]}

    @extend_schema(
        parameters=[
            FinanceReportParamsSerializer,
            OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH, enum=list(BUILDERS)),
        ],
        responses={
            200: OpenApiResponse(OpenApiTypes.OBJECT, description="The report (JSON) or the exported file."),
        },
    )
    def get(self, request, key: str):
        builder = BUILDERS.get(key)
        if builder is None:
            raise NotFound()
        serializer = FinanceReportParamsSerializer(data=request.query_params, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        export = data.pop("export", "") or ""
        if export and "finance.export" not in request.permission_codes:
            raise PermissionDenied(_("You cannot export financial data."))
        school = request.school
        if key == "summary" and not data.get("academic_year"):
            data["academic_year"] = current_year(school)
        language = request.user.language if request.user.language in ("fr", "en") else school.default_language
        report = builder(school, Params(**data), language)
        if not export:
            return Response(to_json(report, school.currency))

        audit.record(
            "export",
            request=request,
            module="finance",
            entity_type="report",
            entity_id=key,
            summary=f"{report.title} ({export}): {report.subtitle}",
            new={"report": key, "format": export, "from": str(data["date_from"]), "to": str(data["date_to"])},
        )
        filename = f"{key}-{data['date_from']}-{data['date_to']}.{export}"
        if export == "pdf":
            return pdf_response(to_pdf(report, school, language, request.user.full_name), filename)
        content = (
            to_xlsx(report, school.currency)
            if export == "xlsx"
            else to_csv(report, school.currency, language)
        )
        response = HttpResponse(content, content_type=EXPORTS[export])
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
