import uuid
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User
from duties.models import DutyReport, PaymentCollection
from employees.models import Employee
from stations.models import Station

from .forms import (
    AdvanceForm,
    CreditCustomerForm,
    CreditRepaymentForm,
    CreditSaleForm,
    OTPTransactionForm,
)
from .models import (
    Advance,
    CreditCustomer,
    CreditRepayment,
    CreditSale,
    OTPTransaction,
)


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


def _accessible_reports(user):
    reports = DutyReport.objects.select_related("station", "shift", "employee__user")
    if user.role == User.Role.DSMS:
        return reports.filter(employee__user=user)
    return reports.filter(station__in=_accessible_stations(user))


def _editable_report(report):
    return report.status in (
        DutyReport.Status.DRAFT,
        DutyReport.Status.CORRECTION_REQUESTED,
    )


def _lock_selected_credit_customer(report, data):
    customer_id = data.get("customer")
    if customer_id:
        CreditCustomer.objects.select_for_update().filter(
            pk=customer_id,
            station=report.station,
        ).first()


def _existing_submission(model, token):
    try:
        parsed_token = uuid.UUID(str(token))
    except (AttributeError, TypeError, ValueError):
        return None
    return model.objects.filter(submission_token=parsed_token).first()


def _customer_context(customer):
    sales = list(
        customer.credit_sales.select_related(
            "report__station",
            "report__shift",
            "fuel_type",
        ).prefetch_related("repayments__received_by")
    )
    overdue_sales = [sale for sale in sales if sale.is_overdue]
    return {
        "customer": customer,
        "sales": sales,
        "overdue_sales": overdue_sales,
        "outstanding_balance": customer.outstanding_balance,
        "available_credit": customer.available_credit,
    }


@role_required(User.Role.OWNER, User.Role.MANAGER)
def customer_list(request, station_id):
    station = get_object_or_404(_accessible_stations(request.user), pk=station_id)
    customers = CreditCustomer.objects.filter(station=station)
    return render(
        request,
        "credits/customer_list.html",
        {
            "station": station,
            "customers": customers,
            "can_manage_customers": request.user.role == User.Role.OWNER,
        },
    )


@role_required(User.Role.OWNER)
def customer_create(request, station_id):
    station = get_object_or_404(
        Station.objects.filter(owner=request.user, is_active=True),
        pk=station_id,
    )
    if request.method == "POST":
        form = CreditCustomerForm(station, request.POST)
        if form.is_valid():
            customer = form.save()
            messages.success(request, f"{customer.name} was added as a credit customer.")
            return redirect("credits:customer-detail", customer_id=customer.pk)
    else:
        form = CreditCustomerForm(station)
    return render(
        request,
        "credits/customer_form.html",
        {
            "station": station,
            "form": form,
            "page_title": "Add credit customer",
            "submit_label": "Add customer",
        },
    )


