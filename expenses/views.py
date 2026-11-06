from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User
from duties.models import DutyFuelEntry, DutyLubricantEntry, DutyReport
from employees.models import Employee
from stations.models import Station

from .forms import ExpenseDecisionForm, ExpenseForm
from .models import Expense


def _accessible_stations(user):
    if user.role == User.Role.OWNER:
        return Station.objects.filter(owner=user)
    if user.role == User.Role.MANAGER:
        return Station.objects.filter(
            manager_assignments__employee__user=user,
            manager_assignments__employee__is_active=True,
            is_active=True,
        ).distinct()
    employee = Employee.objects.filter(user=user, is_active=True).first()
    if not employee or not employee.assigned_station_id:
        return Station.objects.none()
    return Station.objects.filter(pk=employee.assigned_station_id, is_active=True)


def _parse_date_range(request):
    today = timezone.localdate()
    default_start = today.replace(day=1)
    start_text = request.GET.get("start_date", "")
    end_text = request.GET.get("end_date", "")
    try:
        start = date.fromisoformat(start_text) if start_text else default_start
        end = date.fromisoformat(end_text) if end_text else today
    except ValueError:
        messages.error(request, "Enter dates in YYYY-MM-DD format.")
        return default_start, today
    if end < start:
        messages.error(request, "The end date must be on or after the start date.")
        return default_start, today
    return start, end


def _amount(queryset):
    return queryset.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def _sales_amount(queryset):
    return queryset.aggregate(total=Sum("sale_amount"))["total"] or Decimal("0.00")


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def expense_list(request):
    stations = _accessible_stations(request.user)
    expenses = (
        Expense.objects.filter(station__in=stations)
        .select_related("station", "recorded_by", "decided_by", "payroll")
        .order_by("-expense_date", "-pk")
    )
    station_id = request.GET.get("station", "")
    selected_station = None
    if station_id:
        selected_station = get_object_or_404(stations, pk=station_id)
        expenses = expenses.filter(station=selected_station)
    start_date, end_date = _parse_date_range(request)
    expenses = expenses.filter(expense_date__range=(start_date, end_date))
    return render(
        request,
        "expenses/expense_list.html",
        {
            "expenses": expenses[:300],
            "stations": stations,
            "selected_station": selected_station,
            "start_date": start_date,
            "end_date": end_date,
            "can_record": request.user.role
            in (User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS),
            "can_review": request.user.role == User.Role.OWNER,
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def expense_create(request):
    if request.method == "POST":
        form = ExpenseForm(request.user, request.POST)
        if form.is_valid():
            expense = form.save(commit=False)
            expense.recorded_by = request.user
            if request.user.role == User.Role.OWNER:
                expense.status = Expense.Status.APPROVED
                expense.decided_by = request.user
                expense.decided_at = timezone.now()
                expense.decision_note = "Recorded and approved by the station Owner."
            else:
                expense.status = Expense.Status.PENDING
            try:
                with transaction.atomic():
                    expense.save()
            except IntegrityError:
                form.add_error(
                    "reference",
                    "This reference is already used for an expense at the station.",
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                if expense.status == Expense.Status.PENDING:
                    messages.success(request, "Expense was sent to the Owner for approval.")
                else:
                    messages.success(request, "Expense was recorded and approved.")
                return redirect("expenses:expense-list")
    else:
        form = ExpenseForm(request.user)
    return render(
        request,
        "expenses/expense_form.html",
        {
            "form": form,
            "page_title": "Record an expense",
            "submit_label": "Save expense",
        },
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def expense_decide(request, expense_id):
    expense = get_object_or_404(
        Expense.objects.select_for_update().filter(
            station__owner=request.user,
            status=Expense.Status.PENDING,
        ),
        pk=expense_id,
    )
    form = ExpenseDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(
            request,
            "Expense decision was not saved: "
            + "; ".join(
                error
                for errors in form.errors.values()
                for error in errors
            ),
        )
        return redirect("expenses:expense-list")

    expense.status = (
        Expense.Status.APPROVED
        if form.cleaned_data["action"] == "approve"
        else Expense.Status.REJECTED
    )
    expense.decided_by = request.user
    expense.decided_at = timezone.now()
    expense.decision_note = form.cleaned_data["decision_note"].strip()
    try:
        expense.save()
    except ValidationError as exc:
        messages.error(request, f"Expense decision was not saved: {exc}")
    else:
        messages.success(request, "Expense decision was recorded.")
    return redirect("expenses:expense-list")


@role_required(User.Role.OWNER, User.Role.MANAGER)
def accounting_report(request):
    accessible_stations = _accessible_stations(request.user)
    stations = accessible_stations.order_by("name")
    station_id = request.GET.get("station", "")
    selected_station = None
    if station_id:
        selected_station = get_object_or_404(accessible_stations, pk=station_id)
        report_stations = accessible_stations.filter(pk=selected_station.pk)
    else:
        report_stations = accessible_stations

    start_date, end_date = _parse_date_range(request)
    approved_reports = DutyReport.objects.filter(
        station__in=report_stations,
        status=DutyReport.Status.APPROVED,
        report_date__range=(start_date, end_date),
    )
    approved_expenses = Expense.objects.filter(
        station__in=report_stations,
        status=Expense.Status.APPROVED,
        expense_date__range=(start_date, end_date),
    )
    fuel_sales = _sales_amount(
        DutyFuelEntry.objects.filter(report__in=approved_reports)
    )
    lubricant_sales = _sales_amount(
        DutyLubricantEntry.objects.filter(report__in=approved_reports)
    )
    total_sales = fuel_sales + lubricant_sales
    total_expenses = _amount(approved_expenses)
    categories = {
        row["category"]: row["total"]
        for row in approved_expenses.values("category")
        .annotate(total=Sum("amount"))
    }
    category_totals = [
        {
            "label": label,
            "total": categories.get(value, Decimal("0.00")),
        }
        for value, label in Expense.Category.choices
    ]

    station_summaries = []
    for station in report_stations:
        station_reports = approved_reports.filter(station=station)
        station_expenses = approved_expenses.filter(station=station)
        station_fuel_sales = _sales_amount(
            DutyFuelEntry.objects.filter(report__in=station_reports)
        )
        station_lubricant_sales = _sales_amount(
            DutyLubricantEntry.objects.filter(report__in=station_reports)
        )
        station_expense_total = _amount(station_expenses)
        station_summaries.append(
            {
                "station": station,
                "fuel_sales": station_fuel_sales,
                "lubricant_sales": station_lubricant_sales,
                "sales": station_fuel_sales + station_lubricant_sales,
                "expenses": station_expense_total,
                "net_after_expenses": (
                    station_fuel_sales
                    + station_lubricant_sales
                    - station_expense_total
                ),
            }
        )

    return render(
        request,
        "expenses/accounting_report.html",
        {
            "stations": stations,
            "selected_station": selected_station,
            "start_date": start_date,
            "end_date": end_date,
            "fuel_sales": fuel_sales,
            "lubricant_sales": lubricant_sales,
            "total_sales": total_sales,
            "total_expenses": total_expenses,
            "net_after_expenses": total_sales - total_expenses,
            "category_totals": category_totals,
            "station_summaries": station_summaries,
        },
    )
