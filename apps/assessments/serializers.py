from decimal import Decimal

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.academics.models import ClassGroup, Level, Term
from apps.core.serializers import TenantPrimaryKeyRelatedField

from .models import SCORE_DIGITS, SCORE_PLACES, Assessment, Gradebook, GradeCategory, GradingScale
from .scoping import can_edit, is_subject_teacher, visible_gradebooks


def mark_field() -> serializers.DecimalField:
    """A mark on the school's scale (up to 3 decimals), or null when nothing counts yet."""
    return serializers.DecimalField(max_digits=7, decimal_places=3, allow_null=True)


class MentionSerializer(serializers.Serializer):
    min = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0)
    label = serializers.CharField(max_length=40)  # type: ignore[assignment]  # "label" is also a Field attribute


class GradingScaleSerializer(serializers.ModelSerializer):
    level = TenantPrimaryKeyRelatedField(queryset=Level.objects.all(), allow_null=True, required=False)
    level_name = serializers.SerializerMethodField()
    mentions = serializers.ListField(
        child=serializers.DictField(), required=False, max_length=10, help_text="[{min, label}], any order."
    )

    class Meta:
        model = GradingScale
        fields = ["id", "level", "level_name", "max_mark", "pass_mark", "decimals", "rank_method", "mentions"]

    def validate_mentions(self, value):
        bands = MentionSerializer(data=value, many=True)
        bands.is_valid(raise_exception=True)
        cleaned = sorted(
            ({"min": float(b["min"]), "label": b["label"].strip()} for b in bands.validated_data),
            key=lambda b: -b["min"],
        )
        if len({b["min"] for b in cleaned}) != len(cleaned):
            raise serializers.ValidationError(_("Two honours bands start at the same mark."))
        return cleaned

    def get_level_name(self, obj) -> str:
        return obj.level.name if obj.level else ""

    def validate(self, attrs):
        level = attrs["level"] if "level" in attrs else getattr(self.instance, "level", None)
        max_mark = attrs.get("max_mark", getattr(self.instance, "max_mark", Decimal("20")))
        pass_mark = attrs.get("pass_mark", getattr(self.instance, "pass_mark", Decimal("10")))
        if any(b["min"] > max_mark for b in attrs.get("mentions", [])):
            raise serializers.ValidationError({"mentions": [_("An honours band starts above the maximum.")]})
        if pass_mark < 0 or pass_mark > max_mark:
            raise serializers.ValidationError(
                {"pass_mark": [_("The pass mark must be between 0 and the maximum.")]}
            )
        clash = GradingScale.objects.filter(school=self.context["request"].school, level=level)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            message = (
                _("This level already has its own scale.")
                if level
                else _("The school already has a default scale.")
            )
            raise serializers.ValidationError({"level": [message]})
        return attrs


class ScaleBriefSerializer(serializers.ModelSerializer):
    mentions = MentionSerializer(many=True, read_only=True)

    class Meta:
        model = GradingScale
        fields = ["max_mark", "pass_mark", "decimals", "rank_method", "mentions"]


class GradeCategorySerializer(serializers.ModelSerializer):
    gradebook = serializers.PrimaryKeyRelatedField(queryset=Gradebook.objects.all())

    class Meta:
        model = GradeCategory
        fields = ["id", "gradebook", "name", "weight", "method", "order"]

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None and getattr(request, "school", None) is not None:
            fields["gradebook"].queryset = visible_gradebooks(
                request, Gradebook.objects.filter(school=request.school)
            )
        if self.instance is not None and not isinstance(self.instance, list | tuple):
            fields["gradebook"].read_only = True
        return fields

    def validate(self, attrs):
        gradebook = attrs.get("gradebook") or self.instance.gradebook
        name = attrs.get("name", getattr(self.instance, "name", "")).strip()
        if not name:
            raise serializers.ValidationError({"name": [_("Give the category a name.")]})
        clash = GradeCategory.objects.filter(gradebook=gradebook, name__iexact=name)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                {"name": [_("This gradebook already has a category with this name.")]}
            )
        attrs["name"] = name
        return attrs


