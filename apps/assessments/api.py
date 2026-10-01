from django.db.models import Prefetch
from django.utils.text import slugify
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import Term
from apps.academics.scoping import staff_profile
from apps.academics.services import current_year
from apps.audit import services as audit
from apps.core.pdf import pdf_response
from apps.core.permissions import HasSchoolPermission
from apps.core.viewsets import TenantModelViewSet
from apps.enrollments.models import Enrollment
from apps.people.models import Student

from . import services
from .models import Assessment, Gradebook, GradeCategory, GradingScale, ReportComment
from .reportcards import annual_cards, report_cards_pdf, student_results, term_cards
from .scoping import can_view_class, visible_gradebooks
from .selectors import class_results, gradebook_sheet, with_counts
from .serializers import (
    AssessmentSerializer,
    ClassResultsParamsSerializer,
    ClassResultsSerializer,
    CopySetupSerializer,
    GradebookDetailSerializer,
    GradebookReasonSerializer,
    GradebookSerializer,
    GradebookSheetSerializer,
    GradebookUpdateSerializer,
    GradeCategorySerializer,
    GradingScaleSerializer,
    ReportCardParamsSerializer,
    ReportCommentRowSerializer,
    SaveGradesResultSerializer,
    SaveGradesSerializer,
    SaveReportCommentsSerializer,
    StudentResultsSerializer,
)

VIEW = ["grades.view"]
ENTER = ["grades.enter"]


class GradingScaleViewSet(TenantModelViewSet):
    """How the school reports marks (out of 20, 10, 100...), with optional differences for some levels."""

    queryset = GradingScale.objects.select_related("level")
    serializer_class = GradingScaleSerializer
    pagination_class = None
    audit_module = "grades"
    required_permissions = {"list": [], "retrieve": [], "*": ["settings.manage"]}

    def describe(self, instance) -> str:
        return f"/{instance.max_mark} — {instance.level.name if instance.level else 'school'}"


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("term", OpenApiTypes.INT, required=True),
            OpenApiParameter("mine", OpenApiTypes.BOOL, description="Only the subjects I teach."),
        ]
    )
)
class GradebookViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """One gradebook per subject, class and term, holding the teacher's own rules, assessments and marks.

    Gradebooks are never created or deleted by hand: listing a term creates the missing gradebooks of every
    subject taught that year.
    """

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    queryset = Gradebook.objects.select_related(
        "class_subject__class_group__level", "class_subject__subject", "class_subject__teacher", "term"
    )
    serializer_class = GradebookSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    filterset_fields = {
        "class_subject__class_group": ["exact"],
        "class_subject__subject": ["exact"],
        "class_subject__teacher": ["exact"],
        "status": ["exact"],
    }
    search_fields = ["class_subject__subject__name", "class_subject__class_group__name"]
    ordering = [
        "class_subject__class_group__level__order",
        "class_subject__class_group__name",
        "class_subject__subject__name",
    ]
    required_permissions = {
        "list": VIEW,
        "retrieve": VIEW,
        "sheet": VIEW,
        "partial_update": ENTER,
        "grades": ENTER,
        "copy_setup": ENTER,
        "submit": ["grades.submit"],
        "send_back": ["grades.review"],
        "publish": ["grades.publish"],
        "reopen": ["grades.reopen"],
    }

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Gradebook.objects.none()
        queryset = visible_gradebooks(self.request, super().get_queryset().filter(school=self.request.school))
        if self.action == "list":
            return with_counts(queryset)
        if self.action == "retrieve":
            return self._detailed(queryset)
        return queryset

    @staticmethod
    def _detailed(queryset):
        return (
            with_counts(queryset)
            .select_related("submitted_by", "published_by")
            .prefetch_related("categories", Prefetch("assessments", queryset=Assessment.objects.all()))
        )

    def get_serializer_class(self):
        return GradebookSerializer if self.action == "list" else GradebookDetailSerializer

    def list(self, request, *args, **kwargs):
        term_id = request.query_params.get("term", "")
        term = Term.objects.filter(school=request.school, pk=term_id).first() if term_id.isdigit() else None
        if term is None:
            raise ValidationError({"term": [_("Choose a term.")]})
        services.ensure_gradebooks(request.school, term)
        queryset = self.filter_queryset(self.get_queryset()).filter(term=term)
        if request.query_params.get("mine") in ("1", "true"):
            staff = staff_profile(request)
            queryset = queryset.filter(class_subject__teacher=staff) if staff else queryset.none()
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    def _out(self, gradebook, code=status.HTTP_200_OK):
        gradebook = self._detailed(
            Gradebook.objects.select_related(
                "class_subject__class_group__level",
                "class_subject__subject",
                "class_subject__teacher",
                "term",
            )
        ).get(pk=gradebook.pk)
        return Response(
            GradebookDetailSerializer(gradebook, context=self.get_serializer_context()).data, status=code
        )

    @extend_schema(request=GradebookUpdateSerializer, responses=GradebookDetailSerializer)
    def partial_update(self, request, *args, **kwargs):
        serializer = GradebookUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gradebook = services.update_gradebook(
            self.get_object(), missing_policy=serializer.validated_data["missing_policy"], request=request
        )
        return self._out(gradebook)

    @extend_schema(responses=GradebookSheetSerializer)
    @action(detail=True, methods=["get"])
    def sheet(self, request, pk=None):
        """The marks grid: every student's marks, category and subject marks, ranks and class statistics."""
        return Response(GradebookSheetSerializer(gradebook_sheet(self.get_object())).data)

    @extend_schema(request=SaveGradesSerializer, responses=SaveGradesResultSerializer)
    @action(detail=True, methods=["post"])
    def grades(self, request, pk=None):
        """Save a batch of marks. Leave score empty to clear a mark; `excused` leaves the student out."""
        serializer = SaveGradesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.save_grades(
            self.get_object(), [dict(e) for e in serializer.validated_data["grades"]], request=request
        )
        return Response(result)

    @extend_schema(request=CopySetupSerializer, responses=GradebookDetailSerializer)
    @action(detail=True, methods=["post"], url_path="copy-setup")
    def copy_setup(self, request, pk=None):
        """Reuse the categories (and optionally the assessments, without marks) of another gradebook."""
        serializer = CopySetupSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        gradebook = services.copy_setup(
            self.get_object(), data["source"], with_assessments=data["with_assessments"], request=request
        )
        return self._out(gradebook)

    @extend_schema(request=None, responses=GradebookDetailSerializer)
    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        return self._out(services.submit(self.get_object(), request=request))

    @extend_schema(request=GradebookReasonSerializer, responses=GradebookDetailSerializer)
    @action(detail=True, methods=["post"], url_path="send-back")
    def send_back(self, request, pk=None):
        serializer = GradebookReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gradebook = services.send_back(
            self.get_object(), reason=serializer.validated_data["reason"], request=request
        )
        return self._out(gradebook)

    @extend_schema(request=None, responses=GradebookDetailSerializer)
    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        return self._out(services.publish(self.get_object(), request=request))

    @extend_schema(request=GradebookReasonSerializer, responses=GradebookDetailSerializer)
    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        serializer = GradebookReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        gradebook = services.reopen(
            self.get_object(), reason=serializer.validated_data["reason"], request=request
        )
        return self._out(gradebook)


