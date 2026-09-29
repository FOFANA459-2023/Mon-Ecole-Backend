from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Membership, Role, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["username", "email", "first_name", "last_name", "is_active", "is_superuser", "last_login"]
    fieldsets = (
        *(BaseUserAdmin.fieldsets or ()),
        ("Mon École", {"fields": ("phone", "language", "must_change_password")}),
    )


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ["name", "school", "key", "is_system"]
    list_filter = ["school", "is_system"]
    search_fields = ["name", "school__name"]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "school", "is_active", "created_at"]
    list_filter = ["school", "is_active"]
    search_fields = ["user__email", "user__username", "school__name"]
    filter_horizontal = ["roles"]