class AssessmentSerializer(serializers.ModelSerializer):
    gradebook = serializers.PrimaryKeyRelatedField(queryset=Gradebook.objects.all())
    category = serializers.PrimaryKeyRelatedField(queryset=GradeCategory.objects.all())
    max_score = serializers.DecimalField(
        max_digits=SCORE_DIGITS, decimal_places=SCORE_PLACES, min_value=Decimal("0.01"), required=False
    )

    class Meta:
        model = Assessment
        fields = ["id", "gradebook", "category", "name", "date", "max_score", "weight"]

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None and getattr(request, "school", None) is not None:
            visible = visible_gradebooks(request, Gradebook.objects.filter(school=request.school))
            fields["gradebook"].queryset = visible
            fields["category"].queryset = GradeCategory.objects.filter(gradebook__in=visible)
        if self.instance is not None and not isinstance(self.instance, list | tuple):
            fields["gradebook"].read_only = True
        return fields

    def validate(self, attrs):
        gradebook = attrs.get("gradebook") or self.instance.gradebook
        category = attrs.get("category") or self.instance.category
        if category.gradebook_id != gradebook.pk:
            raise serializers.ValidationError({"category": [_("Choose a category of this gradebook.")]})
        name = attrs.get("name", getattr(self.instance, "name", "")).strip()
        if not name:
            raise serializers.ValidationError({"name": [_("Give the assessment a name.")]})
        attrs["name"] = name
        return attrs


class GradebookSerializer(serializers.ModelSerializer):
    class_group = serializers.IntegerField(source="class_subject.class_group_id", read_only=True)
    class_name = serializers.CharField(source="class_subject.class_group.name", read_only=True)
    level = serializers.IntegerField(source="class_subject.class_group.level_id", read_only=True)
    subject = serializers.IntegerField(source="class_subject.subject_id", read_only=True)
    subject_name = serializers.CharField(source="class_subject.subject.name", read_only=True)
    subject_code = serializers.CharField(source="class_subject.subject.code", read_only=True)
    coefficient = serializers.DecimalField(
        source="class_subject.coefficient", max_digits=4, decimal_places=1, read_only=True
    )
    teacher_name = serializers.SerializerMethodField()
    term_name = serializers.CharField(source="term.name", read_only=True)
    student_count = serializers.IntegerField(read_only=True)
    assessment_count = serializers.IntegerField(read_only=True)
    mark_count = serializers.IntegerField(read_only=True)
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = Gradebook
        fields = [
            "id",
            "class_subject",
            "class_group",
            "class_name",
            "level",
            "subject",
            "subject_name",
            "subject_code",
            "coefficient",
            "teacher_name",
            "term",
            "term_name",
            "status",
            "missing_policy",
            "student_count",
            "assessment_count",
            "mark_count",
            "is_mine",
        ]
        read_only_fields = ["class_subject", "term", "status", "missing_policy"]

    def get_teacher_name(self, obj) -> str:
        teacher = obj.class_subject.teacher
        return teacher.full_name if teacher else ""

    def get_is_mine(self, obj) -> bool:
        return is_subject_teacher(self.context["request"], obj)


class GradebookPermissionsSerializer(serializers.Serializer):
    edit = serializers.BooleanField()
    submit = serializers.BooleanField()
    send_back = serializers.BooleanField()
    publish = serializers.BooleanField()
    reopen = serializers.BooleanField()


