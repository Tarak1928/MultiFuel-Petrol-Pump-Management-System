from django.contrib import admin

from .models import (
    Attendance,
    Employee,
    EmployeeAdvance,
    LeaveRequest,
    ManagerAssignment,
    Payroll,
    PayrollAdvanceRepayment,
)


class ManagerAssignmentInline(admin.TabularInline):
    model = ManagerAssignment
    extra = 0


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "employee_code",
        "user",
        "owner",
        "assigned_station",
        "is_active",
    )
    list_filter = ("is_active", "user__role")
    search_fields = ("employee_code", "user__username", "user__email")
    inlines = (ManagerAssignmentInline,)

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


@admin.register(ManagerAssignment)
class ManagerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("employee", "station", "created_at")
    list_filter = ("station",)

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


class SuperuserReadOnlyAdmin(admin.ModelAdmin):
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


@admin.register(Attendance)
class AttendanceAdmin(SuperuserReadOnlyAdmin):
    list_display = ("employee", "station", "work_date", "status", "overtime_hours")
    list_filter = ("station", "status", "work_date")
    search_fields = ("employee__employee_code", "employee__user__username")


@admin.register(LeaveRequest)
class LeaveRequestAdmin(SuperuserReadOnlyAdmin):
    list_display = ("employee", "leave_type", "start_date", "end_date", "status")
    list_filter = ("status", "leave_type")
    search_fields = ("employee__employee_code", "employee__user__username")


@admin.register(EmployeeAdvance)
class EmployeeAdvanceAdmin(SuperuserReadOnlyAdmin):
    list_display = ("employee", "reference", "amount", "issued_date")
    search_fields = ("employee__employee_code", "reference")


@admin.register(Payroll)
class PayrollAdmin(SuperuserReadOnlyAdmin):
    list_display = (
        "employee",
        "year",
        "month",
        "gross_pay",
        "net_pay",
        "status",
    )
    list_filter = ("year", "month", "status")
    search_fields = ("employee__employee_code", "employee__user__username")


@admin.register(PayrollAdvanceRepayment)
class PayrollAdvanceRepaymentAdmin(SuperuserReadOnlyAdmin):
    list_display = ("payroll", "advance", "amount")
