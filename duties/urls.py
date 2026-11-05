from django.urls import path

from . import views


app_name = "duties"

urlpatterns = [
    path("", views.report_list, name="report-list"),
    path("new/", views.report_create, name="report-create"),
    path(
        "<int:report_id>/fuel-entries/",
        views.report_fuel_entries,
        name="report-fuel-entries",
    ),
    path("<int:report_id>/", views.report_detail, name="report-detail"),
    path("<int:report_id>/edit/", views.report_update, name="report-update"),
    path("<int:report_id>/submit/", views.report_submit, name="report-submit"),
    path(
        "stations/<int:station_id>/shifts/",
        views.shift_list,
        name="shift-list",
    ),
    path(
        "stations/<int:station_id>/shifts/new/",
        views.shift_create,
        name="shift-create",
    ),
    path(
        "stations/<int:station_id>/shifts/<int:shift_id>/edit/",
        views.shift_update,
        name="shift-update",
    ),
    path(
        "stations/<int:station_id>/shifts/<int:shift_id>/status/",
        views.shift_toggle_status,
        name="shift-toggle-status",
    ),
    path(
        "stations/<int:station_id>/lubricants/",
        views.lubricant_list,
        name="lubricant-list",
    ),
    path(
        "stations/<int:station_id>/lubricants/new/",
        views.lubricant_create,
        name="lubricant-create",
    ),
    path(
        "stations/<int:station_id>/lubricants/<int:lubricant_id>/edit/",
        views.lubricant_update,
        name="lubricant-update",
    ),
    path(
        "stations/<int:station_id>/lubricants/<int:lubricant_id>/status/",
        views.lubricant_toggle_status,
        name="lubricant-toggle-status",
    ),
    path(
        "<int:report_id>/lubricant-entries/",
        views.report_lubricant_entries,
        name="report-lubricant-entries",
    ),
]