class GradebookDetailSerializer(GradebookSerializer):
    categories = GradeCategorySerializer(many=True, read_only=True)
    assessments = AssessmentSerializer(many=True, read_only=True)
    scale = serializers.SerializerMethodField()
    can = serializers.SerializerMethodField()
    submitted_by_name = serializers.SerializerMethodField()
    published_by_name = serializers.SerializerMethodField()

    class Meta(GradebookSerializer.Meta):
        fields = [
            *GradebookSerializer.Meta.fields,
            "status_note",
            "submitted_at",
            "submitted_by_name",
            "published_at",
            "published_by_name",
            "scale",
            "categories",
            "assessments",
            "can",
        ]
        read_only_fields = [
            *GradebookSerializer.Meta.read_only_fields,
            "status_note",
            "submitted_at",
            "published_at",
        ]

    @extend_schema_field(ScaleBriefSerializer)
    def get_scale(self, obj):
        from .services import scale_for

        return ScaleBriefSerializer(scale_for(obj.school, obj.class_subject.class_group.level)).data

    @extend_schema_field(GradebookPermissionsSerializer)
    def get_can(self, obj):
        request = self.context["request"]
        codes = request.permission_codes
        status = obj.status
        editable = can_edit(request, obj)
        # Nothing to hand in or publish before the first assessment.
        has_assessments = len(obj.assessments.all()) > 0
        return {
            "edit": editable and status == Gradebook.Status.OPEN,
            "submit": editable
            and has_assessments
            and status == Gradebook.Status.OPEN
            and "grades.submit" in codes,
            "send_back": status == Gradebook.Status.SUBMITTED and "grades.review" in codes,
            "publish": has_assessments and status != Gradebook.Status.PUBLISHED and "grades.publish" in codes,
            "reopen": status == Gradebook.Status.PUBLISHED and "grades.reopen" in codes,
        }

    def _name(self, user) -> str:
        return (user.get_full_name() or user.email) if user else ""

    def get_submitted_by_name(self, obj) -> str:
        return self._name(obj.submitted_by)

    def get_published_by_name(self, obj) -> str:
        return self._name(obj.published_by)


class GradebookUpdateSerializer(serializers.Serializer):
    missing_policy = serializers.ChoiceField(choices=Gradebook.MissingPolicy.choices)


class GradebookReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class CopySetupSerializer(serializers.Serializer):
    source = serializers.PrimaryKeyRelatedField(queryset=Gradebook.objects.all())  # type: ignore[assignment]  # "source" is also a Field attribute
    with_assessments = serializers.BooleanField(default=False)

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get("request")
        if request is not None and getattr(request, "school", None) is not None:
            fields["source"].queryset = visible_gradebooks(
                request, Gradebook.objects.filter(school=request.school)
            )
        return fields


class GradeEntrySerializer(serializers.Serializer):
    assessment = serializers.IntegerField()
    enrollment = serializers.IntegerField()
    score = serializers.DecimalField(
        max_digits=SCORE_DIGITS, decimal_places=SCORE_PLACES, min_value=Decimal("0"), allow_null=True
    )
    excused = serializers.BooleanField(default=False)
    comment = serializers.CharField(max_length=255, allow_blank=True, required=False, default="")


class SaveGradesSerializer(serializers.Serializer):
    grades = GradeEntrySerializer(many=True, allow_empty=False, max_length=5000)  # type: ignore[call-arg]  # ListSerializer option


class SaveGradesResultSerializer(serializers.Serializer):
    changed = serializers.IntegerField()


class StatsSerializer(serializers.Serializer):
    average = mark_field()
    lowest = mark_field()
    highest = mark_field()
    passed = serializers.IntegerField()
    counted = serializers.IntegerField()


class SheetMarkSerializer(serializers.Serializer):
    assessment = serializers.IntegerField()
    score = serializers.DecimalField(max_digits=SCORE_DIGITS, decimal_places=SCORE_PLACES, allow_null=True)
    excused = serializers.BooleanField()
    comment = serializers.CharField()


class SheetCategoryMarkSerializer(serializers.Serializer):
    category = serializers.IntegerField()
    mark = mark_field()


class SheetStudentSerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    student = serializers.IntegerField()
    student_name = serializers.CharField()
    student_number = serializers.CharField()
    is_active = serializers.BooleanField()
    marks = SheetMarkSerializer(many=True)
    categories = SheetCategoryMarkSerializer(many=True)
    mark = mark_field()
    rank = serializers.IntegerField(allow_null=True)
    passed = serializers.BooleanField(allow_null=True)
    missing = serializers.IntegerField()


