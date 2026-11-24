"""The platform owner's view: every school on Mon École, registering new schools and their Directors.

Only superusers (the owners of the platform) reach these endpoints. They are not school members; inside a
school they see everything but never manage its staff (see OWNER_CODES): the Director does. Here they
register, suspend and delete schools, and add, suspend, restore or remove Directors.
"""

import re

from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, extend_schema_field
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import Membership, Role
from apps.accounts.permissions_registry import DIRECTOR
from apps.accounts.services import (
    add_member,
    invitation_status,
    is_activated,
    remove_member,
    resend_invitation,
    update_member,
)
from apps.audit import services as audit
from apps.people.models import Student

from .models import School
from .services import create_school


class IsPlatformOwner(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class DirectorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    email = serializers.EmailField()
    phone = serializers.CharField()
    account_status = serializers.ChoiceField(choices=["active", "pending", "expired"])
    is_active = serializers.BooleanField(help_text="False: suspended by the platform owner.")


class PlatformSchoolSerializer(serializers.ModelSerializer):
    student_count = serializers.IntegerField(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    directors = serializers.SerializerMethodField()

    class Meta:
        model = School
        fields = [
            "id",
            "name",
            "code",
            "country",
            "currency",
            "timezone",
            "default_language",
            "status",
            "created_at",
            "student_count",
            "member_count",
            "directors",
        ]
        read_only_fields = fields

    @extend_schema_field(DirectorSerializer(many=True))
    def get_directors(self, obj) -> list[dict]:
        return [
            {
                "id": m.user.pk,
                "full_name": m.user.full_name,
                "email": m.user.email,
                "phone": m.user.phone,
                "account_status": invitation_status(m.user),
                "is_active": m.is_active,
            }
            for m in getattr(obj, "director_memberships", [])
        ]


class NewDirectorSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")


class RegisterSchoolSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    code = serializers.SlugField(max_length=30, help_text="Short unique identifier, e.g. 'horizon'.")
    country = serializers.CharField(max_length=2)
    currency = serializers.CharField(max_length=3)
    timezone = serializers.CharField(max_length=50)
    default_language = serializers.ChoiceField(choices=School.Language.choices)
    director = NewDirectorSerializer()

    def validate_code(self, value):
        value = value.lower()
        if School.objects.filter(code=value).exists():
            raise serializers.ValidationError(_("A school with this code already exists."))
        return value

    def validate_country(self, value):
        if not re.fullmatch(r"[A-Za-z]{2}", value):
            raise serializers.ValidationError(_("Use a 2-letter country code, e.g. GN or LR."))
        return value.upper()

    def validate_currency(self, value):
        if not re.fullmatch(r"[A-Za-z]{3}", value):
            raise serializers.ValidationError(_("Use a 3-letter currency code, e.g. GNF, LRD or USD."))
        return value.upper()


class SchoolStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[School.Status.ACTIVE, School.Status.SUSPENDED])


class DeleteSchoolSerializer(serializers.Serializer):
    confirm_code = serializers.CharField(help_text="The school's code, typed again.")


class DirectorActionSerializer(serializers.Serializer):
    user_id = serializers.IntegerField(help_text="The Director's id, as listed in `directors`.")
    action = serializers.ChoiceField(choices=["suspend", "restore", "remove"])


class PlatformSchoolViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Every school on the platform. Register one (with its Director), suspend or reactivate it, and resend
    the Director's invitation."""

    serializer_class = PlatformSchoolSerializer
    permission_classes = [IsAuthenticated, IsPlatformOwner]
    search_fields = ["name", "code"]
    filterset_fields = ["status", "country"]
    ordering_fields = ["name", "created_at"]
    ordering = ["name"]

    def get_queryset(self):
        directors = (
            Membership.objects.filter(roles__key=DIRECTOR)
            .select_related("user")
            .order_by("-is_active", "user__last_name")
            .distinct()
        )
        students = (
            Student.objects.filter(school=OuterRef("pk"), status=Student.Status.ACTIVE)
            .values("school")
            .annotate(n=Count("id"))
            .values("n")
        )
        return School.objects.annotate(
            student_count=Coalesce(Subquery(students, output_field=IntegerField()), 0),
            member_count=Count("memberships", filter=Q(memberships__is_active=True), distinct=True),
        ).prefetch_related(Prefetch("memberships", queryset=directors, to_attr="director_memberships"))

    @extend_schema(request=RegisterSchoolSerializer, responses={201: PlatformSchoolSerializer})
    def create(self, request):
        serializer = RegisterSchoolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        director = data.pop("director")
        with transaction.atomic():
            school = create_school(created_by=request.user, **data)
            add_member(
                school,
                email=director["email"],
                first_name=director["first_name"],
                last_name=director["last_name"],
                phone=director["phone"],
                # The Director's screens (and invitation email) start in the school's language.
                language=school.default_language,
                roles=[Role.objects.get(school=school, key=DIRECTOR)],
                request=request,
            )
        school = self.get_queryset().get(pk=school.pk)
        return Response(self.get_serializer(school).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=SchoolStatusSerializer, responses=PlatformSchoolSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, pk=None):
        """Suspend a school (nobody can sign in to it) or reactivate it."""
        school = self.get_object()
        serializer = SchoolStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]
        if school.status == School.Status.DELETING:
            raise ValidationError(_("This school is being deleted."))
        if new_status != school.status:
            old_status = school.status
            school.status = new_status
            school.save(update_fields=["status", "updated_at"])
            audit.record(
                "update",
                request=request,
                school=school,
                instance=school,
                module="schools",
                summary=f"School {'suspended' if new_status == School.Status.SUSPENDED else 'reactivated'}: "
                f"{school.name}",
                old={"status": old_status},
                new={"status": new_status},
            )
        return Response(self.get_serializer(self.get_queryset().get(pk=school.pk)).data)

    @extend_schema(request=None, responses={204: None})
    @action(detail=True, methods=["post"], url_path="resend-invitation")
    def resend_director_invitation(self, request, pk=None):
        """Send a new verification link and temporary password to Directors who have not activated yet."""
        school = self.get_object()
        pending = [m for m in school.director_memberships if not is_activated(m.user)]
        if not pending:
            raise ValidationError(_("Every Director of this school has already activated their account."))
        for membership in pending:
            resend_invitation(membership, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _membership(self, school, user_id):
        membership = Membership.objects.filter(school=school, user_id=user_id, roles__key=DIRECTOR).first()
        if membership is None:
            raise ValidationError(_("This person is not a Director of this school."))
        return membership

    def _fresh(self, school):
        return Response(self.get_serializer(self.get_queryset().get(pk=school.pk)).data)

    @extend_schema(request=NewDirectorSerializer, responses={201: PlatformSchoolSerializer})
    @action(detail=True, methods=["post"], url_path="directors")
    def add_director(self, request, pk=None):
        """Give the school another Director (an invitation is sent)."""
        school = self.get_object()
        serializer = NewDirectorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        add_member(
            school,
            **serializer.validated_data,
            language=school.default_language,
            roles=[Role.objects.get(school=school, key=DIRECTOR)],
            request=request,
        )
        response = self._fresh(school)
        response.status_code = status.HTTP_201_CREATED
        return response

    @extend_schema(request=DirectorActionSerializer, responses=PlatformSchoolSerializer)
    @action(detail=True, methods=["post"], url_path="directors/change")
    def change_director(self, request, pk=None):
        """Suspend a Director (they can no longer sign in to this school), restore them, or remove them."""
        school = self.get_object()
        serializer = DirectorActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = self._membership(school, serializer.validated_data["user_id"])
        match serializer.validated_data["action"]:
            case "suspend":
                update_member(membership, is_active=False, request=request)
            case "restore":
                update_member(membership, is_active=True, request=request)
            case "remove":
                remove_member(membership, request=request)
        return self._fresh(school)

    @extend_schema(request=DeleteSchoolSerializer, responses={202: PlatformSchoolSerializer})
    @action(detail=True, methods=["post"], url_path="delete")
    def delete_school(self, request, pk=None):
        """Delete a suspended school and all its data, in the background. Cannot be undone."""
        from .tasks import erase_school_task

        school = self.get_object()
        serializer = DeleteSchoolSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if school.status != School.Status.SUSPENDED:
            raise ValidationError(_("Suspend the school first: only a suspended school can be deleted."))
        if serializer.validated_data["confirm_code"].strip().lower() != school.code:
            raise ValidationError({"confirm_code": [_("Type the school's code exactly to confirm.")]})
        school.status = School.Status.DELETING
        school.save(update_fields=["status", "updated_at"])
        audit.record(
            "delete",
            request=request,
            school=None,
            module="platform",
            entity_type="school",
            entity_id=school.pk,
            summary=f"Deletion started: {school.name} ({school.code})",
        )
        transaction.on_commit(lambda: erase_school_task.delay(school.pk))
        response = self._fresh(school)
        response.status_code = status.HTTP_202_ACCEPTED
        return response
