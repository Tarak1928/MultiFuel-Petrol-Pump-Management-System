import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q, Sum

from accounts.models import User
from employees.models import Employee, ManagerAssignment
from fuel.models import FuelPrice, Nozzle
from stations.models import Station

from .pricing import effective_price_time_for_report


class Shift(models.Model):
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="shifts",
    )
    name = models.CharField(max_length=60)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_time", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=("station", "name"),
                name="shift_station_name_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["station", "is_active"], name="shift_station_active_idx"),
        ]

    def clean(self):
        if self.station_id and not self.station.is_active and not self.pk:
            raise ValidationError({"station": "New shifts require an active station."})

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} — {self.station.name}"


class DutyReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        CORRECTION_REQUESTED = "correction_requested", "Correction requested"
        APPROVED = "approved", "Approved"

    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="duty_reports",
    )
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="duty_reports",
    )
    shift = models.ForeignKey(
        Shift,
        on_delete=models.PROTECT,
        related_name="duty_reports",
    )
    report_date = models.DateField()
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    notes = models.TextField(blank=True)
    submission_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-report_date", "-created_at"]
        indexes = [
            models.Index(
                fields=["station", "report_date", "status"],
                name="duty_station_date_status_idx",
            ),
            models.Index(
                fields=["employee", "report_date"],
                name="duty_employee_date_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(status="draft", submitted_at__isnull=True)
                    | (
                        ~models.Q(status="draft")
                        & models.Q(submitted_at__isnull=False)
                    )
                ),
                name="duty_submitted_at_matches_status",
            ),
        ]

    def clean(self):
        errors = {}
        if self.employee_id and self.station_id:
            if self.employee.user.role != User.Role.DSMS:
                errors["employee"] = "Only DSMS staff can create duty reports."
            if self.employee.assigned_station_id != self.station_id:
                errors["station"] = "The report must use the employee's assigned station."
            if self.employee.owner_id != self.station.owner_id:
                errors["employee"] = "The employee and station must belong to the same Owner."
            if not self.employee.is_active:
                errors["employee"] = "Inactive employees cannot create duty reports."
        if self.shift_id and self.station_id:
            if self.shift.station_id != self.station_id:
                errors["shift"] = "Choose a shift at the report's station."
            if not self.shift.is_active and (
                not self.pk or self.shift_id != self._loaded_shift_id()
            ):
                errors["shift"] = "New reports must use an active shift."
        if self.status == self.Status.DRAFT and self.submitted_at is not None:
            errors["submitted_at"] = "Draft reports cannot have a submission time."
        elif self.status != self.Status.DRAFT and self.submitted_at is None:
            errors["submitted_at"] = "Submitted reports must have a submission time."
        if errors:
            raise ValidationError(errors)

    def _loaded_shift_id(self):
        if not self.pk:
            return None
        return (
            type(self).objects.filter(pk=self.pk)
            .values_list("shift_id", flat=True)
            .first()
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.station.code} · {self.report_date:%d %b %Y} · {self.employee}"


