from django.contrib import admin

from .models import Notification, NotificationPreference, OutboundEmail


@admin.register(OutboundEmail)
class OutboundEmailAdmin(admin.ModelAdmin):
    list_display = ["created_at", "school", "audience", "kind", "to", "status"]
    list_filter = ["status", "audience", "kind"]


admin.site.register(Notification)
admin.site.register(NotificationPreference)
