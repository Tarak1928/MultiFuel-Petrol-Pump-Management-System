from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User
from employees.models import Employee
from stations.models import Station

from .forms import (
    FuelDeliveryForm,
    FuelInventoryAdjustmentDecisionForm,
    FuelInventoryAdjustmentForm,
    FuelPriceForm,
    FuelTypeForm,
    NozzleForm,
    TankForm,
    available_fuel_types,
)
from .models import (
    FuelDelivery,
    FuelInventoryAdjustment,
    FuelPrice,
    FuelType,
    InventoryLedger,
    Nozzle,
    Tank,
)


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


def _accessible_stations(user):
    if user.role == User.Role.OWNER:
        return Station.objects.filter(owner=user)
    if user.role == User.Role.MANAGER:
        employee = Employee.objects.filter(user=user, is_active=True).first()
        if not employee:
            return Station.objects.none()
        station_ids = employee.manager_assignments.filter(
            station__is_active=True
        ).values_list("station_id", flat=True)
        return Station.objects.filter(pk__in=station_ids, is_active=True)
    return Station.objects.none()


def _station_inventory_context(station, user):
    tanks = list(
        Tank.objects.filter(station=station)
        .select_related("fuel_type")
        .order_by("code")
    )
    return {
        "station": station,
        "tanks": tanks,
        "low_stock_tanks": [
            tank for tank in tanks if tank.is_active and tank.is_low_stock
        ],
        "deliveries": FuelDelivery.objects.filter(station=station)
        .select_related("tank", "recorded_by")
        .order_by("-received_at", "-pk")[:50],
        "adjustments": FuelInventoryAdjustment.objects.filter(station=station)
        .select_related("tank", "requested_by", "decided_by")
        .order_by("-requested_at", "-pk")[:50],
        "pending_adjustments": (
            FuelInventoryAdjustment.objects.filter(
                station=station,
                status=FuelInventoryAdjustment.Status.PENDING,
            )
            .select_related("tank", "requested_by")
            .order_by("requested_at")
            if user.role == User.Role.OWNER
            else []
        ),
        "ledger": InventoryLedger.objects.filter(tank__station=station)
        .select_related("tank", "recorded_by", "report", "delivery", "adjustment")
        .order_by("-created_at", "-pk")[:100],
        "can_manage_deliveries": (
            station.is_active and user.role == User.Role.OWNER
        ),
        "can_request_adjustment": (
            station.is_active
            and user.role in (User.Role.OWNER, User.Role.MANAGER)
        ),
    }


@role_required(User.Role.OWNER, User.Role.MANAGER)
def station_inventory(request, station_id):
    station = get_object_or_404(_accessible_stations(request.user), pk=station_id)
    return render(
        request,
        "fuel/station_inventory.html",
        _station_inventory_context(station, request.user),
    )


