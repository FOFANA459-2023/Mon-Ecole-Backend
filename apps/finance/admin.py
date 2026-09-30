from django.contrib import admin

from .models import (
    Expense,
    FeeCategory,
    FeeSchedule,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentAllocation,
    Refund,
    StudentDiscount,
)


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


class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    can_delete = False
    readonly_fields = ["invoice_line", "amount"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Read-only: payments are recorded and reversed only through the app, which keeps the audit trail."""

    list_display = ["number", "student", "date", "amount", "method", "status"]
    list_filter = ["school", "method", "status"]
    search_fields = ["number", "reference", "student__last_name", "student__student_number"]
    inlines = [PaymentAllocationInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(admin.ModelAdmin):
    """Money records change only through the app, which keeps the audit trail."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Expense)
class ExpenseAdmin(ReadOnlyAdmin):
    list_display = ["number", "date", "category", "amount", "method", "status"]
    list_filter = ["school", "category", "method", "status"]
    search_fields = ["number", "payee", "reference", "description"]


@admin.register(Refund)
class RefundAdmin(ReadOnlyAdmin):
    list_display = ["student", "date", "amount", "method", "status"]
    list_filter = ["school", "method", "status"]
    raw_id_fields = ["student"]
