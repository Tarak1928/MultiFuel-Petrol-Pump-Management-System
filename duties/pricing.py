from datetime import datetime

from django.utils import timezone

from fuel.models import FuelPrice, Nozzle


def effective_price_time_for_report(report):
    local_start = datetime.combine(report.report_date, report.shift.start_time)
    return timezone.make_aware(local_start, timezone.get_current_timezone())


def effective_price_for_report(report, nozzle: Nozzle):
    if nozzle.station_id != report.station_id:
        return None
    return (
        FuelPrice.objects.filter(
            station=report.station,
            fuel_type=nozzle.tank.fuel_type,
            effective_from__lte=effective_price_time_for_report(report),
        )
        .select_related("fuel_type")
        .order_by("-effective_from", "-pk")
        .first()
    )
