from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User
from stations.models import Station

from .forms import (
    AttendanceForm,
    EmployeeAdvanceForm,
    EmployeeForm,
    LeaveRequestForm,
    PayrollForm,
)
from .models import (
    Attendance,
    Employee,
    EmployeeAdvance,
    LeaveRequest,
    Payroll,
    PayrollAdvanceRepayment,
)


def _owner_employees(user):
    return Employee.objects.filter(owner=user)


def _accessible_employees(user):
    if user.role == User.Role.OWNER:
        return _owner_employees(user)
    if user.role == User.Role.MANAGER:
        manager = Employee.objects.filter(user=user, is_active=True).first()
        if not manager:
            return Employee.objects.none()
        station_ids = manager.manager_assignments.filter(
            station__is_active=True
        ).values_list("station_id", flat=True)
        return (
            Employee.objects.filter(owner=manager.owner, is_active=True)
            .filter(
                Q(assigned_station_id__in=station_ids)
                | Q(manager_assignments__station_id__in=station_ids)
            )
            .distinct()
        )
    return Employee.objects.filter(user=user)


@role_required(User.Role.OWNER)
def employee_list(request):
    employees = (
        _owner_employees(request.user)
        .select_related("user", "assigned_station")
        .prefetch_related("manager_assignments__station")
    )
    return render(request, "employees/employee_list.html", {"employees": employees})


@role_required(User.Role.OWNER)
def employee_create(request):
    if request.method == "POST":
        form = EmployeeForm(request.user, request.POST)
        if form.is_valid():
            employee = form.save()
            messages.success(request, f"{employee.user} was added to your staff.")
            return redirect("employees:employee-list")
    else:
        form = EmployeeForm(request.user)
    return render(
        request,
        "employees/employee_form.html",
        {"form": form, "page_title": "Add an employee", "submit_label": "Add employee"},
    )


