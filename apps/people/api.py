import django_filters
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.academics.models import ClassGroup, ClassSubject
from apps.academics.scoping import sees_all_classes, visible_class_ids
from apps.academics.services import current_year
from apps.core.exports import tabular_response
from apps.core.viewsets import TenantModelViewSet
from apps.enrollments.models import Enrollment

from . import pdf, services
from .models import Guardian, StaffMember, Student, StudentGuardian
from .serializers import (
    GrantAccessSerializer,
    GuardianInputSerializer,
    GuardianLinkSerializer,
    GuardianLinkUpdateSerializer,
    GuardianSerializer,
    PhotoSerializer,
    StaffCreateSerializer,
    StaffSerializer,
    StudentListSerializer,
    StudentSerializer,
    TeachingSerializer,
)

ACTIVE_ENROLLMENTS = Prefetch(
    "enrollments",
    queryset=Enrollment.objects.filter(status="active").select_related("academic_year", "class_group__level"),
    to_attr="active_enrollments",
)
GUARDIAN_LINKS = Prefetch("guardian_links", queryset=StudentGuardian.objects.select_related("guardian"))


class StudentFilter(django_filters.FilterSet):
    academic_year = django_filters.NumberFilter(method="by_enrollment")
    class_group = django_filters.NumberFilter(method="by_enrollment")
    level = django_filters.NumberFilter(method="by_enrollment")
    enrolled = django_filters.BooleanFilter(method="by_enrollment", label="Has an active enrolment")

    class Meta:
        model = Student
        fields = ["status", "gender"]

    def by_enrollment(self, queryset, name, value):
        return queryset  # combined in filter_queryset so all conditions apply to the same enrolment

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        data = self.form.cleaned_data
        conditions = {}
        if data.get("academic_year"):
            conditions["academic_year_id"] = data["academic_year"]
        if data.get("class_group"):
            conditions["class_group_id"] = data["class_group"]
        if data.get("level"):
            conditions["class_group__level_id"] = data["level"]
        enrolled = data.get("enrolled")
        active = Enrollment.objects.filter(status="active", **conditions).values("student_id")
        if conditions or enrolled is True:
            queryset = queryset.filter(id__in=active)
        elif enrolled is False:
            queryset = queryset.exclude(id__in=active)
        return queryset


def student_export_rows(students, language: str):
    french = language == "fr"
    headers = (
        [
            "Matricule",
            "Nom",
            "Prénom",
            "Sexe",
            "Date de naissance",
            "Classe",
            "Niveau",
            "Année",
            "Parent / tuteur",
            "Téléphone du parent",
            "Statut",
        ]
        if french
        else [
            "Student number",
            "Last name",
            "First name",
            "Gender",
            "Date of birth",
            "Class",
            "Level",
            "Year",
            "Guardian",
            "Guardian phone",
            "Status",
        ]
    )
    rows = []
    for s in students:
        enrolment = StudentListSerializer().get_current_enrollment(s)
        guardian = StudentListSerializer().get_primary_guardian(s)
        rows.append(
            [
                s.student_number,
                s.last_name,
                s.first_name,
                s.gender,
                s.date_of_birth.isoformat() if s.date_of_birth else "",
                enrolment["class_name"] if enrolment else "",
                enrolment["level_name"] if enrolment else "",
                enrolment["academic_year_name"] if enrolment else "",
                guardian["full_name"] if guardian else "",
                guardian["phone"] if guardian else "",
                s.status,
            ]
        )
    return headers, rows


