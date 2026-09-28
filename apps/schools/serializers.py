from rest_framework import serializers

from .models import School, SchoolSettings
from .services import SCHOOL_FIELDS, SETTINGS_FIELDS

MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_LOGO_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}


class SchoolSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolSettings
        fields = SETTINGS_FIELDS

    def validate_idle_timeout_minutes(self, value):
        if not 5 <= value <= 240:
            raise serializers.ValidationError("Must be between 5 and 240 minutes.")
        return value


class SchoolSerializer(serializers.ModelSerializer):
    settings = SchoolSettingsSerializer(required=False)
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = School
        fields = ["id", "code", *SCHOOL_FIELDS, "logo", "logo_url", "status", "settings"]
        read_only_fields = ["id", "code", "status", "logo_url"]
        extra_kwargs = {"logo": {"write_only": True, "required": False}}

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get("request")
        url = obj.logo.url
        return request.build_absolute_uri(url) if request and url.startswith("/") else url

    def validate_logo(self, file):
        if file.size > MAX_LOGO_BYTES:
            raise serializers.ValidationError("Logo must be 2 MB or smaller.")
        if getattr(file, "content_type", None) not in ALLOWED_LOGO_TYPES:
            raise serializers.ValidationError("Logo must be a PNG, JPEG, WebP or SVG image.")
        return file

    def validate_currency(self, value):
        value = value.upper()
        if len(value) != 3 or not value.isalpha():
            raise serializers.ValidationError("Use a 3-letter currency code, e.g. GNF, LRD or USD.")
        return value
