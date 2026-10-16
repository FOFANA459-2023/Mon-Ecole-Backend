from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import AcademicYear, ClassGroup, Level, Term
from apps.academics.scoping import visible_class_ids
from apps.academics.services import current_year
from apps.attendance.models import AbsenceAlert
from apps.core.permissions import HasSchoolPermission
from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.enrollments.models import Enrollment
from apps.finance.selectors import PaymentStatus
from apps.people.models import StaffMember

from . import selectors

# The figures are computed once a minute at most per user and filter (they cost a few queries per class).
CACHE_SECONDS = 60


class DashboardParamsSerializer(serializers.Serializer):
    academic_year = TenantPrimaryKeyRelatedField(
        queryset=AcademicYear.objects.all(), required=False, allow_null=True
    )
    term = TenantPrimaryKeyRelatedField(
        queryset=Term.objects.all(), required=False, allow_null=True, help_text="Defaults to today's term."
    )
    level = TenantPrimaryKeyRelatedField(queryset=Level.objects.all(), required=False, allow_null=True)
    class_group = TenantPrimaryKeyRelatedField(
        queryset=ClassGroup.objects.all(), required=False, allow_null=True
    )
    payment_status = serializers.ChoiceField(
        choices=[s for s in PaymentStatus.CHOICES if s != PaymentStatus.CANCELLED],
        required=False,
        allow_blank=True,
    )


class DashboardSummaryView(APIView):
    """The dashboard of one school year (ToR §5): headline numbers, charts and alerts.

    School-wide, or limited to the user's own classes when they may not see every class. Finance figures need
    finance.view, attendance attendance.view, results grades.view. Filters: term (results, attendance),
    level or class (every figure), payment status (finance).
    """

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"get": ["dashboard.view"]}

    @staticmethod
    def _attendance_today(request, classes):
        """Today's registers in the classes counted above: how many are taken, and who is present."""
        from apps.attendance.models import AttendanceRecord, ClassRegister

        today = selectors.today_for(request.school)
        counts = AttendanceRecord.objects.filter(
            register__class_group__in=classes, register__date=today
        ).aggregate(
            present=Count("id", filter=Q(status="present")),
            absent=Count("id", filter=Q(status="absent")),
            late=Count("id", filter=Q(status="late")),
            excused=Count("id", filter=Q(status="excused")),
        )
        return {
            "date": today.isoformat(),
            "registers_taken": ClassRegister.objects.filter(class_group__in=classes, date=today).count(),
            "classes": classes.count(),
            **counts,
        }

    @extend_schema(
        parameters=[
            DashboardParamsSerializer,
            OpenApiParameter("academic_year", OpenApiTypes.INT, required=False),
        ],
        responses={200: dict},
    )
    def get(self, request):
        params = DashboardParamsSerializer(data=request.query_params, context={"request": request})
        params.is_valid(raise_exception=True)
        data = params.validated_data
        school = request.school
        year = data.get("academic_year") or current_year(school)
        if year is None:
            return Response({"academic_year": None})
        key = "dashboard:{}:{}:{}".format(
            school.pk,
            request.user.pk,
            ":".join(f"{k}={getattr(v, 'pk', v)}" for k, v in sorted(data.items())),
        )
        payload = cache.get(key)
        if payload is None:
            payload = self._payload(request, year, data)
            cache.set(key, payload, CACHE_SECONDS)
        return Response(payload)

    def _payload(self, request, year, data) -> dict:
        school = request.school
        codes = request.permission_codes
        today = selectors.today_for(school)
        term, class_group = data.get("term"), data.get("class_group")
        if term is not None and term.academic_year_id != year.pk:
            term = None
        filters = selectors.Filters(
            year=year,
            term=term or selectors.default_term(year, today),
            level=data.get("level"),
            class_group=class_group if class_group and class_group.academic_year_id == year.pk else None,
            payment_status=data.get("payment_status") or "",
        )

        classes = ClassGroup.objects.filter(school=school, academic_year=year, status="active")
        # Users who only see the classes they teach (teachers) get figures for those classes only.
        class_ids = visible_class_ids(request)
        if class_ids is not None:
            classes = classes.filter(pk__in=class_ids)
        classes = selectors.scoped_classes(classes, filters)
        enrollments = Enrollment.objects.filter(
            school=school, academic_year=year, status="active", class_group__in=classes
        )
        gender = enrollments.aggregate(
            male=Count("id", filter=Q(student__gender="M")),
            female=Count("id", filter=Q(student__gender="F")),
            total=Count("id"),
        )
        by_level = list(
            enrollments.values(
                "class_group__level_id", "class_group__level__name", "class_group__level__order"
            )
            .annotate(count=Count("id"))
            .order_by("class_group__level__order", "class_group__level__name")
        )
        capacity = sum(c for c in classes.values_list("capacity", flat=True) if c)
        staff = StaffMember.objects.filter(school=school, status="active")
        since = today - timedelta(days=30)
        sees_attendance = "attendance.view" in codes

        payload: dict = {
            "academic_year": {"id": year.id, "name": year.name},
            "scope": "school" if class_ids is None else "my_classes",
            "filters": {
                "term": filters.term.pk if filters.term else None,
                "term_name": filters.term.name if filters.term else "",
                "level": filters.level.pk if filters.level else None,
                "class_group": filters.class_group.pk if filters.class_group else None,
                "payment_status": filters.payment_status,
            },
            "students": gender["total"],
            "students_male": gender["male"],
            "students_female": gender["female"],
            "classes": classes.count(),
            "capacity": capacity or None,
            "teachers": staff.filter(staff_type="teacher").count(),
            "staff": staff.count(),
            "new_enrollments_30d": enrollments.filter(enrollment_date__gte=since).count(),
            "attendance_today": self._attendance_today(request, classes) if sees_attendance else None,
            # High absenteeism to follow up (attendance.alerts), in the classes counted here.
            "absence_alerts": (
                AbsenceAlert.objects.filter(
                    school=school, status=AbsenceAlert.AlertStatus.OPEN, enrollment__class_group__in=classes
                ).count()
                if sees_attendance
                else None
            ),
            "by_level": [
                {
                    "level_id": row["class_group__level_id"],
                    "level": row["class_group__level__name"],
                    "count": row["count"],
                }
                for row in by_level
            ],
            "by_class": selectors.by_class(classes),
            "finance": selectors.finance(school, classes, filters, today)
            if "finance.view" in codes
            else None,
            "attendance": selectors.attendance(classes, filters, today) if sees_attendance else None,
            "performance": selectors.performance(classes, filters.term) if "grades.view" in codes else None,
            "missing_grades": (
                selectors.missing_grades(school, classes, filters.term) if "grades.view" in codes else None
            ),
            "upcoming_exams": selectors.upcoming_exams(school, classes, today),
            "announcements": selectors.latest_announcements(request),
        }
        return payload
