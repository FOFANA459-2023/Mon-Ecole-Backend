from django.contrib import admin

from .models import Guardian, StaffMember, Student, StudentGuardian


class GuardianLinkInline(admin.TabularInline):
    model = StudentGuardian
    extra = 0
    fields = ["guardian", "relationship", "is_primary", "is_financial_contact"]
    raw_id_fields = ["guardian"]


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ["student_number", "last_name", "first_name", "gender", "school", "status"]
    list_filter = ["school", "status", "gender"]
    search_fields = ["student_number", "last_name", "first_name"]
    inlines = [GuardianLinkInline]


@admin.register(Guardian)
class GuardianAdmin(admin.ModelAdmin):
    list_display = ["last_name", "first_name", "phone", "email", "school"]
    list_filter = ["school"]
    search_fields = ["last_name", "first_name", "phone", "email"]


@admin.register(StaffMember)
class StaffMemberAdmin(admin.ModelAdmin):
    list_display = [
        "employee_number",
        "last_name",
        "first_name",
        "staff_type",
        "position",
        "school",
        "status",
    ]
    list_filter = ["school", "status", "staff_type"]
    search_fields = ["employee_number", "last_name", "first_name", "email"]