class SheetAssessmentSerializer(serializers.Serializer):
    assessment = serializers.IntegerField()
    marked = serializers.IntegerField()
    average = mark_field()


class GradebookSheetSerializer(serializers.Serializer):
    scale = ScaleBriefSerializer()
    students = SheetStudentSerializer(many=True)
    assessments = SheetAssessmentSerializer(many=True)
    stats = StatsSerializer()


class ClassResultsParamsSerializer(serializers.Serializer):
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.all())
    term = TenantPrimaryKeyRelatedField(queryset=Term.objects.all())

    def validate(self, attrs):
        if attrs["term"].academic_year_id != attrs["class_group"].academic_year_id:
            raise serializers.ValidationError({"term": [_("Choose a term of the class's academic year.")]})
        return attrs


class ResultSubjectSerializer(serializers.Serializer):
    class_subject = serializers.IntegerField()
    subject_name = serializers.CharField()
    subject_code = serializers.CharField()
    coefficient = serializers.DecimalField(max_digits=4, decimal_places=1)
    teacher_name = serializers.CharField()
    gradebook = serializers.IntegerField(allow_null=True)
    status = serializers.ChoiceField(choices=Gradebook.Status.choices)
    stats = StatsSerializer()


class ResultMarkSerializer(serializers.Serializer):
    class_subject = serializers.IntegerField()
    mark = mark_field()
    rank = serializers.IntegerField(allow_null=True)


class ResultStudentSerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    student = serializers.IntegerField()
    student_name = serializers.CharField()
    student_number = serializers.CharField()
    marks = ResultMarkSerializer(many=True)
    average = mark_field()
    rank = serializers.IntegerField(allow_null=True)
    passed = serializers.BooleanField(allow_null=True)


class ClassResultsSerializer(serializers.Serializer):
    scale = ScaleBriefSerializer()
    subjects = ResultSubjectSerializer(many=True)
    students = ResultStudentSerializer(many=True)
    stats = StatsSerializer()


class ReportCardParamsSerializer(serializers.Serializer):
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.all())
    term = TenantPrimaryKeyRelatedField(
        queryset=Term.objects.all(), required=False, allow_null=True, help_text="Leave out for the year."
    )
    enrollment = serializers.IntegerField(required=False, help_text="Only this student's card.")

    def validate(self, attrs):
        term = attrs.get("term")
        if term is not None and term.academic_year_id != attrs["class_group"].academic_year_id:
            raise serializers.ValidationError({"term": [_("Choose a term of the class's academic year.")]})
        return attrs


class CommentEntrySerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    comment = serializers.CharField(max_length=600, allow_blank=True)


class ReportCommentRowSerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    student = serializers.IntegerField()
    student_name = serializers.CharField()
    comment = serializers.CharField()


class SaveReportCommentsSerializer(ReportCardParamsSerializer):
    comments = CommentEntrySerializer(many=True, max_length=500)  # type: ignore[call-arg]  # ListSerializer option


class StudentTermResultSerializer(serializers.Serializer):
    term = serializers.IntegerField(allow_null=True, help_text="Null = the whole year.")
    term_name = serializers.CharField()
    average = mark_field()
    rank = serializers.IntegerField(allow_null=True)
    ranked = serializers.IntegerField()
    mention = serializers.CharField()
    passed = serializers.BooleanField(allow_null=True)
    subjects = serializers.IntegerField(help_text="Published subjects counted.")


class StudentResultsSerializer(serializers.Serializer):
    enrollment = serializers.IntegerField(allow_null=True)
    class_group = serializers.IntegerField(allow_null=True)
    class_name = serializers.CharField()
    scale = ScaleBriefSerializer(allow_null=True)
    terms = StudentTermResultSerializer(many=True)
