from django.contrib import admin

from .models import Assessment, Gradebook, GradeCategory, GradingScale


@admin.register(GradingScale)
class GradingScaleAdmin(admin.ModelAdmin):
    list_display = ["school", "level", "max_mark", "pass_mark", "decimals", "rank_method"]
    list_filter = ["school"]


@admin.register(Gradebook)
class GradebookAdmin(admin.ModelAdmin):
    list_display = ["class_subject", "term", "status", "missing_policy", "school"]
    list_filter = ["school", "status"]
    list_select_related = ["class_subject__class_group", "class_subject__subject", "term__academic_year"]


@admin.register(GradeCategory)
class GradeCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "gradebook", "weight", "method"]


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ["name", "gradebook", "category", "date", "max_score", "weight"]
