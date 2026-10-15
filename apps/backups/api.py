from django.db import transaction
from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.core.permissions import HasSchoolPermission
from apps.schools.platform import IsPlatformOwner

from . import services
from .models import Backup


class BackupSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    has_file = serializers.SerializerMethodField()

    class Meta:
        model = Backup
        fields = [
            "id",
            "kind",
            "status",
            "engine",
            "size",
            "error",
            "started_at",
            "finished_at",
            "created_by_name",
            "has_file",
        ]

    def get_created_by_name(self, obj) -> str:
        return obj.created_by.full_name if obj.created_by else ""

    def get_has_file(self, obj) -> bool:
        return bool(obj.file)


class PlatformBackupViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, viewsets.GenericViewSet):
    """Backups of the whole platform (every school): history, "back up now", and download. Owner only."""

    queryset = Backup.objects.select_related("created_by")
    serializer_class = BackupSerializer
    permission_classes = [IsAuthenticated, IsPlatformOwner]

    @extend_schema(request=None, responses={202: BackupSerializer})
    def create(self, request, *args, **kwargs):
        if Backup.objects.filter(status=Backup.Status.RUNNING).exists():
            raise ValidationError({"detail": "A backup is already running."})
        backup = Backup.objects.create(kind=Backup.Kind.MANUAL, created_by=request.user)
        audit.record(
            "create", request=request, instance=backup, module="backups", summary="Manual backup started"
        )
        from .tasks import run_backup

        transaction.on_commit(lambda: run_backup.delay(backup.pk))
        return Response(BackupSerializer(backup).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(responses={(200, "application/octet-stream"): bytes})
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        backup = self.get_object()
        if not backup.file:
            raise NotFound()
        audit.record(
            "export",
            request=request,
            instance=backup,
            module="backups",
            summary=f"Backup #{backup.pk} downloaded",
        )
        return FileResponse(
            backup.file.open("rb"), as_attachment=True, filename=backup.file.name.split("/")[-1]
        )


class BackupStatusSerializer(serializers.Serializer):
    last_success_at = serializers.DateTimeField(allow_null=True)
    last_status = serializers.CharField(allow_blank=True)
    last_attempt_at = serializers.DateTimeField(allow_null=True)
    keep_days = serializers.IntegerField()
    every_hours = serializers.IntegerField()


class BackupStatusView(APIView):
    """When the platform was last backed up (school settings show it to those who manage settings)."""

    permission_classes = [IsAuthenticated, HasSchoolPermission]
    required_permissions: dict[str, list[str]] = {"get": ["settings.manage"]}

    @extend_schema(responses=BackupStatusSerializer)
    def get(self, request):
        from django.conf import settings

        success = services.last_success()
        last = Backup.objects.order_by("-started_at").first()
        return Response(
            {
                "last_success_at": success.finished_at if success else None,
                "last_status": last.status if last else "",
                "last_attempt_at": last.started_at if last else None,
                "keep_days": settings.BACKUP_KEEP_DAYS,
                "every_hours": settings.BACKUP_EVERY_HOURS,
            }
        )