class DutyFuelEntry(models.Model):
    report = models.ForeignKey(
        DutyReport,
        on_delete=models.CASCADE,
        related_name="fuel_entries",
    )
    nozzle = models.ForeignKey(
        Nozzle,
        on_delete=models.PROTECT,
        related_name="duty_entries",
    )
    fuel_price = models.ForeignKey(
        FuelPrice,
        on_delete=models.PROTECT,
        related_name="duty_entries",
    )
    opening_reading = models.DecimalField(max_digits=12, decimal_places=3)
    closing_reading = models.DecimalField(max_digits=12, decimal_places=3)
    testing_litres = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    price_per_litre = models.DecimalField(max_digits=10, decimal_places=2)
    actual_sale_litres = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    sale_amount = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nozzle__code"]
        constraints = [
            models.UniqueConstraint(
                fields=("report", "nozzle"),
                name="duty_report_nozzle_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(opening_reading__gte=0)
                    & Q(closing_reading__gte=F("opening_reading"))
                    & Q(testing_litres__gte=0)
                    & Q(
                        testing_litres__lte=F("closing_reading")
                        - F("opening_reading")
                    )
                ),
                name="duty_fuel_readings_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(price_per_litre__gt=0)
                    & Q(actual_sale_litres__gte=0)
                    & Q(sale_amount__gte=0)
                ),
                name="duty_fuel_values_nonnegative",
            ),
        ]

    @property
    def metered_litres(self):
        return self.closing_reading - self.opening_reading

    def clean(self):
        errors = {}
        if self.report_id and self.nozzle_id:
            if self.nozzle.station_id != self.report.station_id:
                errors["nozzle"] = "Choose a nozzle at the duty report's station."
            if not self.nozzle.is_active and (
                not self.pk or self.nozzle_id != self._loaded_nozzle_id()
            ):
                errors["nozzle"] = "New entries must use an active nozzle."
            if self.report.status not in (
                DutyReport.Status.DRAFT,
                DutyReport.Status.CORRECTION_REQUESTED,
            ):
                errors["report"] = "Only editable duty reports can contain changes."
        if self.fuel_price_id and self.nozzle_id and self.report_id:
            if self.fuel_price.station_id != self.report.station_id:
                errors["fuel_price"] = "The price must belong to the report's station."
            if self.fuel_price.fuel_type_id != self.nozzle.tank.fuel_type_id:
                errors["fuel_price"] = "The price must match the nozzle's fuel type."
            if self.fuel_price.effective_from > effective_price_time_for_report(self.report):
                errors["fuel_price"] = "The price must be effective when the shift starts."
            if (
                self.price_per_litre is not None
                and Decimal(str(self.price_per_litre))
                != Decimal(str(self.fuel_price.price_per_litre))
            ):
                errors["price_per_litre"] = "The recorded price must match its price history."
        if (
            self.opening_reading is not None
            and self.closing_reading is not None
            and self.testing_litres is not None
        ):
            metered_litres = self.closing_reading - self.opening_reading
            actual_sale_litres = metered_litres - self.testing_litres
            if self.closing_reading < self.opening_reading:
                errors["closing_reading"] = "Closing reading cannot be below the opening reading."
            if self.testing_litres > metered_litres:
                errors["testing_litres"] = "Testing litres cannot exceed metered litres."
            if actual_sale_litres >= 0:
                self.actual_sale_litres = actual_sale_litres
                if self.price_per_litre is not None:
                    self.sale_amount = (actual_sale_litres * self.price_per_litre).quantize(
                        Decimal("0.01"),
                        rounding=ROUND_HALF_UP,
                    )
        if errors:
            raise ValidationError(errors)

    def _loaded_nozzle_id(self):
        if not self.pk:
            return None
        return (
            type(self).objects.filter(pk=self.pk)
            .values_list("nozzle_id", flat=True)
            .first()
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.report} · Nozzle {self.nozzle.code}"


class Lubricant(models.Model):
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="lubricants",
    )
    code = models.CharField(
        max_length=20,
        validators=[
            RegexValidator(
                regex=r"^[A-Z0-9][A-Z0-9_-]{1,19}$",
                message="Use 2–20 uppercase letters, digits, hyphens, or underscores.",
            )
        ],
    )
    name = models.CharField(max_length=120)
    unit = models.CharField(
        max_length=20,
        default="unit",
        help_text="For example: bottle, can, litre, or pack.",
    )
    current_stock = models.DecimalField(max_digits=12, decimal_places=3)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=("station", "code"),
                name="lubricant_station_code_uniq",
            ),
            models.CheckConstraint(
                condition=Q(current_stock__gte=0),
                name="lubricant_stock_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(selling_price__gt=0),
                name="lubricant_price_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["station", "is_active"],
                name="lubricant_station_active_idx",
            ),
        ]

    def clean(self):
        if self.station_id and not self.station.is_active and not self.pk:
            raise ValidationError(
                {"station": "New products require an active station."}
            )

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        self.name = self.name.strip()
        self.unit = self.unit.strip().lower()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"


