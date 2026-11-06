import csv
import html
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.decorators import role_required
from accounts.models import User
from duties.models import DutyReport
from employees.models import Attendance, Payroll
from expenses.models import Expense
from fuel.models import InventoryLedger, Tank
from stations.models import Station


REPORTS = {
    "sales": "Sales and expenses",
    "inventory": "Fuel inventory",
    "duty": "Duty reports and reconciliation",
    "workforce": "Payroll and attendance",
}


def _accessible_stations(user):
    stations = Station.objects.all()
    if user.role == User.Role.OWNER:
        return stations.filter(owner=user).order_by("name")
    if user.role == User.Role.MANAGER:
        return stations.filter(
            manager_assignments__employee__user=user,
            manager_assignments__employee__is_active=True,
            is_active=True,
        ).distinct().order_by("name")
    return Station.objects.none()


def _date_range(request):
    today = timezone.localdate()
    start_default = today.replace(day=1)
    try:
        start = (
            date.fromisoformat(request.GET["start_date"])
            if request.GET.get("start_date")
            else start_default
        )
        end = (
            date.fromisoformat(request.GET["end_date"])
            if request.GET.get("end_date")
            else today
        )
    except ValueError:
        messages.error(request, "Enter dates in YYYY-MM-DD format.")
        return start_default, today
    if end < start:
        messages.error(request, "The end date must be on or after the start date.")
        return start_default, today
    return start, end


def _money(value):
    return value or Decimal("0.00")


def _sales_rows(stations, start, end):
    rows = []
    for station in stations:
        reports = DutyReport.objects.filter(
            station=station,
            status=DutyReport.Status.APPROVED,
            report_date__range=(start, end),
        )
        fuel_sales = _money(reports.aggregate(total=Sum("fuel_entries__sale_amount"))["total"])
        lubricant_sales = _money(
            reports.aggregate(total=Sum("lubricant_entries__sale_amount"))["total"]
        )
        expenses = _money(
            Expense.objects.filter(
                station=station,
                status=Expense.Status.APPROVED,
                expense_date__range=(start, end),
            ).aggregate(total=Sum("amount"))["total"]
        )
        total_sales = fuel_sales + lubricant_sales
        rows.append(
            {
                "Station": station.name,
                "Code": station.code,
                "Approved fuel sales": fuel_sales,
                "Approved lubricant sales": lubricant_sales,
                "Total approved sales": total_sales,
                "Approved expenses": expenses,
                "Sales less expenses": total_sales - expenses,
            }
        )
    return rows


def _inventory_rows(stations, start, end):
    tanks = Tank.objects.filter(station__in=stations, is_active=True).select_related(
        "station", "fuel_type"
    )
    opening_balances = {
        row["tank_id"]: row["balance"]
        for row in InventoryLedger.objects.filter(
            tank__station__in=stations,
            created_at__date__lt=start,
        )
        .values("tank_id")
        .annotate(balance=Sum("quantity_delta"))
    }
    daily_movements = (
        InventoryLedger.objects.filter(
            tank__station__in=stations,
            created_at__date__range=(start, end),
        )
        .annotate(day=TruncDate("created_at"))
        .values("tank_id", "day")
        .annotate(
            net_movement=Sum("quantity_delta"),
            transaction_count=Count("pk"),
        )
        .order_by("day", "tank_id")
    )
    movements_by_tank = {}
    for movement in daily_movements:
        movements_by_tank.setdefault(movement["tank_id"], []).append(movement)

    rows = []
    for tank in tanks:
        opening_balance = opening_balances.get(tank.pk, Decimal("0.000"))
        running_stock = opening_balance
        tank_movements = movements_by_tank.get(tank.pk, [])
        if not tank_movements:
            tank_movements = [
                {
                    "day": None,
                    "net_movement": Decimal("0.000"),
                    "transaction_count": 0,
                }
            ]
        for movement in tank_movements:
            running_stock += movement["net_movement"] or Decimal("0.000")
            rows.append(
                {
                    "Date": movement["day"] or "No movements",
                    "Station": tank.station.name,
                    "Station code": tank.station.code,
                    "Tank": tank.code,
                    "Fuel": tank.fuel_type.name,
                    "Current stock (L)": tank.current_stock_litres,
                    "Stock after movement (L)": running_stock,
                    "Capacity (L)": tank.capacity_litres,
                    "Reorder level (L)": tank.reorder_level_litres,
                    "Low stock after movement": (
                        "Yes"
                        if tank.reorder_level_litres > 0
                        and running_stock <= tank.reorder_level_litres
                        else "No"
                    ),
                    "Daily net movement (L)": movement["net_movement"],
                    "Transactions": movement["transaction_count"],
                }
            )
    return rows


def _duty_rows(stations, start, end):
    reports = (
        DutyReport.objects.filter(station__in=stations, report_date__range=(start, end))
        .select_related("station", "employee__user", "shift", "payment_collection")
        .prefetch_related("fuel_entries", "lubricant_entries")
    )
    rows = []
    for report in reports:
        fuel_sales = sum(
            (entry.sale_amount for entry in report.fuel_entries.all()),
            Decimal("0.00"),
        )
        lubricant_sales = sum(
            (entry.sale_amount for entry in report.lubricant_entries.all()),
            Decimal("0.00"),
        )
        collection = getattr(report, "payment_collection", None)
        reconciliation = (
            collection.reconciliation_summary()
            if collection is not None
            else {}
        )
        rows.append(
            {
                "Date": report.report_date,
                "Station": report.station.name,
                "Station code": report.station.code,
                "Shift": report.shift.name,
                "Employee": report.employee.user.get_full_name()
                or report.employee.user.username,
                "Status": report.get_status_display(),
                "Fuel sales": fuel_sales,
                "Lubricant sales": lubricant_sales,
                "Total sales": fuel_sales + lubricant_sales,
                "Expected collections": reconciliation.get(
                    "expected_collections", Decimal("0.00")
                ),
                "Actual collections": reconciliation.get(
                    "actual_collections", Decimal("0.00")
                ),
                "Variance": reconciliation.get("variance", Decimal("0.00")),
                "Reconciliation": (
                    reconciliation["status"].label if reconciliation else "Not recorded"
                ),
            }
        )
    return rows


