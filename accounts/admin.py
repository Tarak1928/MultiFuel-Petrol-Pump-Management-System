from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.action(description="Approve selected accounts")
def approve_accounts(modeladmin, request, queryset):
    queryset.filter(requested_owner=request.user).exclude(
        role=User.Role.OWNER
    ).update(is_approved=True, approved_by=request.user)


@admin.register(User)
class MultiFuelUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        (
            "MultiFuel access",
            {"fields": ("role", "is_approved", "requested_owner", "approved_by")},
        ),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("MultiFuel access", {"fields": ("role", "requested_owner")}),
    )
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "role",
        "is_approved",
        "is_staff",
    )
    list_filter = UserAdmin.list_filter + ("role", "is_approved")
    actions = (approve_accounts,)
    readonly_fields = UserAdmin.readonly_fields + ("approved_by",)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
