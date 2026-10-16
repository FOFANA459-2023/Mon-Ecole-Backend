from django.contrib import admin

from .models import Backup


@admin.register(Backup)
class BackupAdmin(admin.ModelAdmin):
    list_display = ["started_at", "kind", "status", "engine", "size"]
    list_filter = ["status", "kind"]
