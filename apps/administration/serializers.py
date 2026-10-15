from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.academics.models import Level
from apps.accounts.models import Role
from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.people.models import StaffMember

from .models import Announcement, CalendarEvent, Department


class DepartmentSerializer(serializers.ModelSerializer):
    head = TenantPrimaryKeyRelatedField(queryset=StaffMember.objects.all(), required=False, allow_null=True)
    head_name = serializers.SerializerMethodField()
    staff_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Department
        fields = ["id", "name", "description", "head", "head_name", "staff_count"]

    def get_head_name(self, obj) -> str:
        return obj.head.full_name if obj.head else ""

    def validate_name(self, value):
        value = value.strip()
        others = Department.objects.filter(school=self.context["request"].school, name__iexact=value)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError(_("This department already exists."))
        return value


class AnnouncementSerializer(serializers.ModelSerializer):
    roles = TenantPrimaryKeyRelatedField(queryset=Role.objects.all(), many=True, required=False)
    role_names = serializers.SerializerMethodField()
    author_name = serializers.SerializerMethodField()
    is_published = serializers.SerializerMethodField()

    class Meta:
        model = Announcement
        fields = [
            "id",
            "title",
            "body",
            "priority",
            "roles",
            "role_names",
            "publish_at",
            "expires_on",
            "pinned",
            "is_published",
            "author_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_role_names(self, obj) -> list[str]:
        return [role.name for role in obj.roles.all()]

    def get_author_name(self, obj) -> str:
        return obj.created_by.full_name if obj.created_by else ""

    def get_is_published(self, obj) -> bool:
        from django.utils import timezone

        return obj.publish_at <= timezone.now() and (
            obj.expires_on is None or obj.expires_on >= timezone.localdate()
        )

    def validate(self, attrs):
        publish_at = attrs.get("publish_at", getattr(self.instance, "publish_at", None))
        expires_on = attrs.get("expires_on", getattr(self.instance, "expires_on", None))
        if publish_at and expires_on and expires_on < publish_at.date():
            raise serializers.ValidationError(
                {"expires_on": [_("The end must be on or after the publication.")]}
            )
        return attrs


class CalendarEventSerializer(serializers.ModelSerializer):
    levels = TenantPrimaryKeyRelatedField(queryset=Level.objects.all(), many=True, required=False)
    level_names = serializers.SerializerMethodField()

    class Meta:
        model = CalendarEvent
        fields = [
            "id",
            "title",
            "kind",
            "start_date",
            "end_date",
            "start_time",
            "end_time",
            "location",
            "description",
            "levels",
            "level_names",
        ]
        extra_kwargs = {"end_date": {"required": False}}

    def get_level_names(self, obj) -> list[str]:
        return [level.name for level in obj.levels.all()]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None)) or start
        if start and end < start:
            raise serializers.ValidationError({"end_date": [_("The end must be on or after the start.")]})
        attrs["end_date"] = end
        start_time = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end_time = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start == end and start_time and end_time and end_time < start_time:
            raise serializers.ValidationError({"end_time": [_("The end must be after the start.")]})
        return attrs
