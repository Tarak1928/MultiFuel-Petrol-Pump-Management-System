from django.contrib import admin

from .models import (
    Advance,
    CreditCustomer,
    CreditRepayment,
    CreditSale,
    OTPTransaction,
)


class ReadOnlyFinanceAdmin(admin.ModelAdmin):
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


@admin.register(CreditCustomer)
class CreditCustomerAdmin(admin.ModelAdmin):
    list_display = (
        "station",
        "customer_code",
        "name",
        "phone",
        "credit_limit",
        "is_active",
    )
    list_filter = ("station", "is_active")
    search_fields = ("customer_code", "name", "phone", "vehicle_number")

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


@admin.register(CreditSale)
class CreditSaleAdmin(ReadOnlyFinanceAdmin):
    list_display = (
        "report",
        "customer",
        "fuel_type",
        "litres",
        "sale_amount",
        "due_date",
    )
    list_filter = ("report__station", "fuel_type", "due_date")
    search_fields = ("customer__name", "customer__customer_code", "vehicle_number")


@admin.register(CreditRepayment)
class CreditRepaymentAdmin(ReadOnlyFinanceAdmin):
    list_display = ("sale", "amount", "payment_method", "received_by", "repaid_at")
    list_filter = ("payment_method", "sale__report__station", "repaid_at")
    search_fields = ("sale__customer__name", "reference")


@admin.register(Advance)
class AdvanceAdmin(ReadOnlyFinanceAdmin):
    list_display = ("report", "reference", "amount", "recorded_by", "created_at")
    list_filter = ("report__station", "created_at")
    search_fields = ("reference", "report__station__code")


@admin.register(OTPTransaction)
class OTPTransactionAdmin(ReadOnlyFinanceAdmin):
    list_display = (
        "report",
        "transaction_reference",
        "provider",
        "amount",
        "recorded_by",
        "confirmed_at",
    )
    list_filter = ("provider", "report__station", "confirmed_at")
    search_fields = ("transaction_reference", "report__station__code")
