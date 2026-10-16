from datetime import timedelta

from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassGroup
from apps.academics.scoping import visible_classes
from apps.academics.services import current_year
from apps.core.permissions import HasSchoolPermission
from apps.people.models import Student

from . import alerts, selectors, services
from .models import AbsenceAlert
from .serializers import (
    AbsenceAlertSerializer,
    AbsenceParamsSerializer,
    AbsenceRowSerializer,
    ClassMonthSerializer,
    DayClassSerializer,
    DayParamsSerializer,
    FollowUpSerializer,
    MonthParamsSerializer,
    RegisterParamsSerializer,
    RegisterSheetSerializer,
    SaveRegisterSerializer,
    SaveStaffAttendanceSerializer,
    StaffSheetSerializer,
    StudentAttendanceSerializer,
    StudentParamsSerializer,
    today_or,
)

VIEW = ["attendance.view"]


class _AttendanceView(APIView):
    permission_classes = [IsAuthenticated, HasSchoolPermission]

    def params(self, serializer_class, data):
        serializer = serializer_class(data=data, context={"request": self.request})
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def visible_class(self, class_group: ClassGroup) -> ClassGroup:
        """Teachers only reach the classes they teach or lead; anything else is "not found"."""
        if not visible_classes(self.request, ClassGroup.objects.filter(pk=class_group.pk)).exists():
            raise NotFound()
        return class_group


class AttendanceDayView(_AttendanceView):
    """The classes in session on a day (the user's own, for teachers) and whether their register is taken."""

    required_permissions = {"get": VIEW}

    @extend_schema(parameters=[DayParamsSerializer], responses=DayClassSerializer(many=True))
    def get(self, request):
        day = today_or(self.params(DayParamsSerializer, request.query_params).get("date"), request.school)
        return Response(DayClassSerializer(selectors.day_overview(request, day), many=True).data)


class RegisterView(_AttendanceView):
    """A class's register for a day. GET shows it (everyone present when not taken yet); POST takes or
    corrects it. Teachers take their classes' registers on the day; earlier days need attendance.edit."""

    required_permissions = {"get": VIEW, "post": ["attendance.record"]}

    @extend_schema(parameters=[RegisterParamsSerializer], responses=RegisterSheetSerializer)
    def get(self, request):
        data = self.params(RegisterParamsSerializer, request.query_params)
        class_group = self.visible_class(data["class_group"])
        day = today_or(data.get("date"), request.school)
        return Response(RegisterSheetSerializer(selectors.register_sheet(request, class_group, day)).data)

    @extend_schema(request=SaveRegisterSerializer, responses=RegisterSheetSerializer)
    def post(self, request):
        data = self.params(SaveRegisterSerializer, request.data)
        class_group = self.visible_class(data["class_group"])
        services.save_register(class_group, data["date"], [dict(r) for r in data["records"]], request=request)
        return Response(
            RegisterSheetSerializer(selectors.register_sheet(request, class_group, data["date"])).data
        )


class ClassMonthView(_AttendanceView):
    """A month of a class's registers: a day-by-day grid and each student's totals."""

    required_permissions = {"get": VIEW}

    @extend_schema(parameters=[MonthParamsSerializer], responses=ClassMonthSerializer)
    def get(self, request):
        data = self.params(MonthParamsSerializer, request.query_params)
        year, month = (int(part) for part in data["month"].split("-"))
        class_group = self.visible_class(data["class_group"])
        return Response(ClassMonthSerializer(selectors.class_month(class_group, year, month)).data)


class AbsencesView(_AttendanceView):
    """Students with absences over a period (30 days by default), most absent first."""

    required_permissions = {"get": VIEW}

    @extend_schema(parameters=[AbsenceParamsSerializer], responses=AbsenceRowSerializer(many=True))
    def get(self, request):
        data = self.params(AbsenceParamsSerializer, request.query_params)
        date_to = today_or(data.get("date_to"), request.school)
        date_from = data.get("date_from") or date_to - timedelta(days=30)
        rows = selectors.absences(request, date_from, date_to, data.get("class_group"), data["min_absences"])
        return Response(AbsenceRowSerializer(rows, many=True).data)


class StudentAttendanceView(_AttendanceView):
    """One student's attendance for a school year: totals and the days they were absent, late or excused."""

    required_permissions = {"get": VIEW}

    @extend_schema(parameters=[StudentParamsSerializer], responses=StudentAttendanceSerializer)
    def get(self, request, pk: int):
        student = Student.objects.filter(school=request.school, pk=pk).first()
        if student is None:
            raise NotFound()
        classes = visible_classes(request, ClassGroup.objects.filter(school=request.school))
        if not student.enrollments.filter(class_group__in=classes).exists():
            raise NotFound()
        year = self.params(StudentParamsSerializer, request.query_params).get(
            "academic_year"
        ) or current_year(request.school)
        if year is None:
            raise NotFound()
        return Response(StudentAttendanceSerializer(selectors.student_summary(student, year)).data)


class StaffAttendanceView(_AttendanceView):
    """The staff register for a day: GET shows it, POST records it."""

    required_permissions = {"get": ["attendance.staff"], "post": ["attendance.staff"]}

    @extend_schema(parameters=[DayParamsSerializer], responses=StaffSheetSerializer)
    def get(self, request):
        day = today_or(self.params(DayParamsSerializer, request.query_params).get("date"), request.school)
        return Response(StaffSheetSerializer(selectors.staff_sheet(request.school, day)).data)

    @extend_schema(request=SaveStaffAttendanceSerializer, responses=StaffSheetSerializer)
    def post(self, request):
        data = self.params(SaveStaffAttendanceSerializer, request.data)
        services.save_staff_attendance(
            request.school, data["date"], [dict(e) for e in data["entries"]], request=request
        )
        return Response(StaffSheetSerializer(selectors.staff_sheet(request.school, data["date"])).data)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("status", OpenApiTypes.STR, enum=list(AbsenceAlert.AlertStatus.values)),
            OpenApiParameter("class_group", OpenApiTypes.INT),
        ]
    )
)
class AbsenceAlertViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """High-absenteeism alerts of the classes the user sees: open ones by default, newest first.

    Raised every evening from the registers (see attendance.alerts); `follow-up` closes one with a note.
    """

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    serializer_class = AbsenceAlertSerializer
    required_permissions = {"*": VIEW}

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AbsenceAlert.objects.none()
        classes = visible_classes(self.request, ClassGroup.objects.filter(school=self.request.school))
        queryset = AbsenceAlert.objects.filter(
            school=self.request.school, enrollment__class_group__in=classes
        ).select_related("enrollment__student", "enrollment__class_group", "closed_by")
        if self.action == "list":
            status = self.request.query_params.get("status") or AbsenceAlert.AlertStatus.OPEN
            queryset = queryset.filter(status=status)
            class_group = self.request.query_params.get("class_group", "")
            if class_group.isdigit():
                queryset = queryset.filter(enrollment__class_group_id=int(class_group))
        return queryset

    @extend_schema(request=FollowUpSerializer, responses=AbsenceAlertSerializer)
    @action(detail=True, methods=["post"], url_path="follow-up")
    def follow_up(self, request, pk=None):
        alert = self.get_object()
        if alert.status != AbsenceAlert.AlertStatus.OPEN:
            raise ValidationError({"detail": _("This alert is already closed.")})
        serializer = FollowUpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        alert = alerts.follow_up(alert, note=serializer.validated_data["note"], request=request)
        return Response(AbsenceAlertSerializer(alert).data)
