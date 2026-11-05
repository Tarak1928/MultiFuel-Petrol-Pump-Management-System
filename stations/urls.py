from django.urls import path

from . import views


app_name = "stations"

urlpatterns = [
    path("", views.station_list, name="station-list"),
    path("new/", views.station_create, name="station-create"),
    path("<int:station_id>/edit/", views.station_update, name="station-update"),
    path(
        "<int:station_id>/status/",
        views.station_toggle_status,
        name="station-toggle-status",
    ),
]
