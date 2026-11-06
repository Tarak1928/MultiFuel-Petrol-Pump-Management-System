from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import User
from employees.models import Employee
from fuel.models import InventoryLedger, Nozzle, Tank
from stations.models import Station

from .forms import (
    DutyReportCorrectionForm,
    DutyFuelEntryFormSet,
    DutyLubricantEntryFormSet,
    DutyReportCreateForm,
    DutyReportForm,
    LubricantForm,
    PaymentCollectionForm,
    ReconciliationAdjustmentForm,
    ReconciliationSettingsForm,
    ShiftForm,
)
from .models import (
    DutyReport,
    DutyReportReview,
    Lubricant,
    PaymentCollection,
    ReconciliationAdjustment,
    ReconciliationSettings,
    Shift,
)


def _accessible_stations(user):
    if user.role == User.Role.OWNER:
        return Station.objects.filter(owner=user)

    employee = Employee.objects.filter(user=user, is_active=True).first()
    if not employee:
        return Station.objects.none()
    if user.role == User.Role.MANAGER:
        station_ids = employee.manager_assignments.filter(
            station__is_active=True
        ).values_list("station_id", flat=True)
        return Station.objects.filter(pk__in=station_ids, is_active=True)
    if user.role == User.Role.DSMS and employee.assigned_station_id:
        return Station.objects.filter(
            pk=employee.assigned_station_id,
            is_active=True,
        )
    return Station.objects.none()


def _accessible_reports(user):
    reports = DutyReport.objects.select_related(
        "station",
        "employee__user",
        "shift",
    )
    if user.role == User.Role.OWNER:
        return reports.filter(station__owner=user)
    if user.role == User.Role.MANAGER:
        station_ids = _accessible_stations(user).values_list("pk", flat=True)
        return reports.filter(station_id__in=station_ids)
    return reports.filter(employee__user=user)


def _record_review(report, actor, action, from_status, *, notes="", stock_snapshot=None):
    return DutyReportReview.objects.create(
        report=report,
        actor=actor,
        action=action,
        from_status=from_status,
        to_status=report.status,
        notes=notes,
        stock_snapshot=stock_snapshot or {},
    )


def _reviewable_report(request, report_id, *, lock=False):
    reports = _accessible_reports(request.user)
    if lock:
        reports = reports.select_for_update()
    return get_object_or_404(reports, pk=report_id)


def _post_fuel_inventory(report, actor):
    quantities_by_tank = {}
    entries = report.fuel_entries.select_related("nozzle__tank").order_by(
        "nozzle__tank_id",
        "pk",
    )
    for entry in entries:
        tank_id = entry.nozzle.tank_id
        quantities = quantities_by_tank.setdefault(
            tank_id,
            {
                "sale": Decimal("0.000"),
                "testing_dispensed": Decimal("0.000"),
                "testing_returned": Decimal("0.000"),
            },
        )
        quantities["sale"] += entry.actual_sale_litres
        quantities["testing_dispensed"] += entry.testing_litres
        quantities["testing_returned"] += entry.testing_returned_litres

    tanks = {
        tank.pk: tank
        for tank in Tank.objects.select_for_update()
        .filter(pk__in=quantities_by_tank)
        .order_by("pk")
    }
    shortages = [
        f"{tanks[tank_id].code} (available "
        f"{tanks[tank_id].current_stock_litres} L, required "
        f"{quantities['sale'] + quantities['testing_dispensed']} L)"
        for tank_id, quantities in quantities_by_tank.items()
        if tanks[tank_id].current_stock_litres
        < quantities["sale"] + quantities["testing_dispensed"]
    ]
    if shortages:
        return shortages

    for tank_id, quantities in quantities_by_tank.items():
        tank = tanks[tank_id]
        tank.current_stock_litres += (
            -quantities["sale"]
            - quantities["testing_dispensed"]
            + quantities["testing_returned"]
        )
        tank.save(update_fields=("current_stock_litres", "updated_at"))
        for key, transaction_type, quantity_delta, note in (
            (
                "sale",
                InventoryLedger.TransactionType.SALE,
                -quantities["sale"],
                "Fuel dispensed from approved duty report.",
            ),
            (
                "testing_dispensed",
                InventoryLedger.TransactionType.TESTING_USE,
                -quantities["testing_dispensed"],
                "Fuel withdrawn for testing.",
            ),
            (
                "testing_returned",
                InventoryLedger.TransactionType.TESTING_RETURN,
                quantities["testing_returned"],
                "Testing fuel returned to tank.",
            ),
        ):
            if quantities[key]:
                InventoryLedger.objects.create(
                    tank=tank,
                    transaction_type=transaction_type,
                    quantity_delta=quantity_delta,
                    report=report,
                    recorded_by=actor,
                    notes=note,
                )
    return []


