from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.academics.models import ClassGroup, Subject
from apps.core.serializers import TenantPrimaryKeyRelatedField

from .models import Guardian, StaffMember, Student, StudentGuardian


def file_url(file, request) -> str | None:
    if not file:
        return None
    url = file.url
    return request.build_absolute_uri(url) if request and url.startswith("/") else url


class EnrollmentBriefSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    kind = serializers.CharField()
    enrollment_date = serializers.DateField()
    ended_on = serializers.DateField(allow_null=True)
    academic_year = serializers.IntegerField(source="academic_year_id")
    academic_year_name = serializers.CharField(source="academic_year.name")
    class_group = serializers.IntegerField(source="class_group_id")
    class_name = serializers.CharField(source="class_group.name")
    level_name = serializers.CharField(source="class_group.level.name")


class GuardianStudentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    relationship = serializers.CharField()


class PrimaryGuardianSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    phone = serializers.CharField()
    relationship = serializers.CharField()


class AssignmentSubjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    class_id = serializers.IntegerField()
    class_name = serializers.CharField()
    subject_id = serializers.IntegerField()
    subject_name = serializers.CharField()


class HomeroomSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class AssignmentsSerializer(serializers.Serializer):
    subjects = AssignmentSubjectSerializer(many=True)
    homeroom_classes = HomeroomSerializer(many=True)


class GuardianSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    students = serializers.SerializerMethodField()

    class Meta:
        model = Guardian
        fields = [
            "id",
            "first_name",
            "last_name",
            "full_name",
            "phone",
            "alt_phone",
            "email",
            "address",
            "occupation",
            "students",
        ]

    @extend_schema_field(GuardianStudentSerializer(many=True))
    def get_students(self, obj) -> list[dict]:
        return [
            {"id": link.student_id, "full_name": link.student.full_name, "relationship": link.relationship}
            for link in obj.student_links.all()
        ]


class GuardianLinkSerializer(serializers.ModelSerializer):
    guardian = GuardianSerializer(read_only=True)

    class Meta:
        model = StudentGuardian
        fields = ["id", "guardian", "relationship", "is_primary", "is_financial_contact"]


class GuardianInputSerializer(serializers.Serializer):
    """Link an existing guardian (guardian_id) or create a new one from the other fields."""

    guardian_id = TenantPrimaryKeyRelatedField(
        queryset=Guardian.objects.all(), required=False, allow_null=True
    )
    first_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    alt_phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    occupation = serializers.CharField(max_length=100, required=False, allow_blank=True)
    relationship = serializers.ChoiceField(choices=StudentGuardian.Relationship.choices)
    is_primary = serializers.BooleanField(default=False)
    is_financial_contact = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if not attrs.get("guardian_id"):
            missing = {
                f: [_("This field is required.")] for f in ("first_name", "last_name") if not attrs.get(f)
            }
            if missing:
                raise serializers.ValidationError(missing)
            if not attrs.get("phone") and not attrs.get("email"):
                raise serializers.ValidationError({"phone": [_("Give a phone number or an email address.")]})
        return attrs

    @staticmethod
    def split(data: dict) -> tuple[Guardian | None, dict, dict]:
        """Return (existing guardian, new-guardian fields, link fields)."""
        link_fields = {
            k: data[k] for k in ("relationship", "is_primary", "is_financial_contact") if k in data
        }
        guardian_fields = {
            k: data[k]
            for k in ("first_name", "last_name", "phone", "alt_phone", "email", "address", "occupation")
            if k in data
        }
        return data.get("guardian_id"), guardian_fields, link_fields


class GuardianLinkUpdateSerializer(serializers.Serializer):
    relationship = serializers.ChoiceField(choices=StudentGuardian.Relationship.choices, required=False)
    is_primary = serializers.BooleanField(required=False)
    is_financial_contact = serializers.BooleanField(required=False)
    first_name = serializers.CharField(max_length=100, required=False)
    last_name = serializers.CharField(max_length=100, required=False)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    alt_phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    address = serializers.CharField(required=False, allow_blank=True)
    occupation = serializers.CharField(max_length=100, required=False, allow_blank=True)


class StudentListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    photo_url = serializers.SerializerMethodField()
    current_enrollment = serializers.SerializerMethodField()
    primary_guardian = serializers.SerializerMethodField()

    class Meta:
        model = Student
        fields = [
            "id",
            "student_number",
            "first_name",
            "last_name",
            "full_name",
            "gender",
            "date_of_birth",
            "phone",
            "status",
            "photo_url",
            "current_enrollment",
            "primary_guardian",
        ]

    def get_photo_url(self, obj) -> str | None:
        return file_url(obj.photo, self.context.get("request"))

    @extend_schema_field(EnrollmentBriefSerializer(allow_null=True))
    def get_current_enrollment(self, obj) -> dict | None:
        active = getattr(obj, "active_enrollments", None)
        if active is None:
            active = list(
                obj.enrollments.filter(status="active").select_related("academic_year", "class_group__level")
            )
        active = sorted(active, key=lambda e: e.academic_year.start_date, reverse=True)
        return EnrollmentBriefSerializer(active[0]).data if active else None

    @extend_schema_field(PrimaryGuardianSerializer(allow_null=True))
    def get_primary_guardian(self, obj) -> dict | None:
        links = list(obj.guardian_links.all())
        link = next((lnk for lnk in links if lnk.is_primary), links[0] if links else None)
        if link is None:
            return None
        return {
            "id": link.guardian_id,
            "full_name": link.guardian.full_name,
            "phone": link.guardian.phone,
            "relationship": link.relationship,
        }


