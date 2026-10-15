from django.db import transaction
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import HasSchoolPermission

from .models import Kind, Notification, NotificationPreference
from .serializers import NotificationSerializer, PreferenceSerializer, UnreadCountSerializer
from .services import preferences


@extend_schema_view(
    list=extend_schema(parameters=[OpenApiParameter("unread", OpenApiTypes.BOOL, description="Unread only.")])
)
class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The signed-in user's notifications in the current school, newest first."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"*": []}

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()
        queryset = Notification.objects.filter(user=self.request.user, school=self.request.school)
        if self.action == "list" and self.request.query_params.get("unread") in ("1", "true"):
            queryset = queryset.filter(read_at__isnull=True)
        return queryset

    @extend_schema(responses=UnreadCountSerializer)
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"unread": self.get_queryset().filter(read_at__isnull=True).count()})

    @extend_schema(request=None, responses=NotificationSerializer)
    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])
        return Response(NotificationSerializer(notification).data)

    @extend_schema(request=None, responses=UnreadCountSerializer)
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"unread": 0})

    def _preferences(self, request):
        return [
            {"kind": kind, "label": str(Kind(kind).label), "in_app": in_app, "email": email}
            for kind, (in_app, email) in preferences(request.user).items()
        ]

    @extend_schema(methods=["get"], responses=PreferenceSerializer(many=True))
    @extend_schema(
        methods=["patch"], request=PreferenceSerializer(many=True), responses=PreferenceSerializer(many=True)
    )
    @action(detail=False, methods=["get", "patch"])
    def preferences(self, request):
        """What the user wants for each kind of notification: in the app, by email."""
        if request.method == "PATCH":
            serializer = PreferenceSerializer(data=request.data, many=True)
            serializer.is_valid(raise_exception=True)
            with transaction.atomic():
                for row in serializer.validated_data:
                    NotificationPreference.objects.update_or_create(
                        user=request.user,
                        kind=row["kind"],
                        defaults={"in_app": row["in_app"], "email": row["email"]},
                    )
        return Response(PreferenceSerializer(self._preferences(request), many=True).data)
