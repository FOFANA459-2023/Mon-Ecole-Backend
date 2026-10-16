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

from apps.academics.models import AcademicYear, ClassGroup, Level, Term
from apps.academics.scoping import sees_all_classes
from apps.academics.services import current_year
from apps.assessments.scoping import can_view_class
from apps.audit import services as audit
from apps.cashregister.models import CashRegister
from apps.core.pdf import pdf_response
from apps.core.permissions import HasSchoolPermission
from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.finance.models import Expense, Payment

from . import academic
from . import school as school_reports
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
        return _download(report, school, language, request.user, export, filename)


def _download(report, school, language: str, user, export: str, filename: str):
    if export == "pdf":
        return pdf_response(to_pdf(report, school, language, user.full_name), filename)
    content = (
        to_xlsx(report, school.currency) if export == "xlsx" else to_csv(report, school.currency, language)
    )
    response = HttpResponse(content, content_type=EXPORTS[export])
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class AcademicReportParamsSerializer(serializers.Serializer):
    academic_year = TenantPrimaryKeyRelatedField(
        queryset=AcademicYear.objects.all(),
        required=False,
        allow_null=True,
        help_text="Defaults to the term's year, else the class's, else the current year.",
    )
    term = TenantPrimaryKeyRelatedField(
        queryset=Term.objects.all(), required=False, allow_null=True, help_text="Leave out for the year."
    )
    level = TenantPrimaryKeyRelatedField(queryset=Level.objects.all(), required=False, allow_null=True)
    class_group = TenantPrimaryKeyRelatedField(
        queryset=ClassGroup.objects.all(), required=False, allow_null=True
    )
    export = serializers.ChoiceField(choices=["pdf", "xlsx", "csv"], required=False, allow_blank=True)

    def validate(self, attrs):
        term, class_group = attrs.get("term"), attrs.get("class_group")
        year = attrs.get("academic_year") or (term.academic_year if term else None)
        year = year or (class_group.academic_year if class_group else None)
        year = year or current_year(self.context["request"].school)
        if year is None:
            raise serializers.ValidationError({"academic_year": [_("Choose a school year.")]})
        if term is not None and term.academic_year_id != year.pk:
            raise serializers.ValidationError({"term": [_("Choose a term of this academic year.")]})
        if class_group is not None and class_group.academic_year_id != year.pk:
            raise serializers.ValidationError({"class_group": [_("Choose a class of this academic year.")]})
        attrs["academic_year"] = year
        return attrs


class AcademicReportView(APIView):
    """Academic reports: grades (one class), ranking and subject results (a class, a level or the school).

    Published marks only; a term, or the year when `term` is left out. Teachers reach the class they lead;
    a level or the whole school needs access to every class. `export=pdf|xlsx|csv` needs reports.export.
    """

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": ["grades.view"]}

    @extend_schema(
        parameters=[
            AcademicReportParamsSerializer,
            OpenApiParameter("key", OpenApiTypes.STR, OpenApiParameter.PATH, enum=list(academic.BUILDERS)),
        ],
        responses={
            200: OpenApiResponse(OpenApiTypes.OBJECT, description="The report (JSON) or the exported file."),
        },
    )
    def get(self, request, key: str):
        builder = academic.BUILDERS.get(key)
        if builder is None:
            raise NotFound()
        serializer = AcademicReportParamsSerializer(data=request.query_params, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        export = data.pop("export", "") or ""
        class_group = data.get("class_group")
        if key in academic.CLASS_ONLY and class_group is None:
            raise serializers.ValidationError({"class_group": [_("Choose a class.")]})
        if class_group is not None:
            if not can_view_class(request, class_group):
                raise NotFound()
        elif not sees_all_classes(request):
            raise PermissionDenied(_("Choose the class you lead."))
        if export and "reports.export" not in request.permission_codes:
            raise PermissionDenied(_("You cannot export reports."))
        school = request.school
        language = request.user.language if request.user.language in ("fr", "en") else school.default_language
        report = builder(school, academic.Params(**data), language)
        if not export:
            return Response(to_json(report, school.currency))
        audit.record(
            "export",
            request=request,
            module="grades",
            entity_type="report",
            entity_id=key,
            summary=f"{report.title} ({export}): {report.subtitle}",
            new={"report": key, "format": export},
        )
        return _download(report, school, language, request.user, export, f"{key}.{export}")


class SchoolReportParamsSerializer(serializers.Serializer):
    academic_year = TenantPrimaryKeyRelatedField(
        queryset=AcademicYear.objects.all(),
        required=False,
        allow_null=True,
        help_text="Defaults to the current year.",
    )
    level = TenantPrimaryKeyRelatedField(queryset=Level.objects.all(), required=False, allow_null=True)
    class_group = TenantPrimaryKeyRelatedField(
        queryset=ClassGroup.objects.all(), required=False, allow_null=True
    )
    date_from = serializers.DateField(
        required=False, allow_null=True, help_text="Enrolment and attendance reports."
    )
    date_to = serializers.DateField(required=False, allow_null=True)
    export = serializers.ChoiceField(choices=["pdf", "xlsx", "csv"], required=False, allow_blank=True)

    def validate(self, attrs):
        year = attrs.get("academic_year") or (
            attrs["class_group"].academic_year if attrs.get("class_group") else None
        )
        year = year or current_year(self.context["request"].school)
        if year is None:
            raise serializers.ValidationError({"academic_year": [_("Choose a school year.")]})
        if attrs.get("class_group") is not None and attrs["class_group"].academic_year_id != year.pk:
            raise serializers.ValidationError({"class_group": [_("Choose a class of this academic year.")]})
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_from": [_("The start must be on or before the end.")]})
        attrs["academic_year"] = year
        return attrs


class SchoolReportView(APIView):
    """School reports: enrolment, classes, teachers, attendance and student statistics (reports.view).

    One school year, the whole school or a level or class. `export=pdf|xlsx|csv` needs reports.export.
    """

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": ["reports.view"]}

    @extend_schema(
        parameters=[
            SchoolReportParamsSerializer,
            OpenApiParameter(
                "key", OpenApiTypes.STR, OpenApiParameter.PATH, enum=list(school_reports.BUILDERS)
            ),
        ],
        responses={
            200: OpenApiResponse(OpenApiTypes.OBJECT, description="The report (JSON) or the exported file.")
        },
    )
    def get(self, request, key: str):
        builder = school_reports.BUILDERS.get(key)
        if builder is None:
            raise NotFound()
        serializer = SchoolReportParamsSerializer(data=request.query_params, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        export = data.pop("export", "") or ""
        if export and "reports.export" not in request.permission_codes:
            raise PermissionDenied(_("You cannot export reports."))
        school = request.school
        language = request.user.language if request.user.language in ("fr", "en") else school.default_language
        report = builder(school, school_reports.Params(**data), language)
        if not export:
            return Response(to_json(report, school.currency))
        audit.record(
            "export",
            request=request,
            module="reports",
            entity_type="report",
            entity_id=key,
            summary=f"{report.title} ({export}): {report.subtitle}",
            new={"report": key, "format": export},
        )
        return _download(report, school, language, request.user, export, f"{key}.{export}")