@role_required(User.Role.OWNER)
def customer_update(request, customer_id):
    customer = get_object_or_404(
        CreditCustomer.objects.filter(station__owner=request.user),
        pk=customer_id,
    )
    if request.method == "POST":
        form = CreditCustomerForm(
            customer.station,
            request.POST,
            instance=customer,
        )
        if form.is_valid():
            customer = form.save()
            messages.success(request, f"{customer.name}'s profile was updated.")
            return redirect("credits:customer-detail", customer_id=customer.pk)
    else:
        form = CreditCustomerForm(customer.station, instance=customer)
    return render(
        request,
        "credits/customer_form.html",
        {
            "station": customer.station,
            "customer": customer,
            "form": form,
            "page_title": f"Edit {customer.name}",
            "submit_label": "Save changes",
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER)
def customer_detail(request, customer_id):
    customer = get_object_or_404(
        CreditCustomer.objects.filter(station__in=_accessible_stations(request.user)),
        pk=customer_id,
    )
    return render(
        request,
        "credits/customer_detail.html",
        _customer_context(customer),
    )


@role_required(User.Role.OWNER, User.Role.MANAGER)
def overdue_report(request, station_id):
    station = get_object_or_404(_accessible_stations(request.user), pk=station_id)
    sales = (
        CreditSale.objects.filter(
            report__station=station,
            due_date__lt=timezone.localdate(),
        )
        .annotate(
            paid_to_date=Sum(
                "repayments__amount",
                default=Decimal("0.00"),
            )
        )
        .annotate(balance_due=F("sale_amount") - F("paid_to_date"))
        .filter(balance_due__gt=0)
        .select_related("customer", "fuel_type", "report")
    )
    return render(
        request,
        "credits/overdue_report.html",
        {"station": station, "sales": sales, "today": timezone.localdate()},
    )


@role_required(User.Role.DSMS)
def report_credit_sale_create(request, report_id):
    if request.method == "POST":
        with transaction.atomic():
            report = get_object_or_404(
                DutyReport.objects.select_for_update().select_related(
                    "station",
                    "shift",
                    "employee__user",
                ),
                pk=report_id,
                employee__user=request.user,
            )
            if not _editable_report(report):
                raise PermissionDenied
            existing = _existing_submission(
                CreditSale,
                request.POST.get("submission_token"),
            )
            if existing:
                if existing.report_id != report.pk:
                    raise PermissionDenied
                messages.info(request, "This credit sale has already been recorded.")
                return redirect("duties:report-detail", report_id=report.pk)
            _lock_selected_credit_customer(report, request.POST)
            form = CreditSaleForm(report, request.user, request.POST)
            if form.is_valid():
                sale = form.save()
                messages.success(
                    request,
                    f"Credit sale of ₹{sale.sale_amount:.2f} was recorded for "
                    f"{sale.customer.name}.",
                )
                return redirect("duties:report-detail", report_id=report.pk)
    else:
        report = get_object_or_404(
            DutyReport.objects.select_related("station", "shift", "employee__user"),
            pk=report_id,
            employee__user=request.user,
        )
        if not _editable_report(report):
            raise PermissionDenied
        form = CreditSaleForm(report, request.user)
    return render(
        request,
        "credits/credit_sale_form.html",
        {"report": report, "form": form},
    )


@role_required(User.Role.DSMS)
def credit_sale_update(request, sale_id):
    if request.method == "POST":
        with transaction.atomic():
            sale = get_object_or_404(
                CreditSale.objects.select_for_update().select_related(
                    "report__station",
                    "report__shift",
                    "report__employee__user",
                ),
                pk=sale_id,
                report__employee__user=request.user,
            )
            report = get_object_or_404(
                DutyReport.objects.select_for_update().select_related(
                    "station",
                    "shift",
                    "employee__user",
                ),
                pk=sale.report_id,
            )
            sale.report = report
            if not _editable_report(sale.report):
                raise PermissionDenied
            _lock_selected_credit_customer(report, request.POST)
            form = CreditSaleForm(
                report,
                request.user,
                request.POST,
                instance=sale,
            )
            if form.is_valid():
                sale = form.save()
                messages.success(request, "Credit sale details were updated.")
                return redirect("duties:report-detail", report_id=sale.report_id)
    else:
        sale = get_object_or_404(
            CreditSale.objects.select_related(
                "report__station",
                "report__shift",
                "report__employee__user",
            ),
            pk=sale_id,
            report__employee__user=request.user,
        )
        if not _editable_report(sale.report):
            raise PermissionDenied
        form = CreditSaleForm(sale.report, request.user, instance=sale)
    return render(
        request,
        "credits/credit_sale_form.html",
        {"report": sale.report, "form": form, "sale": sale},
    )


@role_required(User.Role.OWNER, User.Role.MANAGER)
def credit_repayment_create(request, sale_id):
    accessible_sale = CreditSale.objects.filter(
        report__station__in=_accessible_stations(request.user)
    )
    if request.method == "POST":
        with transaction.atomic():
            sale = get_object_or_404(
                accessible_sale.select_for_update().select_related(
                    "customer",
                    "report__station",
                ),
                pk=sale_id,
            )
            existing = _existing_submission(
                CreditRepayment,
                request.POST.get("submission_token"),
            )
            if existing:
                if existing.sale_id != sale.pk:
                    raise PermissionDenied
                messages.info(request, "This repayment has already been recorded.")
                return redirect(
                    "credits:customer-detail",
                    customer_id=sale.customer_id,
                )
            if sale.outstanding_amount <= 0:
                messages.info(request, "This credit sale is already fully repaid.")
                return redirect(
                    "credits:customer-detail",
                    customer_id=sale.customer_id,
                )
            form = CreditRepaymentForm(sale, request.user, request.POST)
            if form.is_valid():
                repayment = form.save()
                messages.success(
                    request,
                    f"Repayment of ₹{repayment.amount:.2f} was recorded.",
                )
                return redirect(
                    "credits:customer-detail",
                    customer_id=sale.customer_id,
                )
    else:
        sale = get_object_or_404(
            accessible_sale.select_related("customer", "report__station"),
            pk=sale_id,
        )
        if sale.outstanding_amount <= 0:
            messages.info(request, "This credit sale is already fully repaid.")
            return redirect(
                "credits:customer-detail",
                customer_id=sale.customer_id,
            )
        form = CreditRepaymentForm(sale, request.user)
    return render(
        request,
        "credits/repayment_form.html",
        {"sale": sale, "form": form},
    )


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def report_advance_list(request, report_id):
    report = get_object_or_404(_accessible_reports(request.user), pk=report_id)
    can_edit = request.user.role == User.Role.DSMS and _editable_report(report)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        with transaction.atomic():
            report = get_object_or_404(
                DutyReport.objects.select_for_update(),
                pk=report_id,
                employee__user=request.user,
            )
            form = AdvanceForm(report, request.user, request.POST)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        form.save()
                except IntegrityError:
                    form.add_error(
                        "reference",
                        "This advance reference is already recorded on the report.",
                    )
                else:
                    messages.success(request, "Advance settlement was recorded.")
                    return redirect("credits:report-advance-list", report_id=report.pk)
    else:
        form = AdvanceForm(report, request.user) if can_edit else None
    return render(
        request,
        "credits/advance_list.html",
        {
            "report": report,
            "advances": report.advances.all(),
            "form": form,
            "can_edit": can_edit,
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def report_otp_list(request, report_id):
    report = get_object_or_404(_accessible_reports(request.user), pk=report_id)
    can_edit = request.user.role == User.Role.DSMS and _editable_report(report)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        with transaction.atomic():
            report = get_object_or_404(
                DutyReport.objects.select_for_update(),
                pk=report_id,
                employee__user=request.user,
            )
            form = OTPTransactionForm(report, request.user, request.POST)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        form.save()
                except IntegrityError:
                    form.add_error(
                        "transaction_reference",
                        "This transaction reference is already recorded on the report.",
                    )
                else:
                    messages.success(request, "OTP transaction was recorded.")
                    return redirect("credits:report-otp-list", report_id=report.pk)
    else:
        form = OTPTransactionForm(report, request.user) if can_edit else None
    return render(
        request,
        "credits/otp_list.html",
        {
            "report": report,
            "transactions": report.otp_transactions.all(),
            "form": form,
            "can_edit": can_edit,
        },
    )
