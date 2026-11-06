from django.contrib import admin

from .models import Expense


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = (
        "station",
        "category",
        "amount",
        "expense_date",
        "status",
        "recorded_by",
    )
    list_filter = ("station", "category", "status", "expense_date")
    search_fields = ("reference", "payee", "description")
    readonly_fields = (
        "station",
        "category",
        "amount",
        "expense_date",
        "payee",
        "reference",
        "description",
        "status",
        "recorded_by",
        "recorded_at",
        "decided_by",
        "decided_at",
        "decision_note",
        "payroll",
    )

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