class StudentViewSet(TenantModelViewSet):
    queryset = Student.objects.prefetch_related(ACTIVE_ENROLLMENTS, GUARDIAN_LINKS)  # type: ignore[arg-type]
    audit_module = "students"
    filterset_class = StudentFilter
    search_fields = [
        "first_name",
        "last_name",
        "student_number",
        "phone",
        "guardian_links__guardian__last_name",
        "guardian_links__guardian__first_name",
        "guardian_links__guardian__phone",
    ]
    ordering_fields = ["last_name", "first_name", "student_number", "created_at"]
    ordering = ["last_name", "first_name"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]  # DELETE: guardian links only
    required_permissions = {
        "list": ["students.view"],
        "retrieve": ["students.view"],
        "create": ["students.create"],
        "partial_update": ["students.update"],
        "archive": ["students.archive"],
        "restore": ["students.archive"],
        "photo": ["students.update"],
        "guardians": ["students.view"],
        "add_guardian": ["students.update"],
        "guardian_link": ["students.update"],
        "card": ["students.view"],
        "export": ["students.export"],
    }

    def get_serializer_class(self):
        return StudentListSerializer if self.action in {"list", "export"} else StudentSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        class_ids = visible_class_ids(self.request)
        if class_ids is not None:
            queryset = queryset.filter(
                id__in=Enrollment.objects.filter(status="active", class_group_id__in=class_ids).values(
                    "student_id"
                )
            )
        return queryset

    def destroy(self, request, *args, **kwargs):
        raise MethodNotAllowed("DELETE", detail="Students are archived, never deleted.")

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        serializer.instance = services.create_student(self.request.school, data=data, request=self.request)

    def perform_update(self, serializer):
        data = dict(serializer.validated_data)
        data.pop("student_number", None)
        serializer.instance = services.update_student(serializer.instance, data=data, request=self.request)

    def _detail(self, student, status_code=status.HTTP_200_OK):
        return Response(
            StudentSerializer(student, context=self.get_serializer_context()).data, status=status_code
        )

    @extend_schema(request=None, responses=StudentSerializer)
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self._detail(services.archive_student(self.get_object(), request=request))

    @extend_schema(request=None, responses=StudentSerializer)
    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        return self._detail(services.restore_student(self.get_object(), request=request))

    @extend_schema(request={"multipart/form-data": PhotoSerializer}, responses=StudentSerializer)
    @action(detail=True, methods=["post"])
    def photo(self, request, pk=None):
        serializer = PhotoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = services.set_photo(
            self.get_object(), serializer.validated_data["file"], module="students", request=request
        )
        return self._detail(student)

    @extend_schema(responses=GuardianLinkSerializer(many=True))
    @action(detail=True, methods=["get"])
    def guardians(self, request, pk=None):
        links = (
            self.get_object()
            .guardian_links.select_related("guardian")
            .prefetch_related("guardian__student_links__student")
        )
        return Response(GuardianLinkSerializer(links, many=True).data)

    @extend_schema(request=GuardianInputSerializer, responses=GuardianLinkSerializer)
    @guardians.mapping.post
    def add_guardian(self, request, pk=None):
        student = self.get_object()
        serializer = GuardianInputSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        guardian, guardian_fields, link_fields = GuardianInputSerializer.split(serializer.validated_data)
        link = services.link_guardian(
            student, guardian=guardian, guardian_data=guardian_fields, request=request, **link_fields
        )
        return Response(GuardianLinkSerializer(link).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=GuardianLinkUpdateSerializer, responses=GuardianLinkSerializer)
    @action(detail=True, methods=["patch", "delete"], url_path=r"guardians/(?P<link_id>\d+)")
    def guardian_link(self, request, pk=None, link_id=None):
        student = self.get_object()
        link = get_object_or_404(StudentGuardian, pk=link_id, student=student)
        if request.method == "DELETE":
            services.unlink_guardian(link, request=request)
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = GuardianLinkUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        link_data = {
            k: data.pop(k) for k in ("relationship", "is_primary", "is_financial_contact") if k in data
        }
        link = services.update_guardian_link(link, data=link_data, guardian_data=data, request=request)
        return Response(GuardianLinkSerializer(link).data)

    @extend_schema(responses={(200, "application/pdf"): bytes})
    @action(detail=True, methods=["get"])
    def card(self, request, pk=None):
        student = self.get_object()
        content = pdf.student_cards_pdf(request.school, [student])
        return pdf.pdf_response(content, f"carte-{student.student_number}.pdf")

    @extend_schema(
        parameters=[OpenApiParameter("file_format", enum=["xlsx", "csv"], required=False)],
        responses={(200, "application/octet-stream"): bytes},
    )
    @action(detail=False, methods=["get"])
    def export(self, request):
        students = self.filter_queryset(self.get_queryset())[:10000]
        headers, rows = student_export_rows(students, request.school.default_language)
        return tabular_response(headers, rows, request.query_params.get("file_format", "xlsx"), "students")


class GuardianViewSet(TenantModelViewSet):
    queryset = Guardian.objects.prefetch_related(
        Prefetch("student_links", queryset=StudentGuardian.objects.select_related("student"))  # type: ignore[arg-type]
    )
    serializer_class = GuardianSerializer
    audit_module = "students"
    search_fields = ["first_name", "last_name", "phone", "alt_phone", "email"]
    http_method_names = ["get", "patch", "head", "options"]
    required_permissions = {
        "list": ["students.view"],
        "retrieve": ["students.view"],
        "*": ["students.update"],
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False) or sees_all_classes(self.request):
            return queryset
        class_ids = visible_class_ids(self.request) or []
        students = Enrollment.objects.filter(status="active", class_group_id__in=class_ids).values(
            "student_id"
        )
        return queryset.filter(student_links__student_id__in=students).distinct()


