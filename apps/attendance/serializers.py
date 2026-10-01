from datetime import date

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from apps.academics.models import AcademicYear, ClassGroup
from apps.core.serializers import TenantPrimaryKeyRelatedField

from .models import StaffAttendance, Status


class DayParamsSerializer(serializers.Serializer):
    date = serializers.DateField(required=False, help_text="Defaults to today (school time).")


class RegisterParamsSerializer(serializers.Serializer):
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.all())
    date = serializers.DateField(required=False, help_text="Defaults to today (school time).")


class RecordEntrySerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    status = serializers.ChoiceField(choices=Status.choices)
    minutes_late = serializers.IntegerField(min_value=1, max_value=600, required=False, allow_null=True)
    note = serializers.CharField(max_length=255, allow_blank=True, required=False, default="")


class SaveRegisterSerializer(serializers.Serializer):
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.all())
    date = serializers.DateField()
    records = RecordEntrySerializer(many=True, max_length=500)  # type: ignore[call-arg]  # ListSerializer option


class DayClassSerializer(serializers.Serializer):
    class_group = serializers.IntegerField()
    class_name = serializers.CharField()
    level_name = serializers.CharField()
    student_count = serializers.IntegerField()
    register = serializers.IntegerField(allow_null=True)
    taken_by_name = serializers.CharField()
    taken_at = serializers.DateTimeField(allow_null=True)
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    excused = serializers.IntegerField()
    can_take = serializers.BooleanField()


class RegisterStudentSerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    student = serializers.IntegerField()
    student_name = serializers.CharField()
    student_number = serializers.CharField()
    status = serializers.ChoiceField(choices=Status.choices)
    minutes_late = serializers.IntegerField(allow_null=True)
    note = serializers.CharField()


class RegisterSheetSerializer(serializers.Serializer):
    class_group = serializers.IntegerField()
    class_name = serializers.CharField()
    date = serializers.DateField()
    register = serializers.IntegerField(allow_null=True)
    taken_by_name = serializers.CharField()
    taken_at = serializers.DateTimeField(allow_null=True)
    updated_by_name = serializers.CharField()
    updated_at = serializers.DateTimeField(allow_null=True)
    can_edit = serializers.BooleanField()
    can_take_today = serializers.BooleanField()
    students = RegisterStudentSerializer(many=True)


class MonthParamsSerializer(serializers.Serializer):
    class_group = TenantPrimaryKeyRelatedField(queryset=ClassGroup.objects.all())
    month = serializers.RegexField(r"^\d{4}-(0[1-9]|1[0-2])$", help_text="YYYY-MM")


class DayStatusSerializer(serializers.Serializer):
    date = serializers.DateField()
    status = serializers.ChoiceField(choices=Status.choices)


class TotalsSerializer(serializers.Serializer):
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    excused = serializers.IntegerField()


class MonthStudentSerializer(TotalsSerializer):
    enrollment = serializers.IntegerField()
    student = serializers.IntegerField()
    student_name = serializers.CharField()
    days = DayStatusSerializer(many=True)


class ClassMonthSerializer(serializers.Serializer):
    class_group = serializers.IntegerField()
    class_name = serializers.CharField()
    month = serializers.CharField()
    dates = serializers.ListField(child=serializers.DateField())
    students = MonthStudentSerializer(many=True)
    totals = TotalsSerializer()


class AbsenceParamsSerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False, help_text="Defaults to 30 days before date_to.")
    date_to = serializers.DateField(required=False, help_text="Defaults to today.")
    class_group = TenantPrimaryKeyRelatedField(
        queryset=ClassGroup.objects.all(), required=False, allow_null=True
    )
    min_absences = serializers.IntegerField(min_value=1, max_value=365, required=False, default=1)

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_from": [_("The start must be on or before the end.")]})
        return attrs


class AbsenceRowSerializer(serializers.Serializer):
    enrollment = serializers.IntegerField()
    student = serializers.IntegerField()
    student_name = serializers.CharField()
    student_number = serializers.CharField()
    class_name = serializers.CharField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    excused = serializers.IntegerField()


class StudentParamsSerializer(serializers.Serializer):
    academic_year = TenantPrimaryKeyRelatedField(
        queryset=AcademicYear.objects.all(), required=False, help_text="Defaults to the current year."
    )


class StudentEventSerializer(serializers.Serializer):
    date = serializers.DateField()
    class_name = serializers.CharField()
    status = serializers.ChoiceField(choices=Status.choices)
    minutes_late = serializers.IntegerField(allow_null=True)
    note = serializers.CharField()


class StudentAttendanceSerializer(TotalsSerializer):
    academic_year = serializers.IntegerField()
    days = serializers.IntegerField()
    events = StudentEventSerializer(many=True)


class StaffEntrySerializer(serializers.Serializer):
    staff = serializers.IntegerField()
    status = serializers.ChoiceField(choices=StaffAttendance.StaffStatus.choices)
    minutes_late = serializers.IntegerField(min_value=1, max_value=600, required=False, allow_null=True)
    note = serializers.CharField(max_length=255, allow_blank=True, required=False, default="")


class SaveStaffAttendanceSerializer(serializers.Serializer):
    date = serializers.DateField()
    entries = StaffEntrySerializer(many=True, max_length=1000)  # type: ignore[call-arg]  # ListSerializer option


class StaffRowSerializer(serializers.Serializer):
    staff = serializers.IntegerField()
    full_name = serializers.CharField()
    position = serializers.CharField()
    staff_type = serializers.CharField()
    recorded = serializers.BooleanField()
    status = serializers.ChoiceField(choices=StaffAttendance.StaffStatus.choices)
    minutes_late = serializers.IntegerField(allow_null=True)
    note = serializers.CharField()


class StaffSheetSerializer(serializers.Serializer):
    date = serializers.DateField()
    staff = StaffRowSerializer(many=True)


def today_or(value, school) -> date:
    from .services import school_today

    return value or school_today(school)