def _duplicate_submission_response(request, employee, token):
    report = DutyReport.objects.filter(submission_token=token).first()
    if not report:
        return None
    if report.employee_id != employee.pk:
        raise PermissionDenied
    messages.info(request, "This duty report has already been created.")
    return redirect("duties:report-detail", report_id=report.pk)


def _get_station(user, station_id):
    return get_object_or_404(_accessible_stations(user), pk=station_id)


def _render_shift_form(request, station, form, *, title, submit_label):
    return render(
        request,
        "duties/shift_form.html",
        {
            "station": station,
            "form": form,
            "page_title": title,
            "submit_label": submit_label,
        },
    )


def _save_shift_form(form):
    try:
        with transaction.atomic():
            return form.save()
    except (IntegrityError, ValidationError):
        shifts = Shift.objects.filter(
            station=form.station,
            name__iexact=form.cleaned_data["name"],
        )
        if form.instance.pk:
            shifts = shifts.exclude(pk=form.instance.pk)
        if shifts.exists():
            form.add_error("name", "A shift with this name already exists at this station.")
            return None
        raise


def _save_lubricant_form(form):
    try:
        with transaction.atomic():
            return form.save()
    except (IntegrityError, ValidationError):
        products = Lubricant.objects.filter(
            station=form.station,
            code=form.cleaned_data["code"],
        )
        if form.instance.pk:
            products = products.exclude(pk=form.instance.pk)
        if products.exists():
            form.add_error(
                "code",
                "A lubricant with this code already exists at this station.",
            )
            return None
        raise


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def shift_list(request, station_id):
    station = _get_station(request.user, station_id)
    shifts = station.shifts.all()
    return render(
        request,
        "duties/shift_list.html",
        {"station": station, "shifts": shifts},
    )


@role_required(User.Role.OWNER)
def shift_create(request, station_id):
    station = get_object_or_404(
        Station.objects.filter(owner=request.user, is_active=True),
        pk=station_id,
    )
    if request.method == "POST":
        form = ShiftForm(station, request.POST)
        if form.is_valid():
            shift = _save_shift_form(form)
            if shift:
                messages.success(request, f"{shift.name} was added to {station.name}.")
                return redirect("duties:shift-list", station_id=station.pk)
    else:
        form = ShiftForm(station)
    return _render_shift_form(
        request,
        station,
        form,
        title=f"Add a shift at {station.name}",
        submit_label="Add shift",
    )


