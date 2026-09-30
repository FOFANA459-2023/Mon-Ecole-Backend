from django.contrib import admin

from .models import FeeCategory, FeeSchedule, Invoice, InvoiceLine, StudentDiscount


@admin.register(FeeCategory)
class FeeCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "school", "is_active"]
    list_filter = ["school", "kind", "is_active"]


@admin.register(FeeSchedule)
class FeeScheduleAdmin(admin.ModelAdmin):
    list_display = ["category", "level", "academic_year", "applies_to", "amount"]
    list_filter = ["school", "academic_year", "category"]


@admin.register(StudentDiscount)
class StudentDiscountAdmin(admin.ModelAdmin):
    list_display = ["student", "academic_year", "category", "kind", "value", "reason", "is_active"]
    list_filter = ["school", "academic_year", "reason", "is_active"]
    raw_id_fields = ["student"]


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False
    readonly_fields = ["category", "description", "due_date", "amount", "discount"]


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Read-only: invoices change only through the app (issue, cancel), which keeps the audit trail."""

    list_display = ["number", "student", "academic_year", "issue_date", "total", "status"]
    list_filter = ["school", "academic_year", "status", "source"]
    search_fields = ["number", "student__last_name", "student__student_number"]
    inlines = [InvoiceLineInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
