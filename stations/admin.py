from django.contrib import admin

from .models import Station


@admin.register(Station)
class StationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "city", "state", "owner", "is_active", "updated_at")
    list_filter = ("is_active", "state")
    search_fields = ("name", "code", "city", "owner__username", "owner__email")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("name", "code")

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