def _workforce_rows(stations, start, end):
    attendance = (
        Attendance.objects.filter(station__in=stations, work_date__range=(start, end))
        .values("station_id", "status")
        .annotate(records=Count("pk"), overtime=Sum("overtime_hours"))
    )
    attendance_by_station = {}
    for item in attendance:
        attendance_by_station.setdefault(item["station_id"], {})[item["status"]] = item

    payroll_period = (
        Q(year__gt=start.year)
        | Q(year=start.year, month__gte=start.month)
    ) & (
        Q(year__lt=end.year)
        | Q(year=end.year, month__lte=end.month)
    )
    payroll = (
        Payroll.objects.filter(station__in=stations)
        .filter(payroll_period)
        .values("station_id")
        .annotate(
            payroll_count=Count("pk"),
            gross=Sum("gross_pay"),
            net=Sum("net_pay"),
        )
    )
    payroll_by_station = {item["station_id"]: item for item in payroll}
    rows = []
    for station in stations:
        attendance_data = attendance_by_station.get(station.pk, {})
        payroll_data = payroll_by_station.get(station.pk, {})
        rows.append(
            {
                "Station": station.name,
                "Station code": station.code,
                "Present records": attendance_data.get("present", {}).get("records", 0),
                "Absent records": attendance_data.get("absent", {}).get("records", 0),
                "Leave records": attendance_data.get("leave", {}).get("records", 0),
                "Overtime hours": sum(
                    (
                        item["overtime"] or Decimal("0.00")
                        for item in attendance_data.values()
                    ),
                    Decimal("0.00"),
                ),
                "Payroll records": payroll_data.get("payroll_count", 0),
                "Gross payroll": _money(payroll_data.get("gross")),
                "Net payroll": _money(payroll_data.get("net")),
            }
        )
    return rows


def _report_data(kind, stations, start, end):
    if kind == "sales":
        return _sales_rows(stations, start, end)
    if kind == "inventory":
        return _inventory_rows(stations, start, end)
    if kind == "duty":
        return _duty_rows(stations, start, end)
    return _workforce_rows(stations, start, end)


def _export_response(kind, title, rows, export_format):
    columns = list(rows[0]) if rows else ["No data"]
    values = [list(row.values()) for row in rows] if rows else [["No records for this period."]]
    filename = f"multifuel-{kind}-report"

    if export_format == "csv":
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(
            [
                f"'{value}"
                if isinstance(value, str)
                and value.lstrip().startswith(("=", "+", "-", "@"))
                else value
                for value in row
            ]
            for row in values
        )
        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
        return response

    if export_format == "excel":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = title[:31]
        sheet.append(columns)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="173B4F")
        for row_index, row in enumerate(values, start=2):
            for column_index, value in enumerate(row, start=1):
                if isinstance(value, date):
                    value = value.isoformat()
                cell = sheet.cell(row=row_index, column=column_index, value=value)
                if isinstance(value, str):
                    cell.data_type = "s"
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cells in sheet.columns:
            width = max(len(str(cell.value or "")) for cell in cells)
            sheet.column_dimensions[cells[0].column_letter].width = min(width + 2, 40)
        output = BytesIO()
        workbook.save(output)
        response = HttpResponse(
            output.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
        return response

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
    )
    styles = getSampleStyleSheet()
    table_data = [
        [Paragraph(f"<b>{html.escape(str(value))}</b>", styles["BodyText"]) for value in columns]
    ]
    table_data.extend(
        [
            [
                Paragraph(html.escape(str(value)), styles["BodyText"])
                for value in row
            ]
            for row in values
        ]
    )
    page_width = landscape(A4)[0] - 24 * mm
    table = Table(
        table_data,
        colWidths=[page_width / len(columns)] * len(columns),
        repeatRows=1,
        splitByRow=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173B4F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5DC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F7F9")]),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    document.build(
        [Paragraph(html.escape(title), styles["Title"]), table]
    )
    response = HttpResponse(output.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return response


@require_GET
@role_required(User.Role.OWNER, User.Role.MANAGER)
def analytics(request):
    stations = _accessible_stations(request.user)
    station_id = request.GET.get("station", "")
    selected_station = None
    if station_id:
        selected_station = get_object_or_404(stations, pk=station_id)
        report_stations = stations.filter(pk=selected_station.pk)
    else:
        report_stations = stations

    start_date, end_date = _date_range(request)
    kind = request.GET.get("report", "sales")
    if kind not in REPORTS:
        kind = "sales"
    rows = _report_data(kind, report_stations, start_date, end_date)
    export_format = request.GET.get("format")
    if export_format:
        if export_format not in {"csv", "excel", "pdf"}:
            messages.error(request, "Choose CSV, Excel, or PDF for the export.")
        else:
            return _export_response(kind, REPORTS[kind], rows, export_format)
    return render(
        request,
        "reports/analytics.html",
        {
            "reports": REPORTS,
            "report_kind": kind,
            "report_title": REPORTS[kind],
            "stations": stations,
            "selected_station": selected_station,
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
            "columns": list(rows[0]) if rows else [],
            "export_formats": ("csv", "excel", "pdf"),
        },
    )
