from django.contrib import admin

from .models import (
    FuelDelivery,
    FuelInventoryAdjustment,
    FuelPrice,
    FuelType,
    InventoryLedger,
    Nozzle,
    Tank,
)


@admin.register(FuelType)
class FuelTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "owner", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code", "owner__username")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Tank)
class TankAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "station",
        "fuel_type",
        "capacity_litres",
        "current_stock_litres",
        "is_active",
    )
    list_filter = ("is_active", "fuel_type")
    search_fields = ("code", "station__name", "station__code")
    readonly_fields = ("current_stock_litres",)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Nozzle)
class NozzleAdmin(admin.ModelAdmin):
    list_display = ("code", "station", "tank", "fuel_type", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "station__name", "tank__code")

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FuelPrice)
class FuelPriceAdmin(admin.ModelAdmin):
    list_display = ("station", "fuel_type", "price_per_litre", "effective_from", "recorded_by")
    list_filter = ("station", "fuel_type")
    search_fields = ("station__name", "station__code", "fuel_type__name")
    readonly_fields = (
        "station",
        "fuel_type",
        "price_per_litre",
        "effective_from",
        "recorded_by",
        "created_at",
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


@admin.register(FuelDelivery)
class FuelDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "station",
        "tank",
        "invoice_number",
        "supplier_name",
        "quantity_litres",
        "total_cost",
        "received_at",
    )
    list_filter = ("station", "received_at")
    search_fields = ("invoice_number", "supplier_name", "station__code")
    readonly_fields = tuple(field.name for field in FuelDelivery._meta.fields)

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


@admin.register(FuelInventoryAdjustment)
class FuelInventoryAdjustmentAdmin(admin.ModelAdmin):
    list_display = (
        "station",
        "tank",
        "adjustment_type",
        "quantity_delta",
        "status",
        "requested_by",
        "decided_by",
    )
    list_filter = ("station", "status", "adjustment_type")
    search_fields = ("reason", "tank__code", "station__code")
    readonly_fields = tuple(
        field.name for field in FuelInventoryAdjustment._meta.fields
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


@admin.register(InventoryLedger)
class InventoryLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "tank",
        "transaction_type",
        "quantity_delta",
        "recorded_by",
        "created_at",
    )
    list_filter = ("tank__station", "transaction_type", "created_at")
    search_fields = ("tank__code", "notes", "delivery__invoice_number")
    readonly_fields = tuple(field.name for field in InventoryLedger._meta.fields)

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
