from django.contrib import admin

from .models import Enrollment


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ["student", "class_group", "academic_year", "enrollment_date", "kind", "status"]
    list_filter = ["school", "academic_year", "status", "kind"]
    search_fields = ["student__last_name", "student__first_name", "student__student_number"]
    raw_id_fields = ["student"]
