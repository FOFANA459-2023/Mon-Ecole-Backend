from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.core.serializers import TenantPrimaryKeyRelatedField
from apps.people.models import StaffMember

from .models import AcademicYear, ClassGroup, ClassSubject, Level, Subject, Term


class TermSerializer(serializers.ModelSerializer):
    academic_year = TenantPrimaryKeyRelatedField(queryset=AcademicYear.objects.all())

    class Meta:
        model = Term
        fields = ["id", "academic_year", "name", "order", "start_date", "end_date"]

    def validate(self, attrs):
        year = attrs.get("academic_year") or self.instance.academic_year
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end_date": _("The end date must be after the start date.")})
        if start and start < year.start_date or end and end > year.end_date:
            raise serializers.ValidationError(_("A term must fall within its academic year."))
        order = attrs.get("order", getattr(self.instance, "order", None))
        clash = Term.objects.filter(academic_year=year, order=order)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"order": _("Another term already has this position.")})
        return attrs


class AcademicYearSerializer(serializers.ModelSerializer):
    terms = TermSerializer(many=True, read_only=True)
    term_count = serializers.IntegerField(write_only=True, required=False, min_value=0, max_value=6)

    class Meta:
        model = AcademicYear
        fields = ["id", "name", "start_date", "end_date", "is_current", "status", "terms", "term_count"]
        read_only_fields = ["is_current"]

    def validate_name(self, value):
        school = self.context["request"].school
        clash = AcademicYear.objects.filter(school=school, name__iexact=value.strip())
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("This academic year already exists."))
        return value.strip()

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end <= start:
            raise serializers.ValidationError({"end_date": _("The end date must be after the start date.")})
        return attrs


class LevelSerializer(serializers.ModelSerializer):
    class_count = serializers.SerializerMethodField()

    class Meta:
        model = Level
        fields = ["id", "name", "order", "cycle", "is_active", "class_count"]

    def get_class_count(self, obj) -> int:
        value = getattr(obj, "class_count", None)
        return value if value is not None else obj.classes.count()

    def validate_name(self, value):
        school = self.context["request"].school
        clash = Level.objects.filter(school=school, name__iexact=value.strip())
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("This level already exists."))
        return value.strip()


class ClassGroupSerializer(serializers.ModelSerializer):
    academic_year = TenantPrimaryKeyRelatedField(queryset=AcademicYear.objects.all())
    level = TenantPrimaryKeyRelatedField(queryset=Level.objects.all())
    class_teacher = TenantPrimaryKeyRelatedField(
        queryset=StaffMember.objects.all(), allow_null=True, required=False
    )
    academic_year_name = serializers.CharField(source="academic_year.name", read_only=True)
    level_name = serializers.CharField(source="level.name", read_only=True)
    class_teacher_name = serializers.SerializerMethodField()
    enrolled_count = serializers.SerializerMethodField()
    subject_count = serializers.SerializerMethodField()

    class Meta:
        model = ClassGroup
        fields = [
            "id",
            "academic_year",
            "academic_year_name",
            "level",
            "level_name",
            "name",
            "room",
            "class_teacher",
            "class_teacher_name",
            "capacity",
            "status",
            "enrolled_count",
            "subject_count",
        ]

    def get_class_teacher_name(self, obj) -> str:
        return obj.class_teacher.full_name if obj.class_teacher else ""

    def get_enrolled_count(self, obj) -> int:
        value = getattr(obj, "enrolled_count", None)
        return value if value is not None else obj.enrollments.filter(status="active").count()

    def get_subject_count(self, obj) -> int:
        value = getattr(obj, "subject_count", None)
        return value if value is not None else obj.class_subjects.count()

    def validate(self, attrs):
        year = attrs.get("academic_year") or getattr(self.instance, "academic_year", None)
        name = (attrs.get("name") or getattr(self.instance, "name", "")).strip()
        clash = ClassGroup.objects.filter(academic_year=year, name__iexact=name)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"name": _("A class with this name already exists this year.")})
        capacity = attrs.get("capacity")
        if self.instance and capacity is not None:
            enrolled = self.instance.enrollments.filter(status="active").count()
            if capacity < enrolled:
                raise serializers.ValidationError(
                    {"capacity": _("%(n)d students are already enrolled in this class.") % {"n": enrolled}}
                )
        return attrs


class SubjectSerializer(serializers.ModelSerializer):
    level = TenantPrimaryKeyRelatedField(queryset=Level.objects.all(), allow_null=True, required=False)
    level_name = serializers.SerializerMethodField()

    class Meta:
        model = Subject
        fields = ["id", "name", "code", "level", "level_name", "default_coefficient", "is_active"]

    def get_level_name(self, obj) -> str:
        return obj.level.name if obj.level else ""

    def validate_code(self, value):
        code = value.strip().upper()
        school = self.context["request"].school
        clash = Subject.objects.filter(school=school, code__iexact=code)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(_("Another subject already uses this code."))
        return code


class ClassSubjectSerializer(serializers.ModelSerializer):
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.all())
    subject = TenantPrimaryKeyRelatedField(queryset=Subject.objects.all())
    teacher = TenantPrimaryKeyRelatedField(
        queryset=StaffMember.objects.all(), allow_null=True, required=False
    )
    coefficient = serializers.DecimalField(
        max_digits=4, decimal_places=1, required=False, min_value=Decimal("0.1")
    )
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    subject_code = serializers.CharField(source="subject.code", read_only=True)
    class_name = serializers.CharField(source="class_group.name", read_only=True)
    teacher_name = serializers.SerializerMethodField()

    class Meta:
        model = ClassSubject
        fields = [
            "id",
            "class_group",
            "class_name",
            "subject",
            "subject_name",
            "subject_code",
            "teacher",
            "teacher_name",
            "coefficient",
            "weekly_hours",
        ]

    def get_teacher_name(self, obj) -> str:
        return obj.teacher.full_name if obj.teacher else ""

    def validate(self, attrs):
        class_group = attrs.get("class_group") or self.instance.class_group
        subject = attrs.get("subject") or self.instance.subject
        clash = ClassSubject.objects.filter(class_group=class_group, subject=subject)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError({"subject": _("This subject is already taught in this class.")})
        if self.instance is None and "coefficient" not in attrs:
            attrs["coefficient"] = subject.default_coefficient
        return attrs
