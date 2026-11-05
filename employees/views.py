from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User

from .forms import EmployeeForm
from .models import Employee


def _owner_employees(user):
    return Employee.objects.filter(owner=user)


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