class StudentSerializer(StudentListSerializer):
    guardians = GuardianLinkSerializer(source="guardian_links", many=True, read_only=True)
    enrollments = serializers.SerializerMethodField()
    student_number = serializers.CharField(max_length=30, required=False, allow_blank=True)

    class Meta(StudentListSerializer.Meta):
        fields = [
            *StudentListSerializer.Meta.fields,
            "place_of_birth",
            "nationality",
            "email",
            "address",
            "notes",
            "archived_at",
            "created_at",
            "guardians",
            "enrollments",
        ]
        read_only_fields = ["status", "archived_at", "created_at"]

    @extend_schema_field(EnrollmentBriefSerializer(many=True))
    def get_enrollments(self, obj) -> list[dict]:
        history = obj.enrollments.select_related("academic_year", "class_group__level").order_by(
            "-academic_year__start_date", "-enrollment_date", "-id"
        )
        return list(EnrollmentBriefSerializer(history, many=True).data)

    def validate_student_number(self, value):
        if self.instance is not None and value and value != self.instance.student_number:
            raise serializers.ValidationError(_("The student number cannot be changed."))
        return value


class StaffSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    photo_url = serializers.SerializerMethodField()
    has_access = serializers.SerializerMethodField()
    employee_number = serializers.CharField(max_length=30, required=False, allow_blank=True)
    assignments = serializers.SerializerMethodField()

    class Meta:
        model = StaffMember
        fields = [
            "id",
            "employee_number",
            "first_name",
            "last_name",
            "full_name",
            "gender",
            "date_of_birth",
            "phone",
            "email",
            "address",
            "staff_type",
            "position",
            "qualification",
            "specialization",
            "employment_date",
            "status",
            "photo_url",
            "has_access",
            "assignments",
            "archived_at",
            "created_at",
        ]
        read_only_fields = ["status", "archived_at", "created_at"]

    def get_photo_url(self, obj) -> str | None:
        return file_url(obj.photo, self.context.get("request"))

    def get_has_access(self, obj) -> bool:
        return obj.user_id is not None

    @extend_schema_field(AssignmentsSerializer)
    def get_assignments(self, obj) -> dict:
        subjects = getattr(obj, "current_assignments", None)
        if subjects is None:
            subjects = list(obj.class_subjects.select_related("class_group", "subject"))
        homeroom = getattr(obj, "current_homerooms", None)
        if homeroom is None:
            homeroom = list(obj.homeroom_classes.all())
        return {
            "subjects": [
                {
                    "id": cs.id,
                    "class_id": cs.class_group_id,
                    "class_name": cs.class_group.name,
                    "subject_id": cs.subject_id,
                    "subject_name": cs.subject.name,
                }
                for cs in subjects
            ],
            "homeroom_classes": [{"id": c.id, "name": c.name} for c in homeroom],
        }

    def validate_employee_number(self, value):
        if self.instance is not None and value and value != self.instance.employee_number:
            raise serializers.ValidationError(_("The staff number cannot be changed."))
        return value


class TeachingItemSerializer(serializers.Serializer):
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.all())
    subject = TenantPrimaryKeyRelatedField(queryset=Subject.objects.all())


class TeachingSerializer(serializers.Serializer):
    """What a teacher does this year: the classes they lead and the subjects they teach, per class."""

    homeroom_class_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    subjects = TeachingItemSerializer(many=True, required=False, default=list)

    def validate_homeroom_class_ids(self, value):
        classes = list(ClassGroup.objects.filter(school=self.context["request"].school, pk__in=value))
        if len(classes) != len(set(value)):
            raise serializers.ValidationError(_("One or more classes do not exist."))
        return classes

    def validate_subjects(self, value):
        pairs = [(item["class_group"].pk, item["subject"].pk) for item in value]
        if len(pairs) != len(set(pairs)):
            raise serializers.ValidationError(_("The same subject is listed twice for a class."))
        return value


class StaffCreateSerializer(StaffSerializer):
    """Adding a staff member always gives them a login: an email and a role are required."""

    role_id = serializers.IntegerField(write_only=True)
    teaching = TeachingSerializer(write_only=True, required=False)

    class Meta(StaffSerializer.Meta):
        fields = [*StaffSerializer.Meta.fields, "role_id", "teaching"]
        extra_kwargs = {"email": {"required": True, "allow_blank": False}}

    def validate_role_id(self, value):
        from apps.accounts.models import Role

        role = Role.objects.filter(school=self.context["request"].school, pk=value).first()
        if role is None:
            raise serializers.ValidationError(_("This role does not exist."))
        return role


class GrantAccessSerializer(serializers.Serializer):
    role_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)

    def validate_role_ids(self, value):
        from apps.accounts.models import Role

        roles = list(Role.objects.filter(school=self.context["request"].school, id__in=value))
        if len(roles) != len(set(value)):
            raise serializers.ValidationError(_("One or more roles do not exist."))
        return roles


class PhotoSerializer(serializers.Serializer):
    file = serializers.FileField()