class DutyLubricantEntry(models.Model):
    report = models.ForeignKey(
        DutyReport,
        on_delete=models.CASCADE,
        related_name="lubricant_entries",
    )
    lubricant = models.ForeignKey(
        Lubricant,
        on_delete=models.PROTECT,
        related_name="duty_entries",
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    sale_amount = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["lubricant__name"]
        constraints = [
            models.UniqueConstraint(
                fields=("report", "lubricant"),
                name="duty_report_lubricant_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(quantity__gt=0)
                    & Q(unit_price__gt=0)
                    & Q(sale_amount__gte=0)
                ),
                name="duty_lubricant_values_valid",
            ),
        ]

    def clean(self):
        errors = {}
        if self.report_id and self.lubricant_id:
            if self.lubricant.station_id != self.report.station_id:
                errors["lubricant"] = (
                    "Choose a product stocked at the duty report's station."
                )
            if not self.lubricant.is_active and (
                not self.pk or self.lubricant_id != self._loaded_lubricant_id()
            ):
                errors["lubricant"] = "New sales must use an active product."
            if self.report.status not in (
                DutyReport.Status.DRAFT,
                DutyReport.Status.CORRECTION_REQUESTED,
            ):
                errors["report"] = "Only editable duty reports can contain changes."
            if (
                self.unit_price is not None
                and (
                    not self.pk
                    or self.lubricant_id != self._loaded_lubricant_id()
                )
                and Decimal(str(self.unit_price))
                != Decimal(str(self.lubricant.selling_price))
            ):
                errors["unit_price"] = (
                    "New sales must use the product's current selling price."
                )
        if self.quantity is not None and self.unit_price is not None:
            if self.quantity > 0 and self.unit_price > 0:
                self.sale_amount = (self.quantity * self.unit_price).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
        if errors:
            raise ValidationError(errors)

    def _loaded_lubricant_id(self):
        if not self.pk:
            return None
        return (
            type(self).objects.filter(pk=self.pk)
            .values_list("lubricant_id", flat=True)
            .first()
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.report} · {self.lubricant.name}"


class ReconciliationSettings(models.Model):
    station = models.OneToOneField(
        Station,
        on_delete=models.PROTECT,
        related_name="reconciliation_settings",
    )
    include_advances = models.BooleanField(
        default=True,
        help_text="Count advances applied to this shift as settlement for its sales.",
    )
    include_otp = models.BooleanField(
        default=True,
        help_text="Count OTP transactions as settlement for this shift's sales.",
    )
    tolerance_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Maximum absolute variance treated as matched.",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(tolerance_amount__gte=0),
                name="recon_tolerance_nonneg",
            ),
        ]

    def clean(self):
        if self.tolerance_amount is not None and self.tolerance_amount < 0:
            raise ValidationError(
                {"tolerance_amount": "Reconciliation tolerance cannot be negative."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Reconciliation settings — {self.station}"


class PaymentCollection(models.Model):
    class ReconciliationStatus(models.TextChoices):
        MATCHED = "matched", "Matched"
        SHORTAGE = "shortage", "Shortage"
        EXCESS = "excess", "Excess"

    report = models.OneToOneField(
        DutyReport,
        on_delete=models.CASCADE,
        related_name="payment_collection",
    )
    upi = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    debit_card = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    credit_card = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    credit_sales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    advances = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    otp = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    expenses = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    expense_notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(upi__gte=0)
                    & Q(debit_card__gte=0)
                    & Q(credit_card__gte=0)
                    & Q(credit_sales__gte=0)
                    & Q(advances__gte=0)
                    & Q(otp__gte=0)
                    & Q(expenses__gte=0)
                ),
                name="pay_collection_nonneg",
            ),
        ]

    def clean(self):
        errors = {}
        if self.report_id:
            if self.report.status not in (
                DutyReport.Status.DRAFT,
                DutyReport.Status.CORRECTION_REQUESTED,
            ):
                errors["report"] = "Collections are locked after report submission."
            if (
                self.credit_sales is not None
                and self.credit_sales > self.duty_sales_total
            ):
                errors["credit_sales"] = "Credit sales cannot exceed total duty sales."
        if errors:
            raise ValidationError(errors)

    @property
    def cash_total(self):
        return sum(
            (
                Decimal(denomination) * count
                for denomination, count in self.cash_denominations.values_list(
                    "denomination",
                    "count",
                )
            ),
            start=Decimal("0.00"),
        ).quantize(Decimal("0.01"))

    @property
    def duty_sales_total(self):
        fuel_total = (
            self.report.fuel_entries.aggregate(total=Sum("sale_amount"))["total"]
            or Decimal("0.00")
        )
        lubricant_total = (
            self.report.lubricant_entries.aggregate(total=Sum("sale_amount"))["total"]
            or Decimal("0.00")
        )
        return fuel_total + lubricant_total

    def reconciliation_summary(self, settings=None):
        if settings is None:
            settings = ReconciliationSettings.objects.filter(
                station_id=self.report.station_id
            ).first()
        counted_cash = self.cash_total
        expected = self.duty_sales_total - self.credit_sales
        actual = (
            counted_cash
            + self.upi
            + self.debit_card
            + self.credit_card
            + (self.advances if settings is None or settings.include_advances else 0)
            + (self.otp if settings is None or settings.include_otp else 0)
        )
        adjustments = (
            self.report.reconciliation_adjustments.aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        variance = actual + adjustments - expected
        tolerance = (
            settings.tolerance_amount
            if settings is not None
            else Decimal("0.00")
        )
        if abs(variance) <= tolerance:
            status = self.ReconciliationStatus.MATCHED
        elif variance < 0:
            status = self.ReconciliationStatus.SHORTAGE
        else:
            status = self.ReconciliationStatus.EXCESS
        return {
            "expected_collections": expected,
            "actual_collections": actual,
            "authorized_adjustments": adjustments,
            "variance": variance,
            "status": status,
        }

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Collections — {self.report}"


class CashDenomination(models.Model):
    DENOMINATION_CHOICES = (
        (500, "₹500"),
        (200, "₹200"),
        (100, "₹100"),
        (50, "₹50"),
        (20, "₹20"),
        (10, "₹10"),
        (5, "₹5 coin"),
        (2, "₹2 coin"),
        (1, "₹1 coin"),
    )

    collection = models.ForeignKey(
        PaymentCollection,
        on_delete=models.CASCADE,
        related_name="cash_denominations",
    )
    denomination = models.PositiveSmallIntegerField(choices=DENOMINATION_CHOICES)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-denomination"]
        constraints = [
            models.UniqueConstraint(
                fields=("collection", "denomination"),
                name="cash_denomination_unique",
            ),
            models.CheckConstraint(
                condition=Q(count__gte=0),
                name="pay_cash_count_nonneg",
            ),
        ]

    def clean(self):
        if self.collection_id and self.collection.report.status not in (
            DutyReport.Status.DRAFT,
            DutyReport.Status.CORRECTION_REQUESTED,
        ):
            raise ValidationError(
                {"collection": "Cash denominations are locked after report submission."}
            )

    @property
    def amount(self):
        return Decimal(self.denomination * self.count).quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_denomination_display()} × {self.count}"


