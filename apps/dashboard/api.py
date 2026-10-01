from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import AcademicYear, ClassGroup
from apps.academics.scoping import visible_class_ids
from apps.academics.services import current_year
from apps.core.permissions import HasSchoolPermission
from apps.enrollments.models import Enrollment
from apps.people.models import StaffMember


class DashboardSummaryView(APIView):
    """Headline numbers for the dashboard, for one academic year: school-wide, or limited to the user's
    own classes when they may not see every class (see academics.scoping)."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions = {"get": ["dashboard.view"]}

    @staticmethod
    def _attendance_today(request, classes):
        """Today's registers in the classes counted above: how many are taken, and who is present."""
        from apps.attendance.models import AttendanceRecord, ClassRegister
        from apps.attendance.services import school_today

        today = school_today(request.school)
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

    @extend_schema(parameters=[OpenApiParameter("academic_year", int, required=False)], responses={200: dict})
    def get(self, request):
        school = request.school
        year_id = request.query_params.get("academic_year", "")
        year = (
            AcademicYear.objects.filter(school=school, pk=int(year_id)).first()
            if year_id.isdigit()
            else current_year(school)
        )
        if year is None:
            return Response({"academic_year": None})

        enrollments = Enrollment.objects.filter(school=school, academic_year=year, status="active")
        classes = ClassGroup.objects.filter(school=school, academic_year=year, status="active")
        # Users who only see the classes they teach (teachers) get figures for those classes only.
        class_ids = visible_class_ids(request)
        if class_ids is not None:
            enrollments = enrollments.filter(class_group_id__in=class_ids)
            classes = classes.filter(pk__in=class_ids)
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
        since = timezone.localdate() - timedelta(days=30)
        attendance = (
            self._attendance_today(request, classes)
            if "attendance.view" in request.permission_codes
            else None
        )

        return Response(
            {
                "academic_year": {"id": year.id, "name": year.name},
                "scope": "school" if class_ids is None else "my_classes",
                "students": gender["total"],
                "students_male": gender["male"],
                "students_female": gender["female"],
                "classes": classes.count(),
                "capacity": capacity or None,
                "teachers": staff.filter(staff_type="teacher").count(),
                "staff": staff.count(),
                "new_enrollments_30d": enrollments.filter(enrollment_date__gte=since).count(),
                "attendance_today": attendance,
                "by_level": [
                    {
                        "level_id": row["class_group__level_id"],
                        "level": row["class_group__level__name"],
                        "count": row["count"],
                    }
                    for row in by_level
                ],
            }
        )
