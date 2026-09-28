from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "action",
            "module",
            "entity_type",
            "entity_id",
            "summary",
            "old_values",
            "new_values",
            "user",
            "user_name",
            "ip",
            "user_agent",
            "request_id",
            "created_at",
        ]
        read_only_fields = fields

    def get_user_name(self, obj) -> str:
        return obj.user.get_full_name() or obj.user.email if obj.user else ""
