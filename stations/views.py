from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User

from .forms import StationForm
from .models import Station


def _owner_stations(user):
    return Station.objects.filter(owner=user)


@role_required(User.Role.OWNER)
def station_list(request):
    stations = _owner_stations(request.user)
    return render(request, "stations/station_list.html", {"stations": stations})


@role_required(User.Role.OWNER)
def station_create(request):
    if request.method == "POST":
        form = StationForm(request.POST)
        if form.is_valid():
            station = form.save(commit=False)
            station.owner = request.user
            station.save()
            messages.success(request, f"{station.name} was added to your stations.")
            return redirect("stations:station-list")
    else:
        form = StationForm()
    return render(
        request,
        "stations/station_form.html",
        {"form": form, "page_title": "Add a station", "submit_label": "Add station"},
    )


@role_required(User.Role.OWNER)
def station_update(request, station_id):
    station = get_object_or_404(_owner_stations(request.user), pk=station_id)
    if request.method == "POST":
        form = StationForm(request.POST, instance=station)
        if form.is_valid():
            form.save()
            messages.success(request, f"{station.name} details were updated.")
            return redirect("stations:station-list")
    else:
        form = StationForm(instance=station)
    return render(
        request,
        "stations/station_form.html",
        {
            "form": form,
            "station": station,
            "page_title": f"Edit {station.name}",
            "submit_label": "Save changes",
        },
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def station_toggle_status(request, station_id):
    station = get_object_or_404(
        _owner_stations(request.user).select_for_update(),
        pk=station_id,
    )
    station.is_active = not station.is_active
    station.save(update_fields=("is_active", "updated_at"))
    status = "activated" if station.is_active else "suspended"
    messages.success(request, f"{station.name} was {status}.")
    return redirect("stations:station-list")
