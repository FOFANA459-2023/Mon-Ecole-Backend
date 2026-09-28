from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.core.pdf import pdf_response
from apps.core.viewsets import TenantModelViewSet
from apps.people.models import StudentGuardian
from apps.people.serializers import GuardianInputSerializer

from . import services
from .models import Enrollment
from .pdf import enrollment_form_pdf
from .serializers import (
    CancelSerializer,
    ChangeClassSerializer,
    EnrollExistingSerializer,
    EnrollmentSerializer,
    PromoteSerializer,
    RegistrationSerializer,
    WithdrawSerializer,
)


class EnrollmentViewSet(TenantModelViewSet):
    queryset = Enrollment.objects.select_related("student", "academic_year", "class_group__level")
    serializer_class = EnrollmentSerializer
    audit_module = "enrollments"
    filterset_fields = {
        "academic_year": ["exact"],
        "class_group": ["exact"],
        "class_group__level": ["exact"],
        "status": ["exact", "in"],
        "kind": ["exact"],
        "student": ["exact"],
        "enrollment_date": ["gte", "lte"],
    }
    search_fields = ["student__first_name", "student__last_name", "student__student_number"]
    ordering_fields = ["enrollment_date", "student__last_name", "created_at"]
    ordering = ["-enrollment_date", "-id"]
    http_method_names = ["get", "post", "patch", "head", "options"]
    required_permissions = {
        "list": ["enrollments.view"],
        "retrieve": ["enrollments.view"],
        "create": ["enrollments.create"],
        "register": ["enrollments.create"],
        "partial_update": ["enrollments.update"],
        "change_class": ["enrollments.update"],
        "withdraw": ["enrollments.update"],
        "cancel": ["enrollments.cancel"],
        "promote": ["enrollments.create"],
        "form": ["enrollments.view"],
    }

    def _out(self, enrollment, code=status.HTTP_200_OK):
        return Response(
            EnrollmentSerializer(enrollment, context=self.get_serializer_context()).data, status=code
        )

    @extend_schema(request=EnrollExistingSerializer, responses={201: EnrollmentSerializer})
    def create(self, request, *args, **kwargs):
        serializer = EnrollExistingSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        enrollment = services.enroll(data.pop("student"), data.pop("class_group"), request=request, **data)
        return self._out(enrollment, status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        # Only descriptive fields are editable; moves and departures go through their own actions.
        allowed = {"enrollment_date", "kind", "previous_school", "notes"}
        unexpected = set(serializer.validated_data) - allowed
        if unexpected:
            raise ValidationError({field: ["Not editable here."] for field in unexpected})
        super().perform_update(serializer)

    @extend_schema(request=RegistrationSerializer, responses={201: EnrollmentSerializer})
    @action(detail=False, methods=["post"])
    def register(self, request):
        serializer = RegistrationSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get("student") and "students.create" not in request.permission_codes:
            raise PermissionDenied()
        guardians = []
        for item in data.get("guardians", []):
            guardian, guardian_fields, link_fields = GuardianInputSerializer.split(item)
            guardians.append({"guardian": guardian, "guardian_data": guardian_fields, "link": link_fields})
        enrollment = services.register_student(
            request.school,
            class_group=data["class_group"],
            student=data.get("student_id"),
            student_data=dict(data["student"]) if data.get("student") else None,
            guardians=guardians,
            enrollment_date=data.get("enrollment_date"),
            kind=data["kind"],
            previous_school=data["previous_school"],
            notes=data["notes"],
            request=request,
        )
        return self._out(enrollment, status.HTTP_201_CREATED)

    @extend_schema(request=ChangeClassSerializer, responses=EnrollmentSerializer)
    @action(detail=True, methods=["post"], url_path="change-class")
    def change_class(self, request, pk=None):
        serializer = ChangeClassSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        new = services.change_class(
            self.get_object(),
            data["class_group"],
            on=data.get("date"),
            reason=data["reason"],
            request=request,
        )
        return self._out(new)

    @extend_schema(request=WithdrawSerializer, responses=EnrollmentSerializer)
    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        serializer = WithdrawSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        enrollment = services.withdraw(
            self.get_object(),
            on=data.get("date"),
            reason=data["reason"],
            transfer_to=data["transfer_to"],
            request=request,
        )
        return self._out(enrollment)

    @extend_schema(request=CancelSerializer, responses=EnrollmentSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        serializer = CancelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._out(
            services.cancel(self.get_object(), reason=serializer.validated_data["reason"], request=request)
        )

    @extend_schema(
        request=PromoteSerializer,
        responses=inline_serializer(
            "PromoteResult",
            {
                "promoted": serializers.IntegerField(),
                "skipped": serializers.ListField(child=serializers.DictField()),
            },
        ),
    )
    @action(detail=False, methods=["post"])
    def promote(self, request):
        serializer = PromoteSerializer(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        enrollments = None
        if data.get("enrollment_ids") is not None:
            enrollments = list(
                Enrollment.objects.filter(
                    school=request.school, pk__in=data["enrollment_ids"]
                ).select_related("student")
            )
            if len(enrollments) != len(set(data["enrollment_ids"])):
                raise ValidationError({"enrollment_ids": ["Unknown enrolment."]})
        result = services.promote(
            data["from_class"],
            data["to_class"],
            enrollments=enrollments,
            on=data.get("date"),
            request=request,
        )
        return Response(result)

    @extend_schema(responses={(200, "application/pdf"): bytes})
    @action(detail=True, methods=["get"])
    def form(self, request, pk=None):
        enrollment = (
            self.get_queryset()
            .select_related("school", "student", "academic_year", "class_group__level")
            .prefetch_related(
                Prefetch(
                    "student__guardian_links", queryset=StudentGuardian.objects.select_related("guardian")
                )
            )
            .get(pk=self.get_object().pk)
        )
        return pdf_response(
            enrollment_form_pdf(enrollment), f"inscription-{enrollment.student.student_number}.pdf"
        )
