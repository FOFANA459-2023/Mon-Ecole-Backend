from django.contrib import admin

from .models import AcademicYear, ClassGroup, ClassSubject, Level, Subject, Term


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ["name", "school", "start_date", "end_date", "is_current", "status"]
    list_filter = ["school", "is_current"]


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ["name", "academic_year", "order", "start_date", "end_date"]


@admin.register(Level)
class LevelAdmin(admin.ModelAdmin):
    list_display = ["name", "school", "order", "cycle", "is_active"]
    list_filter = ["school", "cycle"]


@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ["name", "school", "academic_year", "level", "class_teacher", "capacity", "status"]
    list_filter = ["school", "academic_year", "status"]
    search_fields = ["name"]


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "school", "level", "default_coefficient", "is_active"]
    list_filter = ["school"]
    search_fields = ["name", "code"]


@admin.register(ClassSubject)
class ClassSubjectAdmin(admin.ModelAdmin):
    list_display = ["class_group", "subject", "teacher", "coefficient"]
    list_filter = ["class_group__school"]