class _GradebookPartViewSet(TenantModelViewSet):
    """Categories and assessments: changed only by the subject's teacher, while the gradebook is open."""

    pagination_class = None
    audit_module = "grades"
    filterset_fields = ["gradebook"]
    required_permissions = {"list": VIEW, "retrieve": VIEW, "*": ENTER}

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        visible = visible_gradebooks(self.request, Gradebook.objects.filter(school=self.request.school))
        return queryset.filter(gradebook__in=visible)

    def describe(self, instance) -> str:
        return f"{instance.name} ({instance.gradebook})"

    def perform_create(self, serializer):
        services.require_editable(self.request, serializer.validated_data["gradebook"])
        super().perform_create(serializer)

    def perform_update(self, serializer):
        services.require_editable(self.request, serializer.instance.gradebook)
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        services.require_editable(self.request, instance.gradebook)
        super().perform_destroy(instance)


class GradeCategoryViewSet(_GradebookPartViewSet):
    """The teacher's own kinds of assessment ("Interrogations", "Quiz", "Exam"...) and their weight."""

    queryset = GradeCategory.objects.select_related(
        "gradebook__class_subject__class_group", "gradebook__term"
    )
    serializer_class = GradeCategorySerializer

    def perform_destroy(self, instance):
        services.require_editable(self.request, instance.gradebook)
        if instance.assessments.exists():
            raise ValidationError({"detail": _("Move or delete the assessments of this category first.")})
        super(_GradebookPartViewSet, self).perform_destroy(instance)


class AssessmentViewSet(_GradebookPartViewSet):
    """Every quiz, homework, test or exam, with any name, marked out of any number. Deleting one deletes
    its marks (this is written to the audit log)."""

    queryset = Assessment.objects.select_related("gradebook__class_subject__class_group", "gradebook__term")
    serializer_class = AssessmentSerializer

    def perform_update(self, serializer):
        if "max_score" in serializer.validated_data:
            services.check_max_score(serializer.instance, serializer.validated_data["max_score"])
        super().perform_update(serializer)


class ClassResultsView(APIView):
    """A class's results for a term: each subject's mark, the coefficient-weighted average and the rank.

    For users who see every class, and for the class teacher.
    """

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": VIEW}

    @extend_schema(parameters=[ClassResultsParamsSerializer], responses=ClassResultsSerializer)
    def get(self, request):
        params = ClassResultsParamsSerializer(data=request.query_params, context={"request": request})
        params.is_valid(raise_exception=True)
        class_group, term = params.validated_data["class_group"], params.validated_data["term"]
        if not can_view_class(request, class_group):
            raise NotFound()
        return Response(ClassResultsSerializer(class_results(class_group, term)).data)


