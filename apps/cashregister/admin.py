from django.contrib import admin

from .models import CashMovement, CashRegister, CashSession


@admin.register(CashRegister)
class CashRegisterAdmin(admin.ModelAdmin):
    list_display = ["name", "school", "is_active"]
    list_filter = ["school", "is_active"]


class CashMovementInline(admin.TabularInline):
    model = CashMovement
    extra = 0
    can_delete = False
    fields = ("created_at", "direction", "source", "amount", "description")
    readonly_fields = fields


@admin.register(CashSession)
class CashSessionAdmin(admin.ModelAdmin):
    """Read-only: sessions are opened, used and closed only through the app, which keeps the audit trail."""

    list_display = ["register", "opened_at", "status", "opening_balance", "counted_closing", "difference"]
    list_filter = ["school", "status"]
    inlines = [CashMovementInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
