from django.urls import path

from . import views


app_name = "fuel"

urlpatterns = [
    path("types/", views.fuel_type_list, name="fuel-type-list"),
    path("types/new/", views.fuel_type_create, name="fuel-type-create"),
    path(
        "types/<int:fuel_type_id>/status/",
        views.fuel_type_toggle_status,
        name="fuel-type-toggle-status",
    ),
    path("stations/<int:station_id>/", views.station_fuel_setup, name="station-fuel"),
    path(
        "stations/<int:station_id>/inventory/",
        views.station_inventory,
        name="station-inventory",
    ),
    path(
        "stations/<int:station_id>/deliveries/new/",
        views.fuel_delivery_create,
        name="fuel-delivery-create",
    ),
    path(
        "stations/<int:station_id>/adjustments/new/",
        views.fuel_inventory_adjustment_create,
        name="inventory-adjustment-create",
    ),
    path(
        "stations/<int:station_id>/adjustments/<int:adjustment_id>/decision/",
        views.fuel_inventory_adjustment_decide,
        name="inventory-adjustment-decide",
    ),
    path(
        "stations/<int:station_id>/tanks/new/",
        views.tank_create,
        name="tank-create",
    ),
    path(
        "stations/<int:station_id>/tanks/<int:tank_id>/edit/",
        views.tank_update,
        name="tank-update",
    ),
    path(
        "stations/<int:station_id>/tanks/<int:tank_id>/status/",
        views.tank_toggle_status,
        name="tank-toggle-status",
    ),
    path(
        "stations/<int:station_id>/nozzles/new/",
        views.nozzle_create,
        name="nozzle-create",
    ),
    path(
        "stations/<int:station_id>/nozzles/<int:nozzle_id>/edit/",
        views.nozzle_update,
        name="nozzle-update",
    ),
    path(
        "stations/<int:station_id>/nozzles/<int:nozzle_id>/status/",
        views.nozzle_toggle_status,
        name="nozzle-toggle-status",
    ),
    path(
        "stations/<int:station_id>/prices/new/",
        views.fuel_price_create,
        name="fuel-price-create",
    ),
]
