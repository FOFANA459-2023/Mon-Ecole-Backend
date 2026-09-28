from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.schools.models import School

from .models import Membership, Role
from .permissions_registry import ALL_CODES

User = get_user_model()


class LoginSerializer(serializers.Serializer):
    login = serializers.CharField(max_length=254)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class RoleBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "key", "name"]


def _logo_url(school, request) -> str | None:
    if not school.logo:
        return None
    url = school.logo.url
    return request.build_absolute_uri(url) if request and url.startswith("/") else url


def school_summary(school, request) -> dict:
    return {
        "id": school.id,
        "name": school.name,
        "code": school.code,
        "logo_url": _logo_url(school, request),
        "currency": school.currency,
        "timezone": school.timezone,
        "default_language": school.default_language,
        "idle_timeout_minutes": school.settings.idle_timeout_minutes if hasattr(school, "settings") else 30,
    }


def me_payload(user, request) -> dict:
    memberships = (
        Membership.objects.filter(user=user, is_active=True, school__status=School.Status.ACTIVE)
        .select_related("school", "school__settings")
        .prefetch_related("roles")
    )
    items = [
        {
            "school": school_summary(m.school, request),
            "roles": RoleBriefSerializer(m.roles.all(), many=True).data,
            "permissions": sorted(m.permission_codes()),
        }
        for m in memberships
    ]
    if user.is_superuser:
        member_ids = {m.school_id for m in memberships}
        others = School.objects.filter(status=School.Status.ACTIVE).exclude(id__in=member_ids)
        items += [
            {
                "school": school_summary(s, request),
                "roles": [{"id": None, "key": "platform_admin", "name": "Platform administrator"}],
                "permissions": sorted(ALL_CODES),
            }
            for s in others.select_related("settings")
        ]
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.full_name,
        "phone": user.phone,
        "language": user.language,
        "must_change_password": user.must_change_password,
        "is_platform_admin": user.is_superuser,
        "memberships": items,
    }


class MeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone", "language"]


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(trim_whitespace=False)
    new_password = serializers.CharField(trim_whitespace=False)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError(_("Your current password is incorrect."))
        return value

    def validate_new_password(self, value):
        validate_password(value, self.context["request"].user)
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(trim_whitespace=False)


class PermissionGroupSerializer(serializers.Serializer):
    module = serializers.CharField()
    label = serializers.CharField()
    permissions = serializers.ListField(child=serializers.DictField())


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.ListField(child=serializers.CharField(), required=False)
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ["id", "key", "name", "description", "is_system", "permissions", "member_count"]
        read_only_fields = ["id", "key", "is_system"]

    def get_member_count(self, obj) -> int:
        annotated = getattr(obj, "member_count", None)
        return annotated if annotated is not None else obj.memberships.filter(is_active=True).count()

    def validate_permissions(self, value):
        if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
            raise serializers.ValidationError(_("Must be a list of permission codes."))
        unknown = set(value) - ALL_CODES
        if unknown:
            raise serializers.ValidationError(_("Unknown permissions: %s") % ", ".join(sorted(unknown)))
        return value

    def validate_name(self, value):
        school = self.context["request"].school
        qs = Role.objects.filter(school=school, name__iexact=value.strip())
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(_("A role with this name already exists."))
        return value.strip()


class MemberUserSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    has_password = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "language",
            "last_login",
            "has_password",
        ]

    def get_has_password(self, obj) -> bool:
        return obj.has_usable_password()


class MemberSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer(read_only=True)
    roles = RoleBriefSerializer(many=True, read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "user", "roles", "is_active", "created_at"]


class _RoleIdsMixin:
    def validate_role_ids(self, value):
        school = self.context["request"].school
        roles = list(Role.objects.filter(school=school, id__in=value))
        if len(roles) != len(set(value)):
            raise serializers.ValidationError(_("One or more roles do not exist."))
        return roles


class MemberCreateSerializer(_RoleIdsMixin, serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    language = serializers.ChoiceField(choices=User.Language.choices, required=False)
    role_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)

    def validate(self, attrs):
        password = attrs.get("password")
        if password:
            validate_password(password, User(email=attrs["email"], first_name=attrs["first_name"]))
        return attrs


class MemberUpdateSerializer(_RoleIdsMixin, serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    language = serializers.ChoiceField(choices=User.Language.choices, required=False)
    role_ids = serializers.ListField(child=serializers.IntegerField(), required=False, allow_empty=False)
    is_active = serializers.BooleanField(required=False)
