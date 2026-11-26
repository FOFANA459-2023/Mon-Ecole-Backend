from django.db import transaction
from django.db.models import Count, Q
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.core.pdf import pdf_response
from apps.core.viewsets import TenantModelViewSet

from . import services
from .models import AcademicYear, ClassGroup, ClassSubject, Level, Subject, Term
from .scoping import visible_classes
from .serializers import (
    AcademicYearSerializer,
    ClassGroupSerializer,
    ClassSubjectSerializer,
    LevelSerializer,
    SubjectSerializer,
    TermSerializer,
)

MANAGE_SETTINGS = ["settings.manage"]


class StartYearSerializer(serializers.Serializer):
    continuing_staff_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text="Staff who stay this year. Omit to change the current year without touching the team.",
    )


class AcademicYearViewSet(TenantModelViewSet):
    queryset = AcademicYear.objects.prefetch_related("terms")
    serializer_class = AcademicYearSerializer
    pagination_class = None
    audit_module = "academics"
    required_permissions = {
        "list": [],
        "retrieve": [],
        "create": MANAGE_SETTINGS,
        "partial_update": MANAGE_SETTINGS,
        "destroy": MANAGE_SETTINGS,
        "set_current": MANAGE_SETTINGS,
    }

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        serializer.instance = services.create_academic_year(
            self.request.school,
            name=data["name"],
            start_date=data["start_date"],
            end_date=data["end_date"],
            term_count=data.get("term_count", 0),
            request=self.request,
        )

    @extend_schema(request=StartYearSerializer, responses=AcademicYearSerializer)
    @action(detail=True, methods=["post"], url_path="set-current")
    def set_current(self, request, pk=None):
        """Make this the current year. With `continuing_staff_ids`, it also renews the team: active staff
        not in the list are archived as having left and lose their access (needs staff.archive)."""
        serializer = StartYearSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        continuing = serializer.validated_data.get("continuing_staff_ids")
        if continuing is not None and "staff.archive" not in request.permission_codes:
            raise PermissionDenied(_("Only the school's management can renew the staff."))
        with transaction.atomic():
            year = services.set_current_year(self.get_object(), request=request)
            if continuing is not None:
                services.renew_staff(
                    request.school, continuing_ids=set(continuing), year=year, request=request
                )
        return Response(self.get_serializer(year).data)


class TermViewSet(TenantModelViewSet):
    queryset = Term.objects.select_related("academic_year")
    serializer_class = TermSerializer
    pagination_class = None
    audit_module = "academics"
    filterset_fields = ["academic_year"]
    required_permissions = {"list": [], "retrieve": [], "*": MANAGE_SETTINGS}


class LevelViewSet(TenantModelViewSet):
    queryset = Level.objects.annotate(class_count=Count("classes"))
    serializer_class = LevelSerializer
    pagination_class = None
    audit_module = "academics"
    filterset_fields = ["is_active", "cycle"]
    required_permissions = {"list": [], "retrieve": [], "*": MANAGE_SETTINGS}


class ClassGroupViewSet(TenantModelViewSet):
    queryset = ClassGroup.objects.select_related("academic_year", "level", "class_teacher").annotate(
        enrolled_count=Count("enrollments", filter=Q(enrollments__status="active"), distinct=True),
        subject_count=Count("class_subjects", distinct=True),
    )
    serializer_class = ClassGroupSerializer
    audit_module = "classes"
    filterset_fields = ["academic_year", "level", "status", "class_teacher"]
    search_fields = ["name", "room", "level__name"]
    ordering_fields = ["name", "level__order", "enrolled_count"]
    ordering = ["level__order", "name"]
    required_permissions = {
        "list": ["classes.view"],
        "retrieve": ["classes.view"],
        "cards": ["classes.view", "students.view"],
        "*": ["classes.manage"],
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        return visible_classes(self.request, queryset)

    @extend_schema(responses={(200, "application/pdf"): bytes})
    @action(detail=True, methods=["get"])
    def cards(self, request, pk=None):
        """Printable ID cards for every student in the class (A4 sheets of 10)."""
        from apps.people.models import Student
        from apps.people.pdf import student_cards_pdf

        class_group = self.get_object()
        students = Student.objects.filter(
            enrollments__class_group=class_group, enrollments__status="active"
        ).order_by("last_name", "first_name")
        return pdf_response(student_cards_pdf(request.school, students), f"cartes-{class_group.name}.pdf")


class SubjectViewSet(TenantModelViewSet):
    queryset = Subject.objects.select_related("level")
    serializer_class = SubjectSerializer
    audit_module = "subjects"
    filterset_fields = ["level", "is_active"]
    search_fields = ["name", "code"]
    ordering_fields = ["name", "code"]
    required_permissions = {
        "list": ["subjects.view"],
        "retrieve": ["subjects.view"],
        "*": ["subjects.manage"],
    }


class ClassSubjectViewSet(TenantModelViewSet):
    queryset = ClassSubject.objects.select_related("class_group", "subject", "teacher")
    serializer_class = ClassSubjectSerializer
    pagination_class = None
    audit_module = "classes"
    filterset_fields = ["class_group", "subject", "teacher", "class_group__academic_year"]
    required_permissions = {"list": ["classes.view"], "retrieve": ["classes.view"], "*": ["classes.manage"]}

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        classes = visible_classes(self.request, ClassGroup.objects.filter(school=self.request.school))
        return queryset.filter(class_group__in=classes)