def _may_comment(request, class_group) -> bool:
    """Report-card comments: whoever prints report cards, and the class teacher."""
    if "reportcards.generate" in request.permission_codes:
        return can_view_class(request, class_group)
    staff = staff_profile(request)
    return staff is not None and class_group.class_teacher_id == staff.pk


class ReportCardView(APIView):
    """Report cards as one PDF: a whole class (one page per student) or one student; a term, or the year
    when `term` is left out. Only published marks count."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": ["reportcards.generate"]}

    @extend_schema(parameters=[ReportCardParamsSerializer], responses={(200, "application/pdf"): bytes})
    def get(self, request):
        params = ReportCardParamsSerializer(data=request.query_params, context={"request": request})
        params.is_valid(raise_exception=True)
        data = params.validated_data
        class_group, term = data["class_group"], data.get("term")
        if not can_view_class(request, class_group):
            raise NotFound()
        enrollment = None
        if data.get("enrollment"):
            enrollment = (
                Enrollment.objects.filter(pk=data["enrollment"], class_group=class_group)
                .select_related("student")
                .first()
            )
            if enrollment is None:
                raise NotFound()
        cards = term_cards(class_group, term, enrollment) if term else annual_cards(class_group, enrollment)
        period = slugify(term.name) if term else "annuel"
        who = slugify(enrollment.student.full_name) if enrollment else slugify(class_group.name)
        audit.record(
            "export",
            request=request,
            instance=class_group,
            module="grades",
            summary=f"Report cards printed: {class_group.name}, {term.name if term else 'year'}"
            + (f", {enrollment.student.full_name}" if enrollment else f" ({len(cards['cards'])} students)"),
        )
        return pdf_response(report_cards_pdf(cards), f"bulletin-{who}-{period}.pdf")


class ReportCommentsView(APIView):
    """The general comment on each student's report card, for a term or (no term) the year."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": VIEW, "post": VIEW}

    def _scope(self, data):
        class_group, term = data["class_group"], data.get("term")
        if not _may_comment(self.request, class_group):
            raise NotFound()
        enrollments = list(
            Enrollment.objects.filter(class_group=class_group, status__in=services.IN_CLASS)
            .select_related("student")
            .order_by("student__last_name", "student__first_name", "id")
        )
        return class_group, term, enrollments

    @staticmethod
    def _rows(term, enrollments):
        comments = ReportComment.objects.filter(enrollment__in=enrollments)
        comments = comments.filter(term=term) if term else comments.filter(term__isnull=True)
        by_enrollment = dict(comments.values_list("enrollment", "comment"))
        return [
            {
                "enrollment": e.pk,
                "student": e.student_id,
                "student_name": e.student.full_name,
                "comment": by_enrollment.get(e.pk, ""),
            }
            for e in enrollments
        ]

    @extend_schema(parameters=[ReportCardParamsSerializer], responses=ReportCommentRowSerializer(many=True))
    def get(self, request):
        params = ReportCardParamsSerializer(data=request.query_params, context={"request": request})
        params.is_valid(raise_exception=True)
        _, term, enrollments = self._scope(params.validated_data)
        return Response(ReportCommentRowSerializer(self._rows(term, enrollments), many=True).data)

    @extend_schema(request=SaveReportCommentsSerializer, responses=ReportCommentRowSerializer(many=True))
    def post(self, request):
        params = SaveReportCommentsSerializer(data=request.data, context={"request": request})
        params.is_valid(raise_exception=True)
        class_group, term, enrollments = self._scope(params.validated_data)
        services.save_report_comments(
            class_group,
            term,
            enrollments,
            [dict(c) for c in params.validated_data["comments"]],
            request=request,
        )
        return Response(ReportCommentRowSerializer(self._rows(term, enrollments), many=True).data)


class StudentResultsView(APIView):
    """A student's results this school year: each term's average, rank and honours band, and the year's."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": VIEW}

    @extend_schema(responses=StudentResultsSerializer)
    def get(self, request, pk: int):
        student = Student.objects.filter(school=request.school, pk=pk).first()
        if student is None:
            raise NotFound()
        year = current_year(request.school)
        enrollment = (
            student.enrollments.filter(academic_year=year, status=Enrollment.Status.ACTIVE)
            .select_related("class_group__level", "class_group__academic_year")
            .first()
            if year
            else None
        )
        if enrollment is None:
            empty: dict[str, object] = {
                "enrollment": None,
                "class_group": None,
                "class_name": "",
                "scale": None,
                "terms": [],
            }
            return Response(StudentResultsSerializer(empty).data)
        if not can_view_class(request, enrollment.class_group):
            raise NotFound()
        return Response(StudentResultsSerializer(student_results(enrollment)).data)
