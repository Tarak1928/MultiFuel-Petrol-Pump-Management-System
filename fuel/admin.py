from django.contrib import admin

from .models import FuelPrice, FuelType, Nozzle, Tank


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
