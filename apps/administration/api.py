from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view

from apps.core.viewsets import TenantModelViewSet

from .models import Announcement, CalendarEvent, Department
from .serializers import AnnouncementSerializer, CalendarEventSerializer, DepartmentSerializer

MANAGE = ["administration.manage"]
# Every member reads them (staff forms pick a department; everyone gets the announcements and the calendar).
READ_MANAGE = {"list": [], "retrieve": [], "*": MANAGE}


class DepartmentViewSet(TenantModelViewSet):
    queryset = Department.objects.select_related("head").annotate(
        staff_count=Count("staff", filter=Q(staff__status="active"))
    )
    serializer_class = DepartmentSerializer
    pagination_class = None
    audit_module = "administration"
    required_permissions = READ_MANAGE


def visible_announcements(request, queryset):
    """What a member reads: published, not expired, for everyone or one of their roles."""
    now = timezone.now()
    queryset = queryset.filter(publish_at__lte=now).filter(
        Q(expires_on__isnull=True) | Q(expires_on__gte=timezone.localdate())
    )
    membership = getattr(request, "membership", None)
    if membership is None:  # the platform owner reads everything
        return queryset
    role_ids = [role.pk for role in membership.roles.all()]
    return queryset.filter(Q(roles__isnull=True) | Q(roles__in=role_ids)).distinct()


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                "manage",
                OpenApiTypes.BOOL,
                description="Every announcement, scheduled and expired too (administration.manage).",
            )
        ]
    )
)
class AnnouncementViewSet(TenantModelViewSet):
    """School announcements. Members read the published ones meant for them; managers write them."""

    queryset = Announcement.objects.select_related("created_by").prefetch_related("roles")
    serializer_class = AnnouncementSerializer
    audit_module = "administration"
    required_permissions = READ_MANAGE
    search_fields = ["title", "body"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset
        manager = "administration.manage" in self.request.permission_codes
        if manager and (self.action != "list" or self.request.query_params.get("manage") in ("1", "true")):
            return queryset
        return visible_announcements(self.request, queryset)

    def perform_update(self, serializer):
        serializer.validated_data["updated_by"] = self.request.user
        super().perform_update(serializer)


@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("date_from", OpenApiTypes.DATE, description="Events ending on or after."),
            OpenApiParameter("date_to", OpenApiTypes.DATE, description="Events starting on or before."),
            OpenApiParameter("kind", OpenApiTypes.STR, enum=list(CalendarEvent.Kind.values)),
            OpenApiParameter("level", OpenApiTypes.INT, description="Whole-school events and this level's."),
        ]
    )
)
class CalendarEventViewSet(TenantModelViewSet):
    """The school calendar: exams, holidays, meetings, events and deadlines."""

    queryset = CalendarEvent.objects.prefetch_related("levels")
    serializer_class = CalendarEventSerializer
    pagination_class = None
    audit_module = "administration"
    required_permissions = READ_MANAGE

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False) or self.action != "list":
            return queryset
        params = self.request.query_params
        if params.get("date_from"):
            queryset = queryset.filter(end_date__gte=params["date_from"])
        if params.get("date_to"):
            queryset = queryset.filter(start_date__lte=params["date_to"])
        if params.get("kind") in CalendarEvent.Kind.values:
            queryset = queryset.filter(kind=params["kind"])
        if params.get("level", "").isdigit():
            queryset = queryset.filter(Q(levels__isnull=True) | Q(levels=int(params["level"]))).distinct()
        return queryset[:500]
