from django.urls import path

from . import views


app_name = "employees"

urlpatterns = [
    path("", views.employee_list, name="employee-list"),
    path("new/", views.employee_create, name="employee-create"),
    path("<int:employee_id>/edit/", views.employee_update, name="employee-update"),
    path(
        "<int:employee_id>/status/",
        views.employee_toggle_status,
        name="employee-toggle-status",
    ),
    path("attendance/", views.attendance_list, name="attendance-list"),
    path(
        "attendance/stations/<int:station_id>/",
        views.attendance_list,
        name="attendance-station-list",
    ),
    path("attendance/new/", views.attendance_create, name="attendance-create"),
    path(
        "attendance/<int:attendance_id>/edit/",
        views.attendance_update,
        name="attendance-update",
    ),
    path("leave/", views.leave_list, name="leave-list"),
    path("leave/new/", views.leave_request_create, name="leave-request-create"),
    path(
        "leave/<int:leave_id>/decision/",
        views.leave_request_decide,
        name="leave-request-decide",
    ),
    path("advances/", views.employee_advance_list, name="advance-list"),
    path(
        "advances/new/",
        views.employee_advance_create,
        name="advance-create",
    ),
    path("payroll/", views.payroll_list, name="payroll-list"),
    path("payroll/new/", views.payroll_create, name="payroll-create"),
    path(
        "payroll/<int:payroll_id>/paid/",
        views.payroll_mark_paid,
        name="payroll-mark-paid",
    ),
    path(
        "payroll/<int:payroll_id>/",
        views.payslip,
        name="payslip",
    ),
]
