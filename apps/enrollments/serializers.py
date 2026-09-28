from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.academics.models import ClassGroup
from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.people.models import Gender, Student
from apps.people.serializers import GuardianInputSerializer, file_url

from .models import Enrollment


class EnrollmentStudentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    student_number = serializers.CharField()
    full_name = serializers.CharField()
    gender = serializers.CharField()
    date_of_birth = serializers.DateField(allow_null=True)
    photo_url = serializers.CharField(allow_null=True)


class EnrollmentSerializer(serializers.ModelSerializer):
    student = serializers.SerializerMethodField()
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    class_name = serializers.CharField(source="class_group.name", read_only=True)
    level_name = serializers.CharField(source="class_group.level.name", read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            "id",
            "student",
            "academic_year",
            "academic_year_name",
            "class_group",
            "class_name",
            "level_name",
            "enrollment_date",
            "kind",
            "status",
            "previous_school",
            "ended_on",
            "end_reason",
            "transfer_to",
            "notes",
            "created_at",
        ]
        read_only_fields = [
            "academic_year",
            "class_group",
            "status",
            "ended_on",
            "end_reason",
            "transfer_to",
            "created_at",
        ]

    @extend_schema_field(EnrollmentStudentSerializer)
    def get_student(self, obj) -> dict:
        s = obj.student
        return {
            "id": s.id,
            "student_number": s.student_number,
            "full_name": s.full_name,
            "gender": s.gender,
            "date_of_birth": s.date_of_birth.isoformat() if s.date_of_birth else None,
            "photo_url": file_url(s.photo, self.context.get("request")),
        }


class EnrollExistingSerializer(serializers.Serializer):
    student = TenantPrimaryKeyRelatedField(queryset=Student.objects.all())
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.select_related("academic_year"))
    enrollment_date = serializers.DateField(required=False)
    kind = serializers.ChoiceField(choices=Enrollment.Kind.choices, default=Enrollment.Kind.RE_ENROLMENT)
    previous_school = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class NewStudentSerializer(serializers.Serializer):
    student_number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100)
    gender = serializers.ChoiceField(choices=Gender.choices)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    place_of_birth = serializers.CharField(max_length=100, required=False, allow_blank=True)
    nationality = serializers.CharField(max_length=60, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class RegistrationSerializer(serializers.Serializer):
    """New enrolment in one request: a new student (or an existing one), guardians and the class."""

    student_id = TenantPrimaryKeyRelatedField(queryset=Student.objects.all(), required=False, allow_null=True)
    student = NewStudentSerializer(required=False)
    guardians = GuardianInputSerializer(many=True, required=False)
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.select_related("academic_year"))
    enrollment_date = serializers.DateField(required=False)
    kind = serializers.ChoiceField(choices=Enrollment.Kind.choices, default=Enrollment.Kind.NEW)
    previous_school = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if bool(attrs.get("student_id")) == bool(attrs.get("student")):
            raise serializers.ValidationError(
                _("Give either an existing student or the details of a new one.")
            )
        return attrs


class ChangeClassSerializer(serializers.Serializer):
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.select_related("academic_year"))
    date = serializers.DateField(required=False)
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


class WithdrawSerializer(serializers.Serializer):
    date = serializers.DateField(required=False)
    reason = serializers.CharField(max_length=255)
    transfer_to = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class PromoteSerializer(serializers.Serializer):
    from_class = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.select_related("academic_year"))
    to_class = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.select_related("academic_year"))
    enrollment_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    date = serializers.DateField(required=False)
