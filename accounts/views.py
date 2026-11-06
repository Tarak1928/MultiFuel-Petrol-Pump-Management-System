from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import (
    LoginView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST

from duties.models import DutyReport
from .decorators import role_required
from .forms import (
    ApprovedPasswordResetForm,
    MultiFuelAuthenticationForm,
    RegistrationForm,
)
from employees.models import Employee
from stations.models import Station
from .models import User


ROLE_DASHBOARDS = {
    User.Role.OWNER: "dashboard-owner",
    User.Role.MANAGER: "dashboard-manager",
    User.Role.DSMS: "dashboard-dsms",
}


def _dashboard_name_for(user):
    try:
        return ROLE_DASHBOARDS[user.role]
    except KeyError as exc:
        raise PermissionDenied("No dashboard is configured for this account role.") from exc


class MultiFuelLoginView(LoginView):
    template_name = "accounts/login.html"
    authentication_form = MultiFuelAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        redirect_to = self.get_redirect_url()
        if redirect_to:
            return redirect_to
        return reverse(_dashboard_name_for(self.request.user))


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Your registration was submitted. An Owner must approve it before you can sign in.",
            )
            return redirect("login")
    else:
        form = RegistrationForm()
    return render(request, "accounts/register.html", {"form": form})


def dashboard_redirect(request):
    if not request.user.is_authenticated:
        return redirect("login")
    employee = getattr(request.user, "employee_profile", None)
    if (
        not request.user.is_active
        or not request.user.is_approved
        or (employee and not employee.is_active)
    ):
        logout(request)
        messages.error(
            request,
            "Your account or employee profile is inactive or not approved.",
        )
        return redirect("login")

    return redirect(_dashboard_name_for(request.user))


@role_required(User.Role.OWNER)
def owner_dashboard(request):
    pending_accounts = User.objects.filter(
        is_approved=False,
        requested_owner=request.user,
        role__in=(User.Role.MANAGER, User.Role.DSMS),
    )
    stations = Station.objects.filter(owner=request.user)
    employee_count = Employee.objects.filter(owner=request.user, is_active=True).count()
    pending_report_count = DutyReport.objects.filter(
        station__owner=request.user,
        status__in=(
            DutyReport.Status.SUBMITTED,
            DutyReport.Status.PENDING_VERIFICATION,
            DutyReport.Status.RESUBMITTED,
        ),
    ).count()
    return render(
        request,
        "accounts/dashboard_owner.html",
        {
            "pending_accounts": pending_accounts,
            "station_count": stations.count(),
            "active_station_count": stations.filter(is_active=True).count(),
            "employee_count": employee_count,
            "pending_report_count": pending_report_count,
        },
    )


@role_required(User.Role.MANAGER)
def manager_dashboard(request):
    employee = (
        Employee.objects.filter(user=request.user, is_active=True)
        .select_related("user")
        .first()
    )
    manager_stations = (
        employee.manager_assignments.filter(station__is_active=True)
        .select_related("station")
        if employee
        else []
    )
    station_ids = (
        manager_stations.values_list("station_id", flat=True)
        if employee
        else []
    )
    pending_report_count = DutyReport.objects.filter(
        station_id__in=station_ids,
        status__in=(
            DutyReport.Status.SUBMITTED,
            DutyReport.Status.PENDING_VERIFICATION,
            DutyReport.Status.RESUBMITTED,
        ),
    ).count()
    return render(
        request,
        "accounts/dashboard_manager.html",
        {
            "employee": employee,
            "manager_stations": manager_stations,
            "pending_report_count": pending_report_count,
        },
    )


@role_required(User.Role.DSMS)
def dsms_dashboard(request):
    employee = (
        Employee.objects.filter(user=request.user, is_active=True)
        .select_related("assigned_station")
        .first()
    )
    return render(
        request,
        "accounts/dashboard_dsms.html",
        {"employee": employee},
    )


@role_required(User.Role.OWNER)
@require_POST
def approve_account(request, user_id):
    with transaction.atomic():
        account = get_object_or_404(
            User.objects.select_for_update(),
            pk=user_id,
            is_approved=False,
            requested_owner=request.user,
            role__in=(User.Role.MANAGER, User.Role.DSMS),
        )
        account.is_approved = True
        account.approved_by = request.user
        account.save(update_fields=("is_approved", "approved_by"))

    messages.success(request, f"Account {account.username} has been approved.")
    return redirect("dashboard-owner")


class MultiFuelPasswordResetView(PasswordResetView):
    template_name = "accounts/password_reset_form.html"
    form_class = ApprovedPasswordResetForm
    email_template_name = "accounts/password_reset_email.html"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("password-reset-done")


class MultiFuelPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class MultiFuelPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("password-reset-complete")


class MultiFuelPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"