class StaffFilter(django_filters.FilterSet):
    has_access = django_filters.BooleanFilter(field_name="user", lookup_expr="isnull", exclude=True)

    class Meta:
        model = StaffMember
        fields = ["status", "staff_type"]


class StaffViewSet(TenantModelViewSet):
    queryset = StaffMember.objects.all()
    serializer_class = StaffSerializer
    audit_module = "staff"
    filterset_class = StaffFilter
    search_fields = [
        "first_name",
        "last_name",
        "employee_number",
        "phone",
        "email",
        "position",
        "specialization",
    ]
    ordering_fields = ["last_name", "first_name", "employee_number", "employment_date"]
    ordering = ["last_name", "first_name"]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    http_method_names = ["get", "post", "patch", "head", "options"]
    required_permissions = {
        "list": ["staff.view"],
        "retrieve": ["staff.view"],
        # Every staff member added gets a login, so adding one also needs the right to manage users.
        "create": ["staff.create", "users.manage"],
        "partial_update": ["staff.update"],
        "teaching": ["staff.update", "classes.manage"],
        "photo": ["staff.update"],
        "archive": ["staff.archive"],
        "restore": ["staff.archive"],
        "grant_access": ["users.manage"],
    }

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        year = current_year(self.request.school)
        return queryset.prefetch_related(
            Prefetch(
                "class_subjects",
                queryset=ClassSubject.objects.filter(class_group__academic_year=year).select_related(
                    "class_group", "subject"
                ),
                to_attr="current_assignments",
            ),
            Prefetch(
                "homeroom_classes",
                queryset=ClassGroup.objects.filter(academic_year=year),
                to_attr="current_homerooms",
            ),
        )

    def get_serializer_class(self):
        return StaffCreateSerializer if self.action == "create" else StaffSerializer

    @extend_schema(request=StaffCreateSerializer, responses={201: StaffSerializer})
    def create(self, request, *args, **kwargs):
        serializer = StaffCreateSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        role = data.pop("role_id")
        teaching = data.pop("teaching", None) or {"homeroom_class_ids": [], "subjects": []}
        staff = services.add_staff_with_access(
            request.school,
            data=data,
            role=role,
            homeroom_classes=teaching["homeroom_class_ids"],
            subjects=teaching["subjects"],
            request=request,
        )
        response = self._detail(staff)
        response.status_code = status.HTTP_201_CREATED
        return response

    @extend_schema(request=TeachingSerializer, responses=StaffSerializer)
    @action(detail=True, methods=["post"])
    def teaching(self, request, pk=None):
        """Replace the classes this teacher leads and the subjects they teach this school year."""
        serializer = TeachingSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        staff = services.set_teaching(
            self.get_object(),
            homeroom_classes=serializer.validated_data["homeroom_class_ids"],
            subjects=serializer.validated_data["subjects"],
            request=request,
        )
        return self._detail(staff)

    def perform_update(self, serializer):
        data = dict(serializer.validated_data)
        data.pop("employee_number", None)
        serializer.instance = services.update_staff(serializer.instance, data=data, request=self.request)

    def _detail(self, staff):
        return Response(
            StaffSerializer(self.get_queryset().get(pk=staff.pk), context=self.get_serializer_context()).data
        )

    @extend_schema(request=None, responses=StaffSerializer)
    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        return self._detail(services.set_staff_status(self.get_object(), archived=True, request=request))

    @extend_schema(request=None, responses=StaffSerializer)
    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        return self._detail(services.set_staff_status(self.get_object(), archived=False, request=request))

    @extend_schema(request={"multipart/form-data": PhotoSerializer}, responses=StaffSerializer)
    @action(detail=True, methods=["post"])
    def photo(self, request, pk=None):
        serializer = PhotoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff = services.set_photo(
            self.get_object(), serializer.validated_data["file"], module="staff", request=request
        )
        return self._detail(staff)

    @extend_schema(request=GrantAccessSerializer, responses=StaffSerializer)
    @action(detail=True, methods=["post"], url_path="grant-access")
    def grant_access(self, request, pk=None):
        serializer = GrantAccessSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        staff = services.grant_staff_access(
            self.get_object(), roles=serializer.validated_data["role_ids"], request=request
        )
        return self._detail(staff)