class ReconciliationAdjustment(models.Model):
    report = models.ForeignKey(
        DutyReport,
        on_delete=models.CASCADE,
        related_name="reconciliation_adjustments",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Use a positive amount for an approved increase or negative for a decrease.",
    )
    reason = models.CharField(max_length=500)
    authorized_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="reconciliation_adjustments",
    )
    authorized_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-authorized_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(amount=0),
                name="recon_adjustment_nonzero",
            ),
        ]

    def clean(self):
        errors = {}
        if self.report_id and self.report.status not in (
            DutyReport.Status.SUBMITTED,
            DutyReport.Status.APPROVED,
        ):
            errors["report"] = "Adjustments require a submitted or approved duty report."
        if self.authorized_by_id:
            user = self.authorized_by
            if not user.is_active or not user.is_approved:
                errors["authorized_by"] = "Only active, approved staff can authorize adjustments."
            elif not self.report_id:
                errors["report"] = "Choose a report for this adjustment."
            elif user.role == User.Role.OWNER:
                if user.pk != self.report.station.owner_id:
                    errors["authorized_by"] = "Only the station Owner can authorize this adjustment."
            elif user.role == User.Role.MANAGER:
                assigned = ManagerAssignment.objects.filter(
                    employee__user=user,
                    employee__is_active=True,
                    station_id=self.report.station_id,
                    station__is_active=True,
                ).exists()
                if not assigned:
                    errors["authorized_by"] = (
                        "Only a Manager assigned to the station can authorize this adjustment."
                    )
            else:
                errors["authorized_by"] = "Only the Owner or an assigned Manager can authorize adjustments."
        if not (self.reason or "").strip():
            errors["reason"] = "Provide a reason for the adjustment."
        if self.amount == 0:
            errors["amount"] = "An adjustment must be non-zero."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "report_id",
                "amount",
                "reason",
                "authorized_by_id",
            ).first()
            if previous and any(
                previous[field] != getattr(self, field)
                for field in ("report_id", "amount", "reason", "authorized_by_id")
            ):
                raise ValidationError("Authorized reconciliation adjustments are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.amount} adjustment — {self.report}"