@role_required(User.Role.OWNER)
def fuel_delivery_create(request, station_id):
    station = get_object_or_404(
        _owner_stations(request.user).filter(is_active=True),
        pk=station_id,
    )
    if request.method == "POST":
        form = FuelDeliveryForm(station, request.POST)
        if form.is_valid():
            delivery = None
            try:
                with transaction.atomic():
                    tank = get_object_or_404(
                        Tank.objects.select_for_update().filter(
                            station=station,
                            is_active=True,
                        ),
                        pk=form.cleaned_data["tank"].pk,
                    )
                    quantity = form.cleaned_data["quantity_litres"]
                    if tank.current_stock_litres + quantity > tank.capacity_litres:
                        form.add_error(
                            "quantity_litres",
                            "This delivery would exceed the tank's remaining capacity.",
                        )
                    else:
                        delivery = form.save(commit=False)
                        delivery.station = station
                        delivery.tank = tank
                        delivery.recorded_by = request.user
                        delivery.save()
                        tank.current_stock_litres += quantity
                        tank.save(
                            update_fields=("current_stock_litres", "updated_at")
                        )
                        InventoryLedger.objects.create(
                            tank=tank,
                            transaction_type=InventoryLedger.TransactionType.DELIVERY,
                            quantity_delta=quantity,
                            delivery=delivery,
                            recorded_by=request.user,
                            notes=f"Invoice {delivery.invoice_number}",
                        )
            except IntegrityError:
                duplicate = FuelDelivery.objects.filter(
                    station=station,
                    invoice_number__iexact=form.cleaned_data["invoice_number"],
                    invoice_line_number=form.cleaned_data["invoice_line_number"],
                ).exists()
                if not duplicate:
                    raise
                form.add_error(
                    "invoice_number",
                    "This invoice line has already been recorded at the station.",
                )
            if delivery:
                messages.success(
                    request,
                    f"Received {delivery.quantity_litres} L for tank {delivery.tank.code}.",
                )
                return redirect("fuel:station-inventory", station_id=station.pk)
    else:
        form = FuelDeliveryForm(station)
    return render(
        request,
        "fuel/form.html",
        {
            "form": form,
            "page_title": f"Record fuel delivery at {station.name}",
            "submit_label": "Record delivery",
            "cancel_url": "fuel:station-inventory",
            "cancel_kwargs": {"station_id": station.pk},
            "help_text": (
                "The delivery is recorded against its supplier invoice and "
                "increases tank stock atomically."
            ),
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER)
def fuel_inventory_adjustment_create(request, station_id):
    station = get_object_or_404(_accessible_stations(request.user), pk=station_id)
    if not station.is_active:
        raise PermissionDenied
    if request.method == "POST":
        form = FuelInventoryAdjustmentForm(station, request.POST)
        if form.is_valid():
            with transaction.atomic():
                tank = get_object_or_404(
                    Tank.objects.select_for_update().filter(
                        station=station,
                        is_active=True,
                    ),
                    pk=form.cleaned_data["tank"].pk,
                )
                delta = form.cleaned_data["quantity_delta"]
                adjusted_stock = tank.current_stock_litres + delta
                if adjusted_stock < 0 or adjusted_stock > tank.capacity_litres:
                    form.add_error(
                        "quantity_delta",
                        "The adjustment must keep stock between zero and tank capacity.",
                    )
                else:
                    is_owner = request.user.role == User.Role.OWNER
                    adjustment = FuelInventoryAdjustment(
                        station=station,
                        tank=tank,
                        adjustment_type=form.cleaned_data["adjustment_type"],
                        quantity_delta=delta,
                        reason=form.cleaned_data["reason"].strip(),
                        status=(
                            FuelInventoryAdjustment.Status.APPROVED
                            if is_owner
                            else FuelInventoryAdjustment.Status.PENDING
                        ),
                        requested_by=request.user,
                        decided_by=request.user if is_owner else None,
                        decided_at=timezone.now() if is_owner else None,
                    )
                    adjustment.save()
                    if is_owner:
                        tank.current_stock_litres = adjusted_stock
                        tank.save(
                            update_fields=("current_stock_litres", "updated_at")
                        )
                        InventoryLedger.objects.create(
                            tank=tank,
                            transaction_type=InventoryLedger.TransactionType.ADJUSTMENT,
                            quantity_delta=delta,
                            adjustment=adjustment,
                            recorded_by=request.user,
                            notes=adjustment.reason,
                        )
                    if is_owner:
                        messages.success(request, "Inventory adjustment was approved and applied.")
                    else:
                        messages.success(request, "Inventory adjustment was sent to the Owner.")
                    return redirect("fuel:station-inventory", station_id=station.pk)
    else:
        form = FuelInventoryAdjustmentForm(station)
    return render(
        request,
        "fuel/form.html",
        {
            "form": form,
            "page_title": f"Request inventory adjustment at {station.name}",
            "submit_label": "Save adjustment",
            "cancel_url": "fuel:station-inventory",
            "cancel_kwargs": {"station_id": station.pk},
            "help_text": (
                "Leakage and evaporation are negative quantities. Managers submit "
                "adjustments for Owner approval; Owner adjustments are applied immediately."
            ),
        },
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def fuel_inventory_adjustment_decide(request, station_id, adjustment_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    adjustment = get_object_or_404(
        FuelInventoryAdjustment.objects.select_for_update().filter(
            station=station,
            status=FuelInventoryAdjustment.Status.PENDING,
        ),
        pk=adjustment_id,
    )
    tank = get_object_or_404(
        Tank.objects.select_for_update(),
        pk=adjustment.tank_id,
        station=station,
    )
    form = FuelInventoryAdjustmentDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            "Adjustment decision was not saved: "
            + "; ".join(
                error
                for errors in form.errors.values()
                for error in errors
            ),
        )
        return redirect("fuel:station-inventory", station_id=station.pk)

    adjustment.decision_note = form.cleaned_data["decision_note"].strip()
    adjustment.decided_by = request.user
    adjustment.decided_at = timezone.now()
    if form.cleaned_data["action"] == "approve":
        adjusted_stock = tank.current_stock_litres + adjustment.quantity_delta
        if adjusted_stock < 0 or adjusted_stock > tank.capacity_litres:
            messages.error(
                request,
                "Adjustment cannot be approved because it would move tank stock "
                "outside zero and capacity.",
            )
            return redirect("fuel:station-inventory", station_id=station.pk)
        adjustment.status = FuelInventoryAdjustment.Status.APPROVED
        adjustment.save()
        tank.current_stock_litres = adjusted_stock
        tank.save(update_fields=("current_stock_litres", "updated_at"))
        InventoryLedger.objects.create(
            tank=tank,
            transaction_type=InventoryLedger.TransactionType.ADJUSTMENT,
            quantity_delta=adjustment.quantity_delta,
            adjustment=adjustment,
            recorded_by=request.user,
            notes=adjustment.reason,
        )
        messages.success(request, "Adjustment was approved and applied.")
    else:
        adjustment.status = FuelInventoryAdjustment.Status.REJECTED
        adjustment.save()
        messages.success(request, "Adjustment request was rejected.")
    return redirect("fuel:station-inventory", station_id=station.pk)


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
            with transaction.atomic():
                tank = _save_with_unique_guard(
                    form,
                    "code",
                    lambda: Tank.objects.filter(
                        station=station,
                        code=form.cleaned_data["code"],
                    ),
                    "A tank with this code already exists at this station.",
                )
                if tank and tank.current_stock_litres:
                    InventoryLedger.objects.create(
                        tank=tank,
                        transaction_type=InventoryLedger.TransactionType.OPENING,
                        quantity_delta=tank.current_stock_litres,
                        recorded_by=request.user,
                        notes="Opening balance from tank setup.",
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
