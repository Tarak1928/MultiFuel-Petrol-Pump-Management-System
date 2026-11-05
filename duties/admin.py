from django.contrib import admin

from .models import (
    DutyFuelEntry,
    DutyLubricantEntry,
    DutyReport,
    Lubricant,
    Shift,
)


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("name", "station", "start_time", "end_time", "is_active")
    list_filter = ("station", "is_active")
    search_fields = ("name", "station__name", "station__code")

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


@admin.register(DutyReport)
class DutyReportAdmin(admin.ModelAdmin):
    list_display = ("station", "employee", "shift", "report_date", "status")
    list_filter = ("station", "status", "report_date")
    search_fields = ("station__name", "employee__employee_code", "employee__user__username")
    readonly_fields = (
        "station",
        "employee",
        "shift",
        "report_date",
        "status",
        "notes",
        "submission_token",
        "submitted_at",
        "created_at",
        "updated_at",
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


@admin.register(DutyFuelEntry)
class DutyFuelEntryAdmin(admin.ModelAdmin):
    list_display = (
        "report",
        "nozzle",
        "opening_reading",
        "closing_reading",
        "testing_litres",
        "actual_sale_litres",
        "price_per_litre",
        "sale_amount",
    )
    list_filter = ("report__station", "nozzle__tank__fuel_type")
    search_fields = ("report__station__code", "nozzle__code")

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


@admin.register(Lubricant)
class LubricantAdmin(admin.ModelAdmin):
    list_display = (
        "station",
        "code",
        "name",
        "current_stock",
        "selling_price",
        "is_active",
    )
    list_filter = ("station", "is_active")
    search_fields = ("station__name", "station__code", "code", "name")

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


@admin.register(DutyLubricantEntry)
class DutyLubricantEntryAdmin(admin.ModelAdmin):
    list_display = ("report", "lubricant", "quantity", "unit_price", "sale_amount")
    list_filter = ("report__station", "lubricant")
    search_fields = ("report__station__code", "lubricant__code", "lubricant__name")

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
