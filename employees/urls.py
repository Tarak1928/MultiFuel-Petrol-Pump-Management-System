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
]
