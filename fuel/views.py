from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User
from stations.models import Station

from .forms import FuelPriceForm, FuelTypeForm, NozzleForm, TankForm, available_fuel_types
from .models import FuelPrice, FuelType, Nozzle, Tank


def _save_with_unique_guard(form, field_name, conflict_query, message):
    try:
        with transaction.atomic():
            return form.save()
    except IntegrityError:
        if conflict_query().exists():
            form.add_error(field_name, message)
            return None
        raise


def _owner_stations(user):
    return Station.objects.filter(owner=user)


@role_required(User.Role.OWNER)
def fuel_type_list(request):
    fuel_types = FuelType.objects.filter(
        Q(owner__isnull=True) | Q(owner=request.user)
    )
    return render(
        request,
        "fuel/fuel_type_list.html",
        {"fuel_types": fuel_types},
    )
@role_required(User.Role.OWNER)
def fuel_type_create(request):
    if request.method == "POST":
        form = FuelTypeForm(request.user, request.POST)
        if form.is_valid():
            fuel_type = _save_with_unique_guard(
                form,
                "code",
                lambda: FuelType.objects.filter(code=form.cleaned_data["code"]),
                "A fuel type with this code already exists.",
            )
            if fuel_type:
                messages.success(request, f"{fuel_type.name} was added to your fuel types.")
                return redirect("fuel:fuel-type-list")
    else:
        form = FuelTypeForm(request.user)
    return render(
        request,
        "fuel/form.html",
        {
            "form": form,
            "page_title": "Add a fuel type",
            "submit_label": "Add fuel type",
            "cancel_url": "fuel:fuel-type-list",
        },
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def fuel_type_toggle_status(request, fuel_type_id):
    fuel_type = get_object_or_404(
        FuelType.objects.select_for_update(),
        pk=fuel_type_id,
        owner=request.user,
    )
    fuel_type.is_active = not fuel_type.is_active
    fuel_type.save(update_fields=("is_active", "updated_at"))
    status = "activated" if fuel_type.is_active else "deactivated"
    messages.success(request, f"{fuel_type.name} was {status}.")
    return redirect("fuel:fuel-type-list")


@role_required(User.Role.OWNER)
def station_fuel_setup(request, station_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    tanks = station.tanks.select_related("fuel_type")
    nozzles = station.nozzles.select_related("tank", "tank__fuel_type")
    all_prices = station.fuel_prices.select_related("fuel_type", "recorded_by")
    effective_prices = all_prices.filter(effective_from__lte=timezone.now())
    current_prices = []
    seen_fuel_types = set()
    for price in effective_prices:
        if price.fuel_type_id not in seen_fuel_types:
            current_prices.append(price)
            seen_fuel_types.add(price.fuel_type_id)

    return render(
        request,
        "fuel/station_setup.html",
        {
            "station": station,
            "tanks": tanks,
            "nozzles": nozzles,
            "current_prices": current_prices,
            "price_history": all_prices[:50],
            "available_fuel_types": available_fuel_types(request.user),
        },
    )


@role_required(User.Role.OWNER)
def tank_create(request, station_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    if request.method == "POST":
        form = TankForm(request.user, station, request.POST)
        if form.is_valid():
            tank = _save_with_unique_guard(
                form,
                "code",
                lambda: Tank.objects.filter(
                    station=station,
                    code=form.cleaned_data["code"],
                ),
                "A tank with this code already exists at this station.",
            )
            if tank:
                messages.success(request, f"Tank {tank.code} was added to {station.name}.")
                return redirect("fuel:station-fuel", station_id=station.pk)
    else:
        form = TankForm(request.user, station)
    return render(
        request,
        "fuel/form.html",
        {
            "form": form,
            "page_title": f"Add tank at {station.name}",
            "submit_label": "Add tank",
            "cancel_url": "fuel:station-fuel",
            "cancel_kwargs": {"station_id": station.pk},
        },
    )


@role_required(User.Role.OWNER)
def tank_update(request, station_id, tank_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    tank = get_object_or_404(Tank.objects.filter(station=station), pk=tank_id)
    if request.method == "POST":
        form = TankForm(request.user, station, request.POST, instance=tank)
        if form.is_valid():
            tank = _save_with_unique_guard(
                form,
                "code",
                lambda: Tank.objects.filter(
                    station=station,
                    code=form.cleaned_data["code"],
                ).exclude(pk=tank.pk),
                "A tank with this code already exists at this station.",
            )
            if tank:
                messages.success(request, f"Tank {tank.code} was updated.")
                return redirect("fuel:station-fuel", station_id=station.pk)
    else:
        form = TankForm(request.user, station, instance=tank)
    return render(
        request,
        "fuel/form.html",
        {
            "form": form,
            "page_title": f"Edit tank {tank.code}",
            "submit_label": "Save changes",
            "cancel_url": "fuel:station-fuel",
            "cancel_kwargs": {"station_id": station.pk},
        },
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def tank_toggle_status(request, station_id, tank_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    tank = get_object_or_404(
        Tank.objects.select_for_update().filter(station=station),
        pk=tank_id,
    )
    tank.is_active = not tank.is_active
    tank.save(update_fields=("is_active", "updated_at"))
    status = "activated" if tank.is_active else "deactivated"
    messages.success(request, f"Tank {tank.code} was {status}.")
    return redirect("fuel:station-fuel", station_id=station.pk)


@role_required(User.Role.OWNER)
def nozzle_create(request, station_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    if request.method == "POST":
        form = NozzleForm(station, request.POST)
        if form.is_valid():
            nozzle = _save_with_unique_guard(
                form,
                "code",
                lambda: Nozzle.objects.filter(
                    station=station,
                    code=form.cleaned_data["code"],
                ),
                "A nozzle with this code already exists at this station.",
            )
            if nozzle:
                messages.success(request, f"Nozzle {nozzle.code} was added to {station.name}.")
                return redirect("fuel:station-fuel", station_id=station.pk)
    else:
        form = NozzleForm(station)
    return render(
        request,
        "fuel/form.html",
        {
            "form": form,
            "page_title": f"Add nozzle at {station.name}",
            "submit_label": "Add nozzle",
            "cancel_url": "fuel:station-fuel",
            "cancel_kwargs": {"station_id": station.pk},
        },
    )


@role_required(User.Role.OWNER)
def nozzle_update(request, station_id, nozzle_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    nozzle = get_object_or_404(Nozzle.objects.filter(station=station), pk=nozzle_id)
    if request.method == "POST":
        form = NozzleForm(station, request.POST, instance=nozzle)
        if form.is_valid():
            nozzle = _save_with_unique_guard(
                form,
                "code",
                lambda: Nozzle.objects.filter(
                    station=station,
                    code=form.cleaned_data["code"],
                ).exclude(pk=nozzle.pk),
                "A nozzle with this code already exists at this station.",
            )
            if nozzle:
                messages.success(request, f"Nozzle {nozzle.code} was updated.")
                return redirect("fuel:station-fuel", station_id=station.pk)
    else:
        form = NozzleForm(station, instance=nozzle)
    return render(
        request,
        "fuel/form.html",
        {
            "form": form,
            "page_title": f"Edit nozzle {nozzle.code}",
            "submit_label": "Save changes",
            "cancel_url": "fuel:station-fuel",
            "cancel_kwargs": {"station_id": station.pk},
        },
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def nozzle_toggle_status(request, station_id, nozzle_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    nozzle = get_object_or_404(
        Nozzle.objects.select_for_update().filter(station=station),
        pk=nozzle_id,
    )
    nozzle.is_active = not nozzle.is_active
    nozzle.save(update_fields=("is_active", "updated_at"))
    status = "activated" if nozzle.is_active else "deactivated"
    messages.success(request, f"Nozzle {nozzle.code} was {status}.")
    return redirect("fuel:station-fuel", station_id=station.pk)


@role_required(User.Role.OWNER)
def fuel_price_create(request, station_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    if request.method == "POST":
        form = FuelPriceForm(request.user, station, request.POST)
        if form.is_valid():
            price = _save_with_unique_guard(
                form,
                "effective_from",
                lambda: FuelPrice.objects.filter(
                    station=station,
                    fuel_type=form.cleaned_data["fuel_type"],
                    effective_from=form.cleaned_data["effective_from"],
                ),
                "A price for this fuel type is already recorded at that time.",
            )
            if price:
                messages.success(
                    request,
                    f"{price.fuel_type.name} price was recorded for {station.name}.",
                )
                return redirect("fuel:station-fuel", station_id=station.pk)
    else:
        form = FuelPriceForm(request.user, station)
    return render(
        request,
        "fuel/form.html",
        {
            "form": form,
            "page_title": f"Record fuel price at {station.name}",
            "submit_label": "Record price",
            "cancel_url": "fuel:station-fuel",
            "cancel_kwargs": {"station_id": station.pk},
            "help_text": "Price records are append-only so historical prices remain available.",
        },
    )
