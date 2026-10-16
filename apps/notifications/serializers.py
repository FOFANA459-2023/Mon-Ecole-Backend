from rest_framework import serializers

from .models import Kind, Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "link", "read_at", "created_at"]


class PreferenceSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Kind.choices)
    label = serializers.CharField(read_only=True)  # type: ignore[assignment]  # "label" is also a Field attribute
    in_app = serializers.BooleanField()
    email = serializers.BooleanField()


class UnreadCountSerializer(serializers.Serializer):
    unread = serializers.IntegerField()