@role_required(User.Role.OWNER)
def employee_update(request, employee_id):
    employee = get_object_or_404(_owner_employees(request.user), pk=employee_id)
    if request.method == "POST":
        form = EmployeeForm(request.user, request.POST, instance=employee)
        if form.is_valid():
            form.save()
            messages.success(request, f"{employee.user}’s profile was updated.")
            return redirect("employees:employee-list")
    else:
        form = EmployeeForm(request.user, instance=employee)
    return render(
        request,
        "employees/employee_form.html",
        {
            "form": form,
            "employee": employee,
            "page_title": f"Edit {employee.user.get_full_name() or employee.user.username}",
            "submit_label": "Save changes",
        },
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def employee_toggle_status(request, employee_id):
    employee = get_object_or_404(
        _owner_employees(request.user).select_for_update(),
        pk=employee_id,
    )
    employee.is_active = not employee.is_active
    employee.save(update_fields=("is_active", "updated_at"))
    status = "activated" if employee.is_active else "deactivated"
    messages.success(request, f"{employee.user} was {status}.")
    return redirect("employees:employee-list")


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def attendance_list(request, station_id=None):
    employees = _accessible_employees(request.user)
    records = (
        Attendance.objects.filter(employee__in=employees)
        .select_related("employee__user", "station", "recorded_by")
        .order_by("-work_date", "employee__employee_code")
    )
    if request.user.role == User.Role.MANAGER:
        records = records.filter(
            station__in=Station.objects.filter(
                manager_assignments__employee__user=request.user,
                manager_assignments__employee__is_active=True,
                is_active=True,
            )
        )
    selected_station = None
    if station_id is not None:
        if request.user.role == User.Role.OWNER:
            stations = Station.objects.filter(owner=request.user)
        elif request.user.role == User.Role.MANAGER:
            stations = Station.objects.filter(
                manager_assignments__employee__user=request.user,
                manager_assignments__employee__is_active=True,
                is_active=True,
            )
        else:
            employee = employees.first()
            stations = (
                Station.objects.filter(pk=employee.assigned_station_id)
                if employee and employee.assigned_station_id
                else Station.objects.none()
            )
        selected_station = get_object_or_404(stations.distinct(), pk=station_id)
        records = records.filter(station=selected_station)

    month_value = request.GET.get("month", "")
    if month_value:
        try:
            year, month = (int(value) for value in month_value.split("-", maxsplit=1))
            if not 1 <= month <= 12:
                raise ValueError
        except ValueError:
            messages.error(request, "Choose a month in YYYY-MM format.")
        else:
            records = records.filter(work_date__year=year, work_date__month=month)

    can_manage = request.user.role in (User.Role.OWNER, User.Role.MANAGER)
    return render(
        request,
        "employees/attendance_list.html",
        {
            "records": records[:300],
            "selected_station": selected_station,
            "can_manage": can_manage,
            "month_filter": month_value,
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER)
def attendance_create(request):
    if request.method == "POST":
        form = AttendanceForm(request.user, request.POST)
        if form.is_valid():
            attendance = form.save(commit=False)
            attendance.recorded_by = request.user
            try:
                with transaction.atomic():
                    Employee.objects.select_for_update().get(pk=attendance.employee_id)
                    attendance.save()
            except IntegrityError:
                form.add_error(
                    "work_date",
                    "Attendance has already been recorded for this employee and date.",
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Attendance was recorded.")
                return redirect("employees:attendance-list")
    else:
        form = AttendanceForm(request.user)
    return render(
        request,
        "employees/workforce_form.html",
        {
            "form": form,
            "page_title": "Record attendance",
            "submit_label": "Save attendance",
            "cancel_url": "employees:attendance-list",
            "help_text": (
                "Only present staff can have clock times and overtime. "
                "Payroll locks attendance for the completed pay period."
            ),
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER)
def attendance_update(request, attendance_id):
    attendance_scope = Attendance.objects.filter(
        employee__in=_accessible_employees(request.user)
    )
    if request.user.role == User.Role.MANAGER:
        attendance_scope = attendance_scope.filter(
            station__in=Station.objects.filter(
                manager_assignments__employee__user=request.user,
                manager_assignments__employee__is_active=True,
                is_active=True,
            )
        )
    attendance = get_object_or_404(
        attendance_scope,
        pk=attendance_id,
    )
    if request.method == "POST":
        form = AttendanceForm(request.user, request.POST, instance=attendance)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.recorded_by = request.user
            try:
                with transaction.atomic():
                    Employee.objects.select_for_update().get(pk=updated.employee_id)
                    updated.save()
            except (IntegrityError, ValidationError) as exc:
                if isinstance(exc, IntegrityError):
                    form.add_error(
                        "work_date",
                        "Attendance already exists for this employee and date.",
                    )
                else:
                    form.add_error(None, exc)
            else:
                messages.success(request, "Attendance was updated.")
                return redirect("employees:attendance-list")
    else:
        form = AttendanceForm(request.user, instance=attendance)
    return render(
        request,
        "employees/workforce_form.html",
        {
            "form": form,
            "page_title": f"Edit attendance for {attendance.employee}",
            "submit_label": "Save attendance",
            "cancel_url": "employees:attendance-list",
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def leave_list(request):
    leave_requests = (
        LeaveRequest.objects.filter(employee__in=_accessible_employees(request.user))
        .select_related("employee__user", "reviewed_by")
        .order_by("-start_date", "-pk")
    )
    return render(
        request,
        "employees/leave_list.html",
        {
            "leave_requests": leave_requests,
            "can_request": request.user.role in (User.Role.MANAGER, User.Role.DSMS),
            "can_review": request.user.role in (User.Role.OWNER, User.Role.MANAGER),
        },
    )


@role_required(User.Role.MANAGER, User.Role.DSMS)
def leave_request_create(request):
    employee = get_object_or_404(
        Employee.objects.select_related("user"),
        user=request.user,
        is_active=True,
    )
    if request.method == "POST":
        form = LeaveRequestForm(request.POST)
        if form.is_valid():
            leave_request = form.save(commit=False)
            leave_request.employee = employee
            leave_request.requested_by = request.user
            try:
                leave_request.save()
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Your leave request was submitted for review.")
                return redirect("employees:leave-list")
    else:
        form = LeaveRequestForm()
    return render(
        request,
        "employees/workforce_form.html",
        {
            "form": form,
            "page_title": "Request leave",
            "submit_label": "Submit request",
            "cancel_url": "employees:leave-list",
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER)
@require_POST
@transaction.atomic
def leave_request_decide(request, leave_id):
    leave_request = get_object_or_404(
        LeaveRequest.objects.select_for_update().filter(
            employee__in=_accessible_employees(request.user),
            status=LeaveRequest.Status.PENDING,
        ),
        pk=leave_id,
    )
    action = request.POST.get("action")
    note = request.POST.get("review_note", "").strip()
    if action not in ("approve", "reject") or not note:
        messages.error(request, "Choose a decision and provide a review note.")
        return redirect("employees:leave-list")
    if action == "approve" and Attendance.objects.filter(
        employee=leave_request.employee,
        status=Attendance.Status.PRESENT,
        work_date__range=(leave_request.start_date, leave_request.end_date),
    ).exists():
        messages.error(
            request,
            "Leave cannot be approved while present attendance exists in that date range.",
        )
        return redirect("employees:leave-list")

    leave_request.status = (
        LeaveRequest.Status.APPROVED
        if action == "approve"
        else LeaveRequest.Status.REJECTED
    )
    leave_request.reviewed_by = request.user
    leave_request.reviewed_at = timezone.now()
    leave_request.review_note = note
    try:
        leave_request.save()
    except ValidationError as exc:
        messages.error(request, f"Leave decision was not saved: {exc}")
    else:
        messages.success(request, "Leave request decision was recorded.")
    return redirect("employees:leave-list")


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def employee_advance_list(request):
    advances = EmployeeAdvance.objects.select_related("employee__user", "owner")
    if request.user.role == User.Role.OWNER:
        advances = advances.filter(owner=request.user)
    else:
        advances = advances.filter(employee__user=request.user)
    advances = list(advances.annotate(repaid_total=Sum("repayments__amount")))
    for advance in advances:
        advance.repaid_balance = advance.repaid_total or Decimal("0.00")
        advance.outstanding_balance = advance.amount - advance.repaid_balance
    return render(
        request,
        "employees/advance_list.html",
        {
            "advances": advances,
            "can_issue": request.user.role == User.Role.OWNER,
        },
    )


@role_required(User.Role.OWNER)
def employee_advance_create(request):
    if request.method == "POST":
        form = EmployeeAdvanceForm(request.user, request.POST)
        if form.is_valid():
            advance = form.save(commit=False)
            advance.owner = request.user
            advance.issued_by = request.user
            try:
                advance.save()
            except IntegrityError:
                form.add_error(
                    "reference",
                    "This advance reference is already used by your business.",
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Employee advance was recorded.")
                return redirect("employees:advance-list")
    else:
        form = EmployeeAdvanceForm(request.user)
    return render(
        request,
        "employees/workforce_form.html",
        {
            "form": form,
            "page_title": "Issue employee advance",
            "submit_label": "Record advance",
            "cancel_url": "employees:advance-list",
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def payroll_list(request):
    payroll_records = Payroll.objects.select_related("employee__user")
    if request.user.role == User.Role.OWNER:
        payroll_records = payroll_records.filter(employee__owner=request.user)
    else:
        payroll_records = payroll_records.filter(employee__user=request.user)
    return render(
        request,
        "employees/payroll_list.html",
        {
            "payroll_records": payroll_records,
            "can_generate": request.user.role == User.Role.OWNER,
        },
    )


@role_required(User.Role.OWNER)
def payroll_create(request):
    if request.method == "POST":
        form = PayrollForm(request.user, request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    employee = Employee.objects.select_for_update().get(
                        pk=form.cleaned_data["employee"].pk,
                        owner=request.user,
                    )
                    attendance_hours = sum(
                        Attendance.objects.select_for_update()
                        .filter(
                            employee=employee,
                            work_date__year=form.cleaned_data["year"],
                            work_date__month=form.cleaned_data["month"],
                        )
                        .values_list("overtime_hours", flat=True),
                        start=Decimal("0.00"),
                    )
                    payroll = form.save(commit=False)
                    payroll.employee = employee
                    payroll.basic_salary = employee.basic_salary
                    payroll.overtime_hours = attendance_hours
                    payroll.created_by = request.user
                    payroll.save()

                    remaining = payroll.advance_deduction
                    advances = EmployeeAdvance.objects.select_for_update().filter(
                        employee=employee
                    ).order_by("issued_date", "pk")
                    for advance in advances:
                        repayment = min(remaining, advance.outstanding_amount)
                        if repayment > 0:
                            PayrollAdvanceRepayment.objects.create(
                                payroll=payroll,
                                advance=advance,
                                amount=repayment,
                            )
                            remaining -= repayment
                        if remaining == 0:
                            break
                    if remaining:
                        raise ValidationError(
                            "Outstanding advances changed. Review the deduction amount and try again."
                        )
                    recorded_repayments = payroll.advance_repayments.aggregate(
                        total=Sum("amount")
                    )["total"] or Decimal("0.00")
                    if recorded_repayments != payroll.advance_deduction:
                        raise ValidationError(
                            "Advance deductions could not be fully allocated."
                        )
            except IntegrityError:
                form.add_error(
                    "month",
                    "Payroll already exists for this employee and month.",
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Monthly payroll was generated.")
                return redirect("employees:payslip", payroll_id=payroll.pk)
    else:
        form = PayrollForm(request.user)
    return render(
        request,
        "employees/workforce_form.html",
        {
            "form": form,
            "page_title": "Generate monthly payroll",
            "submit_label": "Generate payslip",
            "cancel_url": "employees:payroll-list",
            "help_text": (
                "Overtime hours are taken from attendance for the selected month. "
                "Advance deductions are allocated to the oldest outstanding advances. "
                "Generated payroll locks attendance for that month."
            ),
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def payslip(request, payroll_id):
    payroll = get_object_or_404(
        Payroll.objects.select_related("employee__user", "employee__owner")
        .prefetch_related("advance_repayments__advance"),
        pk=payroll_id,
    )
    if request.user.role == User.Role.OWNER:
        if payroll.employee.owner_id != request.user.pk:
            raise Http404
    elif payroll.employee.user_id != request.user.pk:
        raise Http404
    return render(request, "employees/payslip.html", {"payroll": payroll})


@role_required(User.Role.OWNER)
@require_POST
def payroll_mark_paid(request, payroll_id):
    try:
        with transaction.atomic():
            payroll = get_object_or_404(
                Payroll.objects.select_for_update().filter(
                    employee__owner=request.user,
                    station__isnull=False,
                ),
                pk=payroll_id,
                status=Payroll.Status.UNPAID,
            )
            payroll.status = Payroll.Status.PAID
            payroll.paid_at = timezone.now()
            payroll.save(update_fields=("status", "paid_at"))

            from expenses.models import Expense

            Expense.objects.create(
                station=payroll.station,
                category=Expense.Category.SALARY,
                amount=payroll.gross_pay,
                expense_date=date(
                    payroll.year,
                    payroll.month,
                    monthrange(payroll.year, payroll.month)[1],
                ),
                reference=(
                    f"PAY-{payroll.year}-{payroll.month:02d}-"
                    f"{payroll.employee.employee_code}"
                ),
                description=(
                    f"Gross payroll expense for {payroll.employee.employee_code}, "
                    f"{payroll.year}-{payroll.month:02d}."
                ),
                status=Expense.Status.APPROVED,
                recorded_by=payroll.created_by,
                decided_by=payroll.created_by,
                decided_at=timezone.now(),
                decision_note="Automatically posted when payroll was marked paid.",
                payroll=payroll,
            )
    except ValidationError as exc:
        messages.error(request, f"Payroll was not marked paid: {exc}")
        return redirect("employees:payslip", payroll_id=payroll_id)
    except IntegrityError:
        messages.error(
            request,
            "Payroll could not be marked paid because its salary expense already exists.",
        )
        return redirect("employees:payslip", payroll_id=payroll_id)
    else:
        messages.success(request, "Payroll was marked paid and posted to station expenses.")
    return redirect("employees:payslip", payroll_id=payroll.pk)
