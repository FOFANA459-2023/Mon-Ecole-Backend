from django.contrib import admin

from .models import ClassRegister, StaffAttendance


@admin.register(ClassRegister)
class ClassRegisterAdmin(admin.ModelAdmin):
    list_display = ["class_group", "date", "created_by", "school"]
    list_filter = ["school", "date"]


@admin.register(StaffAttendance)
class StaffAttendanceAdmin(admin.ModelAdmin):
    list_display = ["staff", "date", "status", "school"]
    list_filter = ["school", "status", "date"]
