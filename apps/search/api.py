from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.academics.models import ClassGroup
from apps.academics.scoping import visible_class_ids, visible_classes
from apps.academics.services import current_year
from apps.core.permissions import HasSchoolPermission
from apps.enrollments.models import Enrollment
from apps.people.models import Guardian, StaffMember, Student

LIMIT = 6


def _terms_query(q: str, fields: list[str]) -> Q:
    """Every word must match at least one field (so "diallo awa" finds Awa Diallo)."""
    query = Q()
    for word in q.split()[:5]:
        word_q = Q()
        for field in fields:
            word_q |= Q(**{f"{field}__icontains": word})
        query &= word_q
    return query


class GlobalSearchView(APIView):
    """One search box for the whole school: students, guardians, staff and classes."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": []}

    @extend_schema(parameters=[OpenApiParameter("q", str, required=True)], responses={200: dict})
    def get(self, request):
        q = request.query_params.get("q", "").strip()
        results = {"students": [], "guardians": [], "staff": [], "classes": []}
        if len(q) < 2:
            return Response(results)
        school = request.school
        perms = request.permission_codes
        class_ids = visible_class_ids(request)

        if "students.view" in perms:
            students = Student.objects.filter(school=school).filter(
                _terms_query(q, ["first_name", "last_name", "student_number", "phone"])
            )
            if class_ids is not None:
                students = students.filter(
                    id__in=Enrollment.objects.filter(status="active", class_group_id__in=class_ids).values(
                        "student_id"
                    )
                )
            found = list(students.order_by("last_name", "first_name")[:LIMIT])
            active = {
                e.student_id: e
                for e in Enrollment.objects.filter(student__in=found, status="active").select_related(
                    "class_group"
                )
            }
            results["students"] = [
                {
                    "id": s.id,
                    "title": s.full_name,
                    "subtitle": " · ".join(
                        filter(
                            None, [s.student_number, active[s.id].class_group.name if s.id in active else ""]
                        )
                    ),
                    "status": s.status,
                }
                for s in found
            ]

            guardians = (
                Guardian.objects.filter(school=school)
                .filter(_terms_query(q, ["first_name", "last_name", "phone", "alt_phone", "email"]))
                .prefetch_related("student_links__student")
            )
            if class_ids is not None:
                guardians = guardians.filter(
                    student_links__student__enrollments__status="active",
                    student_links__student__enrollments__class_group_id__in=class_ids,
                ).distinct()
            results["guardians"] = [
                {
                    "id": g.id,
                    "title": g.full_name,
                    "subtitle": " · ".join(
                        filter(
                            None,
                            [
                                g.phone,
                                ", ".join(link.student.full_name for link in g.student_links.all()[:3]),
                            ],
                        )
                    ),
                    "student_id": next((link.student_id for link in g.student_links.all()), None),
                }
                for g in guardians[:LIMIT]
            ]

        if "staff.view" in perms:
            staff = StaffMember.objects.filter(school=school).filter(
                _terms_query(q, ["first_name", "last_name", "employee_number", "phone", "email"])
            )
            results["staff"] = [
                {
                    "id": s.id,
                    "title": s.full_name,
                    "subtitle": " · ".join(filter(None, [s.employee_number, s.position])),
                    "status": s.status,
                }
                for s in staff[:LIMIT]
            ]

        if "classes.view" in perms:
            classes = (
                ClassGroup.objects.filter(school=school, academic_year=current_year(school))
                .filter(_terms_query(q, ["name", "level__name", "room"]))
                .select_related("level")
            )
            classes = visible_classes(request, classes)
            results["classes"] = [
                {"id": c.id, "title": c.name, "subtitle": c.level.name} for c in classes[:LIMIT]
            ]
        return Response(results)