@role_required(User.Role.OWNER)
def shift_update(request, station_id, shift_id):
    station = get_object_or_404(Station.objects.filter(owner=request.user), pk=station_id)
    shift = get_object_or_404(Shift.objects.filter(station=station), pk=shift_id)
    if request.method == "POST":
        form = ShiftForm(station, request.POST, instance=shift)
        if form.is_valid():
            shift = _save_shift_form(form)
            if shift:
                messages.success(request, f"{shift.name} was updated.")
                return redirect("duties:shift-list", station_id=station.pk)
    else:
        form = ShiftForm(station, instance=shift)
    return _render_shift_form(
        request,
        station,
        form,
        title=f"Edit {shift.name}",
        submit_label="Save changes",
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def shift_toggle_status(request, station_id, shift_id):
    station = get_object_or_404(Station.objects.filter(owner=request.user), pk=station_id)
    shift = get_object_or_404(
        Shift.objects.select_for_update().filter(station=station),
        pk=shift_id,
    )
    shift.is_active = not shift.is_active
    shift.save(update_fields=("is_active", "updated_at"))
    status = "activated" if shift.is_active else "deactivated"
    messages.success(request, f"{shift.name} was {status}.")
    return redirect("duties:shift-list", station_id=station.pk)


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def lubricant_list(request, station_id):
    station = _get_station(request.user, station_id)
    products = station.lubricants.all()
    return render(
        request,
        "duties/lubricant_list.html",
        {"station": station, "products": products},
    )


@role_required(User.Role.OWNER)
def lubricant_create(request, station_id):
    station = get_object_or_404(
        Station.objects.filter(owner=request.user, is_active=True),
        pk=station_id,
    )
    if request.method == "POST":
        form = LubricantForm(station, request.POST)
        if form.is_valid():
            product = _save_lubricant_form(form)
            if product:
                messages.success(request, f"{product.name} was added to {station.name}.")
                return redirect("duties:lubricant-list", station_id=station.pk)
    else:
        form = LubricantForm(station)
    return render(
        request,
        "duties/lubricant_form.html",
        {
            "station": station,
            "form": form,
            "page_title": f"Add lubricant at {station.name}",
            "submit_label": "Add product",
        },
    )


@role_required(User.Role.OWNER)
def lubricant_update(request, station_id, lubricant_id):
    station = get_object_or_404(Station.objects.filter(owner=request.user), pk=station_id)
    product = get_object_or_404(
        Lubricant.objects.filter(station=station),
        pk=lubricant_id,
    )
    if request.method == "POST":
        form = LubricantForm(station, request.POST, instance=product)
        if form.is_valid():
            product = _save_lubricant_form(form)
            if product:
                messages.success(request, f"{product.name} was updated.")
                return redirect("duties:lubricant-list", station_id=station.pk)
    else:
        form = LubricantForm(station, instance=product)
    return render(
        request,
        "duties/lubricant_form.html",
        {
            "station": station,
            "form": form,
            "page_title": f"Edit {product.name}",
            "submit_label": "Save changes",
        },
    )


@role_required(User.Role.OWNER)
@require_POST
@transaction.atomic
def lubricant_toggle_status(request, station_id, lubricant_id):
    station = get_object_or_404(Station.objects.filter(owner=request.user), pk=station_id)
    product = get_object_or_404(
        Lubricant.objects.select_for_update().filter(station=station),
        pk=lubricant_id,
    )
    product.is_active = not product.is_active
    product.save(update_fields=("is_active", "updated_at"))
    status = "activated" if product.is_active else "deactivated"
    messages.success(request, f"{product.name} was {status}.")
    return redirect("duties:lubricant-list", station_id=station.pk)


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def report_list(request):
    reports = _accessible_reports(request.user)
    return render(request, "duties/report_list.html", {"reports": reports})


@role_required(User.Role.OWNER, User.Role.MANAGER)
def report_review_queue(request):
    reports = _accessible_reports(request.user).filter(
        status__in=(
            DutyReport.Status.SUBMITTED,
            DutyReport.Status.PENDING_VERIFICATION,
            DutyReport.Status.RESUBMITTED,
        )
    )
    return render(
        request,
        "duties/report_list.html",
        {"reports": reports, "review_queue": True},
    )


@role_required(User.Role.OWNER, User.Role.MANAGER, User.Role.DSMS)
def report_detail(request, report_id):
    report = get_object_or_404(_accessible_reports(request.user), pk=report_id)
    can_edit = (
        request.user.role == User.Role.DSMS
        and report.status in (DutyReport.Status.DRAFT, DutyReport.Status.CORRECTION_REQUESTED)
    )
    fuel_entries = list(
        report.fuel_entries.select_related(
            "nozzle__tank__fuel_type",
            "fuel_price",
        )
    )
    lubricant_entries = list(
        report.lubricant_entries.select_related("lubricant")
    )
    credit_sales = list(
        report.credit_sales.select_related("customer", "fuel_type")
    )
    total_lubricant_sales = sum(
        (entry.sale_amount for entry in lubricant_entries),
        start=Decimal("0.00"),
    )
    total_fuel_sales = sum(
        (entry.sale_amount for entry in fuel_entries),
        start=Decimal("0.00"),
    )
    collection = PaymentCollection.objects.filter(report=report).first()
    reconciliation_settings = ReconciliationSettings.objects.filter(
        station=report.station
    ).first()
    reconciliation = (
        collection.reconciliation_summary(reconciliation_settings)
        if collection
        else None
    )
    can_edit_collection = (
        request.user.role == User.Role.DSMS
        and report.status
        in (DutyReport.Status.DRAFT, DutyReport.Status.CORRECTION_REQUESTED)
    )
    can_authorize_adjustments = (
        request.user.role in (User.Role.OWNER, User.Role.MANAGER)
        and report.status
        in (
            DutyReport.Status.SUBMITTED,
            DutyReport.Status.PENDING_VERIFICATION,
            DutyReport.Status.RESUBMITTED,
            DutyReport.Status.APPROVED,
        )
    )
    return render(
        request,
        "duties/report_detail.html",
        {
            "report": report,
            "can_edit": can_edit,
            "fuel_entries": fuel_entries,
            "lubricant_entries": lubricant_entries,
            "credit_sales": credit_sales,
            "total_sale_litres": sum(
                (entry.actual_sale_litres for entry in fuel_entries),
                start=Decimal("0.000"),
            ),
            "total_fuel_sales": total_fuel_sales,
            "total_lubricant_sales": total_lubricant_sales,
            "total_duty_sales": total_fuel_sales + total_lubricant_sales,
            "collection": collection,
            "reconciliation": reconciliation,
            "reconciliation_settings": reconciliation_settings,
            "adjustments": report.reconciliation_adjustments.select_related(
                "authorized_by"
            ),
            "can_edit_collection": can_edit_collection,
            "can_authorize_adjustments": can_authorize_adjustments,
            "review_history": report.review_history.select_related("actor"),
            "can_start_verification": (
                request.user.role in (User.Role.OWNER, User.Role.MANAGER)
                and report.status == DutyReport.Status.SUBMITTED
            ),
            "can_review_report": (
                request.user.role in (User.Role.OWNER, User.Role.MANAGER)
                and report.status
                in (
                    DutyReport.Status.PENDING_VERIFICATION,
                    DutyReport.Status.RESUBMITTED,
                )
            ),
        },
    )


@role_required(User.Role.OWNER)
def reconciliation_settings_update(request, station_id):
    station = get_object_or_404(
        Station.objects.filter(owner=request.user),
        pk=station_id,
    )
    settings = ReconciliationSettings.objects.filter(station=station).first()
    if request.method == "POST":
        form = ReconciliationSettingsForm(request.POST, instance=settings)
        if form.is_valid():
            settings = form.save(commit=False)
            settings.station = station
            settings.save()
            messages.success(request, "Reconciliation settings were saved.")
            return redirect("duties:reconciliation-settings", station_id=station.pk)
    else:
        form = ReconciliationSettingsForm(instance=settings)
    return render(
        request,
        "duties/reconciliation_settings_form.html",
        {"station": station, "form": form},
    )


@role_required(User.Role.DSMS)
def report_payment_collection(request, report_id):
    if request.method == "POST":
        with transaction.atomic():
            report = get_object_or_404(
                DutyReport.objects.select_for_update().select_related("station"),
                pk=report_id,
                employee__user=request.user,
            )
            if report.status not in (
                DutyReport.Status.DRAFT,
                DutyReport.Status.CORRECTION_REQUESTED,
            ):
                raise PermissionDenied
            collection = PaymentCollection.objects.filter(report=report).first()
            form = PaymentCollectionForm(
                report,
                request.POST,
                instance=collection,
            )
            if form.is_valid():
                form.save()
                messages.success(request, "Payment collection details were saved.")
                return redirect("duties:report-detail", report_id=report.pk)
    else:
        report = get_object_or_404(
            DutyReport.objects.select_related("station"),
            pk=report_id,
            employee__user=request.user,
        )
        if report.status not in (
            DutyReport.Status.DRAFT,
            DutyReport.Status.CORRECTION_REQUESTED,
        ):
            raise PermissionDenied
        collection = PaymentCollection.objects.filter(report=report).first()
        form = PaymentCollectionForm(report, instance=collection)
    return render(
        request,
        "duties/payment_collection_form.html",
        {
            "report": report,
            "form": form,
            "cash_fields": [
                (form[field_name], label)
                for field_name, label in (
                    ("cash_500_count", "₹500 notes"),
                    ("cash_200_count", "₹200 notes"),
                    ("cash_100_count", "₹100 notes"),
                    ("cash_50_count", "₹50 notes"),
                    ("cash_20_count", "₹20 notes"),
                    ("cash_10_count", "₹10 notes"),
                    ("cash_5_coin_count", "₹5 coins"),
                    ("cash_2_coin_count", "₹2 coins"),
                    ("cash_1_coin_count", "₹1 coins"),
                )
            ],
            "tender_fields": [
                form[field_name]
                for field_name in (
                    "upi",
                    "debit_card",
                    "credit_card",
                )
            ],
        },
    )


@role_required(User.Role.OWNER, User.Role.MANAGER)
def reconciliation_adjustment_create(request, report_id):
    if request.method == "POST":
        with transaction.atomic():
            report = get_object_or_404(
                _accessible_reports(request.user).select_for_update(),
                pk=report_id,
                status__in=(
                    DutyReport.Status.SUBMITTED,
                    DutyReport.Status.PENDING_VERIFICATION,
                    DutyReport.Status.RESUBMITTED,
                    DutyReport.Status.APPROVED,
                ),
            )
            form = ReconciliationAdjustmentForm(report, request.user, request.POST)
            if form.is_valid():
                adjustment = form.save()
                messages.success(
                    request,
                    f"Authorized adjustment of ₹{adjustment.amount:.2f} was recorded.",
                )
                return redirect("duties:report-detail", report_id=report.pk)
    else:
        report = get_object_or_404(
            _accessible_reports(request.user),
            pk=report_id,
            status__in=(
                DutyReport.Status.SUBMITTED,
                DutyReport.Status.PENDING_VERIFICATION,
                DutyReport.Status.RESUBMITTED,
                DutyReport.Status.APPROVED,
            ),
        )
        form = ReconciliationAdjustmentForm(report, request.user)
    return render(
        request,
        "duties/reconciliation_adjustment_form.html",
        {"report": report, "form": form},
    )


@role_required(User.Role.DSMS)
def report_create(request):
    employee = get_object_or_404(
        Employee.objects.select_related("assigned_station"),
        user=request.user,
        is_active=True,
        assigned_station__is_active=True,
    )
    station = employee.assigned_station
    if request.method == "POST":
        form = DutyReportCreateForm(station, request.POST)
        if form.is_valid():
            token = form.cleaned_data["submission_token"]
            duplicate_response = _duplicate_submission_response(
                request,
                employee,
                token,
            )
            if duplicate_response:
                return duplicate_response

            report = form.save(commit=False)
            report.station = station
            report.employee = employee
            report.submission_token = token
            try:
                with transaction.atomic():
                    report.save()
            except IntegrityError:
                duplicate_response = _duplicate_submission_response(
                    request,
                    employee,
                    token,
                )
                if not duplicate_response:
                    raise
                return duplicate_response
            except ValidationError as error:
                if "submission_token" not in error.error_dict:
                    raise
                duplicate_response = _duplicate_submission_response(
                    request,
                    employee,
                    token,
                )
                if not duplicate_response:
                    raise
                return duplicate_response

            messages.success(request, "Duty report saved as a draft.")
            return redirect("duties:report-detail", report_id=report.pk)
    else:
        form = DutyReportCreateForm(station)
    return render(
        request,
        "duties/report_form.html",
        {"form": form, "page_title": "Create a duty report", "submit_label": "Save draft"},
    )


@role_required(User.Role.DSMS)
@transaction.atomic
def report_update(request, report_id):
    report = get_object_or_404(
        DutyReport.objects.select_for_update().select_related("station"),
        pk=report_id,
        employee__user=request.user,
    )
    if report.status not in (
        DutyReport.Status.DRAFT,
        DutyReport.Status.CORRECTION_REQUESTED,
    ):
        raise PermissionDenied
    if request.method == "POST":
        form = DutyReportForm(report.station, request.POST, instance=report)
        if form.is_valid():
            report = form.save()
            messages.success(request, "Duty report draft was updated.")
            return redirect("duties:report-detail", report_id=report.pk)
    else:
        form = DutyReportForm(report.station, instance=report)
    return render(
        request,
        "duties/report_form.html",
        {
            "form": form,
            "page_title": "Edit duty report",
            "submit_label": "Save changes",
            "report": report,
        },
    )


@role_required(User.Role.DSMS)
def report_fuel_entries(request, report_id):
    if request.method == "POST":
        with transaction.atomic():
            report = get_object_or_404(
                DutyReport.objects.select_for_update().select_related("station", "shift"),
                pk=report_id,
                employee__user=request.user,
            )
            if report.status not in (
                DutyReport.Status.DRAFT,
                DutyReport.Status.CORRECTION_REQUESTED,
            ):
                raise PermissionDenied
            formset = DutyFuelEntryFormSet(
                request.POST,
                instance=report,
                prefix="fuel_entries",
            )
            if formset.is_valid():
                formset.save()
                messages.success(request, "Nozzle meter readings were saved.")
                return redirect("duties:report-detail", report_id=report.pk)
    else:
        report = get_object_or_404(
            DutyReport.objects.select_related("station", "shift"),
            pk=report_id,
            employee__user=request.user,
        )
        if report.status not in (
            DutyReport.Status.DRAFT,
            DutyReport.Status.CORRECTION_REQUESTED,
        ):
            raise PermissionDenied
        formset = DutyFuelEntryFormSet(
            instance=report,
            prefix="fuel_entries",
        )
    return render(
        request,
        "duties/fuel_entries_form.html",
        {"report": report, "formset": formset},
    )


@role_required(User.Role.DSMS)
def report_lubricant_entries(request, report_id):
    if request.method == "POST":
        with transaction.atomic():
            report = get_object_or_404(
                DutyReport.objects.select_for_update().select_related("station"),
                pk=report_id,
                employee__user=request.user,
            )
            if report.status not in (
                DutyReport.Status.DRAFT,
                DutyReport.Status.CORRECTION_REQUESTED,
            ):
                raise PermissionDenied
            formset = DutyLubricantEntryFormSet(
                request.POST,
                instance=report,
                prefix="lubricant_entries",
            )
            if formset.is_valid():
                formset.save()
                messages.success(request, "Lubricant sales were saved.")
                return redirect("duties:report-detail", report_id=report.pk)
    else:
        report = get_object_or_404(
            DutyReport.objects.select_related("station"),
            pk=report_id,
            employee__user=request.user,
        )
        if report.status not in (
            DutyReport.Status.DRAFT,
            DutyReport.Status.CORRECTION_REQUESTED,
        ):
            raise PermissionDenied
        formset = DutyLubricantEntryFormSet(
            instance=report,
            prefix="lubricant_entries",
        )
    return render(
        request,
        "duties/lubricant_entries_form.html",
        {"report": report, "formset": formset},
    )


@role_required(User.Role.DSMS)
@require_POST
@transaction.atomic
def report_submit(request, report_id):
    report = get_object_or_404(
        DutyReport.objects.select_for_update(),
        pk=report_id,
        employee__user=request.user,
    )
    if report.status in (
        DutyReport.Status.SUBMITTED,
        DutyReport.Status.PENDING_VERIFICATION,
        DutyReport.Status.RESUBMITTED,
    ):
        messages.info(request, "This duty report is already submitted.")
    elif report.status not in (
        DutyReport.Status.DRAFT,
        DutyReport.Status.CORRECTION_REQUESTED,
    ):
        raise PermissionDenied
    else:
        active_nozzles = Nozzle.objects.filter(
            station=report.station,
            is_active=True,
        )
        recorded_nozzle_ids = report.fuel_entries.values_list("nozzle_id", flat=True)
        missing_nozzles = list(
            active_nozzles.exclude(pk__in=recorded_nozzle_ids).values_list(
                "code",
                flat=True,
            )
        )
        if missing_nozzles:
            messages.error(
                request,
                "Add meter readings for every active nozzle before submitting. "
                f"Missing: {', '.join(missing_nozzles)}.",
            )
            return redirect("duties:report-detail", report_id=report.pk)
        if not report.fuel_entries.exists():
            messages.error(
                request,
                "At least one active nozzle with a fuel reading is required before submitting.",
            )
            return redirect("duties:report-detail", report_id=report.pk)
        lubricant_entries = list(
            report.lubricant_entries.select_related("lubricant")
        )
        previous_submission = (
            report.review_history.filter(
                action__in=(
                    DutyReportReview.Action.SUBMITTED,
                    DutyReportReview.Action.RESUBMITTED,
                )
            )
            .order_by("-created_at", "-pk")
            .first()
        )
        previous_snapshot = (
            previous_submission.stock_snapshot if previous_submission else {}
        )
        current_totals = {}
        for entry in lubricant_entries:
            key = str(entry.lubricant_id)
            current_totals[key] = current_totals.get(key, Decimal("0.000"))
            current_totals[key] += entry.quantity
        current_snapshot = {
            key: str(quantity) for key, quantity in current_totals.items()
        }
        product_ids = {
            int(product_id)
            for product_id in (*previous_snapshot.keys(), *current_snapshot.keys())
        }
        products = {
            str(product.pk): product
            for product in Lubricant.objects.select_for_update()
            .filter(pk__in=product_ids)
            .order_by("pk")
        }
        stock_deltas = {
            product_id: current_totals.get(product_id, Decimal("0.000"))
            - Decimal(previous_snapshot.get(product_id, "0.000"))
            for product_id in products
        }
        shortages = [
            f"{products[product_id].name} "
            f"(available {products[product_id].current_stock} "
            f"{products[product_id].unit}, additional {delta})"
            for product_id, delta in stock_deltas.items()
            if delta > 0 and products[product_id].current_stock < delta
        ]
        if shortages:
            messages.error(
                request,
                "Insufficient lubricant stock: " + "; ".join(shortages),
            )
            return redirect("duties:report-detail", report_id=report.pk)
        for product_id, delta in stock_deltas.items():
            product = products[product_id]
            product.current_stock -= delta
            product.save(update_fields=("current_stock", "updated_at"))
        previous_status = report.status
        report.status = DutyReport.Status.SUBMITTED
        action = DutyReportReview.Action.SUBMITTED
        if previous_status == DutyReport.Status.CORRECTION_REQUESTED:
            report.status = DutyReport.Status.RESUBMITTED
            action = DutyReportReview.Action.RESUBMITTED
        report.submitted_at = timezone.now()
        report.save(update_fields=("status", "submitted_at", "updated_at"))
        _record_review(
            report,
            request.user,
            action,
            previous_status,
            stock_snapshot=current_snapshot,
        )
        if action == DutyReportReview.Action.RESUBMITTED:
            messages.success(request, "Duty report was resubmitted for verification.")
        else:
            messages.success(request, "Duty report submitted for verification.")
    return redirect("duties:report-detail", report_id=report.pk)


@role_required(User.Role.OWNER, User.Role.MANAGER)
@require_POST
@transaction.atomic
def report_verification_start(request, report_id):
    report = _reviewable_report(request, report_id, lock=True)
    if report.status != DutyReport.Status.SUBMITTED:
        raise PermissionDenied
    previous_status = report.status
    report.status = DutyReport.Status.PENDING_VERIFICATION
    report.save(update_fields=("status", "updated_at"))
    _record_review(
        report,
        request.user,
        DutyReportReview.Action.VERIFICATION_STARTED,
        previous_status,
    )
    messages.success(request, "Report moved to pending verification.")
    return redirect("duties:report-detail", report_id=report.pk)


@role_required(User.Role.OWNER, User.Role.MANAGER)
@require_POST
@transaction.atomic
def report_approve(request, report_id):
    report = _reviewable_report(request, report_id, lock=True)
    if report.status not in (
        DutyReport.Status.PENDING_VERIFICATION,
        DutyReport.Status.RESUBMITTED,
    ):
        raise PermissionDenied
    shortages = _post_fuel_inventory(report, request.user)
    if shortages:
        messages.error(
            request,
            "Report cannot be approved because fuel stock is insufficient: "
            + "; ".join(shortages),
        )
        return redirect("duties:report-detail", report_id=report.pk)
    previous_status = report.status
    report.status = DutyReport.Status.APPROVED
    report.save(update_fields=("status", "updated_at"))
    _record_review(
        report,
        request.user,
        DutyReportReview.Action.APPROVED,
        previous_status,
    )
    messages.success(request, "Duty report was approved.")
    return redirect("duties:report-detail", report_id=report.pk)


@role_required(User.Role.OWNER, User.Role.MANAGER)
def report_request_correction(request, report_id):
    if request.method == "POST":
        with transaction.atomic():
            report = _reviewable_report(request, report_id, lock=True)
            if report.status not in (
                DutyReport.Status.PENDING_VERIFICATION,
                DutyReport.Status.RESUBMITTED,
            ):
                raise PermissionDenied
            form = DutyReportCorrectionForm(request.POST)
            if form.is_valid():
                previous_status = report.status
                report.status = DutyReport.Status.CORRECTION_REQUESTED
                report.save(update_fields=("status", "updated_at"))
                _record_review(
                    report,
                    request.user,
                    DutyReportReview.Action.CORRECTION_REQUESTED,
                    previous_status,
                    notes=form.cleaned_data["reason"],
                )
                messages.success(request, "Correction request was sent to the DSMS.")
                return redirect("duties:report-detail", report_id=report.pk)
    else:
        report = _reviewable_report(request, report_id)
        if report.status not in (
            DutyReport.Status.PENDING_VERIFICATION,
            DutyReport.Status.RESUBMITTED,
        ):
            raise PermissionDenied
        form = DutyReportCorrectionForm()
    return render(
        request,
        "duties/correction_request_form.html",
        {"report": report, "form": form},
    )
