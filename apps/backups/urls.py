from django.urls import path
from rest_framework.routers import SimpleRouter

from . import api

router = SimpleRouter()
router.register("platform/backups", api.PlatformBackupViewSet, basename="platform-backup")

urlpatterns = [*router.urls, path("backup-status/", api.BackupStatusView.as_view(), name="backup-status")]
