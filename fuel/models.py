from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from accounts.models import User
from employees.models import ManagerAssignment


class FuelType(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="fuel_types",
        blank=True,
        null=True,
        help_text="Empty for a shared system fuel type.",
    )
    name = models.CharField(max_length=80)
    code = models.CharField(
        max_length=20,
        unique=True,
        validators=[
            RegexValidator(
                regex=r"^[A-Z0-9][A-Z0-9_-]{1,19}$",
                message="Use 2–20 uppercase letters, digits, hyphens, or underscores.",
            )
        ],
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "code"]

    def clean(self):
        if self.owner_id and (
            self.owner.role != User.Role.OWNER
            or not self.owner.is_approved
            or not self.owner.is_active
        ):
            raise ValidationError(
                {"owner": "Custom fuel types must belong to an approved Owner."}
            )

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.code})"


class Tank(models.Model):
    station = models.ForeignKey(
        "stations.Station",
        on_delete=models.PROTECT,
        related_name="tanks",
    )
    fuel_type = models.ForeignKey(
        FuelType,
        on_delete=models.PROTECT,
        related_name="tanks",
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
    capacity_litres = models.DecimalField(max_digits=12, decimal_places=3)
    current_stock_litres = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    reorder_level_litres = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        default=0,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["station__name", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=("station", "code"),
                name="tank_station_code_uniq",
            ),
            models.CheckConstraint(
                condition=Q(capacity_litres__gt=0),
                name="tank_capacity_positive",
            ),
            models.CheckConstraint(
                condition=Q(current_stock_litres__gte=0)
                & Q(current_stock_litres__lte=F("capacity_litres")),
                name="tank_stock_within_capacity",
            ),
            models.CheckConstraint(
                condition=Q(reorder_level_litres__gte=0)
                & Q(reorder_level_litres__lte=F("capacity_litres")),
                name="tank_reorder_within_capacity",
            ),
        ]
        indexes = [
            models.Index(fields=["station", "fuel_type"], name="tank_station_fuel_idx"),
        ]

    def clean(self):
        errors = {}
        if self.station_id and self.fuel_type_id:
            if self.fuel_type.owner_id not in (None, self.station.owner_id):
                errors["fuel_type"] = "Choose a fuel type available to this Owner."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.station.code} · {self.code} ({self.fuel_type.code})"

    @property
    def is_low_stock(self):
        return (
            self.reorder_level_litres > 0
            and self.current_stock_litres <= self.reorder_level_litres
        )


class FuelDelivery(models.Model):
    station = models.ForeignKey(
        "stations.Station",
        on_delete=models.PROTECT,
        related_name="fuel_deliveries",
    )
    tank = models.ForeignKey(
        Tank,
        on_delete=models.PROTECT,
        related_name="deliveries",
    )
    invoice_number = models.CharField(max_length=50)
    invoice_line_number = models.PositiveSmallIntegerField(default=1)
    supplier_name = models.CharField(max_length=120)
    supplier_gstin = models.CharField(max_length=20, blank=True)
    supplier_phone = models.CharField(max_length=20, blank=True)
    supplier_address = models.CharField(max_length=255, blank=True)
    invoice_date = models.DateField()
    received_at = models.DateTimeField(default=timezone.now)
    quantity_litres = models.DecimalField(max_digits=12, decimal_places=3)
    cost_per_litre = models.DecimalField(max_digits=10, decimal_places=2)
    total_cost = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_fuel_deliveries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=("station", "invoice_number", "invoice_line_number"),
                name="fuel_delivery_invoice_line_uniq",
            ),
            models.CheckConstraint(
                condition=Q(quantity_litres__gt=0) & Q(cost_per_litre__gte=0),
                name="fuel_delivery_values_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=("station", "-received_at"),
                name="fuel_delivery_station_date_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.tank_id and self.station_id:
            if self.tank.station_id != self.station_id:
                errors["tank"] = "Choose a tank at this station."
            if not self.tank.is_active:
                errors["tank"] = "Fuel can only be delivered to an active tank."
        if self.station_id and not self.station.is_active:
            errors["station"] = "Fuel can only be delivered to an active station."
        if not (self.supplier_name or "").strip():
            errors["supplier_name"] = "Enter the supplier name."
        if self.recorded_by_id and self.station_id:
            if (
                self.recorded_by.role != User.Role.OWNER
                or self.recorded_by_id != self.station.owner_id
                or not self.recorded_by.is_active
                or not self.recorded_by.is_approved
            ):
                errors["recorded_by"] = (
                    "Only this station's active Owner can record deliveries."
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Fuel delivery records are immutable.")
        if self.quantity_litres is not None and self.cost_per_litre is not None:
            self.total_cost = (self.quantity_litres * self.cost_per_litre).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        self.invoice_number = self.invoice_number.strip().upper()
        self.supplier_name = self.supplier_name.strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Fuel delivery records are immutable.")

    def __str__(self):
        return f"{self.station.code} · {self.invoice_number} · {self.quantity_litres} L"


class FuelInventoryAdjustment(models.Model):
    class Type(models.TextChoices):
        LEAKAGE = "leakage", "Leakage"
        EVAPORATION = "evaporation", "Evaporation"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    station = models.ForeignKey(
        "stations.Station",
        on_delete=models.PROTECT,
        related_name="fuel_inventory_adjustments",
    )
    tank = models.ForeignKey(
        Tank,
        on_delete=models.PROTECT,
        related_name="inventory_adjustments",
    )
    adjustment_type = models.CharField(max_length=16, choices=Type.choices)
    quantity_delta = models.DecimalField(max_digits=12, decimal_places=3)
    reason = models.CharField(max_length=500)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="requested_fuel_inventory_adjustments",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="decided_fuel_inventory_adjustments",
        null=True,
        blank=True,
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(quantity_delta=0),
                name="fuel_adjustment_nonzero",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="pending",
                        decided_by__isnull=True,
                        decided_at__isnull=True,
                    )
                    | (
                        ~Q(status="pending")
                        & Q(decided_by__isnull=False)
                        & Q(decided_at__isnull=False)
                    )
                ),
                name="fuel_adjustment_decision_matches_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=("station", "status"),
                name="fuel_adjust_station_status_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.tank_id and self.station_id:
            if self.tank.station_id != self.station_id:
                errors["tank"] = "Choose a tank at this station."
        if self.requested_by_id and self.station_id:
            user = self.requested_by
            if not user.is_active or not user.is_approved:
                errors["requested_by"] = "Only active, approved staff can request adjustments."
            elif user.role == User.Role.OWNER:
                if user.pk != self.station.owner_id:
                    errors["requested_by"] = "Only this station's Owner can request adjustments."
            elif user.role == User.Role.MANAGER:
                assigned = ManagerAssignment.objects.filter(
                    employee__user=user,
                    employee__is_active=True,
                    station_id=self.station_id,
                    station__is_active=True,
                ).exists()
                if not assigned:
                    errors["requested_by"] = (
                        "Only a Manager assigned to the station can request adjustments."
                    )
            else:
                errors["requested_by"] = (
                    "Only the Owner or an assigned Manager can request adjustments."
                )
        if self.status == self.Status.PENDING:
            if self.decided_by_id or self.decided_at:
                errors["status"] = "Pending adjustments cannot have a decision recorded."
        elif self.status in (self.Status.APPROVED, self.Status.REJECTED):
            if not self.decided_by_id or not self.decided_at:
                errors["status"] = "A decision requires an approver and timestamp."
            elif (
                self.decided_by.role != User.Role.OWNER
                or self.decided_by_id != self.station.owner_id
                or not self.decided_by.is_active
                or not self.decided_by.is_approved
            ):
                errors["decided_by"] = "Only this station's active Owner can decide."
        if not (self.reason or "").strip():
            errors["reason"] = "Provide a reason for the inventory adjustment."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "station_id",
                "tank_id",
                "adjustment_type",
                "quantity_delta",
                "reason",
                "requested_by_id",
                "status",
            ).first()
            if previous is None:
                raise ValidationError("The inventory adjustment no longer exists.")
            if any(
                previous[field] != getattr(self, field)
                for field in (
                    "station_id",
                    "tank_id",
                    "adjustment_type",
                    "quantity_delta",
                    "reason",
                    "requested_by_id",
                )
            ):
                raise ValidationError("Requested inventory adjustments are immutable.")
            if (
                previous["status"] != self.Status.PENDING
                or self.status not in (self.Status.APPROVED, self.Status.REJECTED)
            ):
                raise ValidationError("An inventory adjustment can only be decided once.")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.tank.code} · {self.quantity_delta} L · {self.get_status_display()}"


class InventoryLedger(models.Model):
    class TransactionType(models.TextChoices):
        OPENING = "opening", "Opening balance"
        DELIVERY = "delivery", "Fuel delivery"
        SALE = "sale", "Fuel sales"
        TESTING_USE = "testing_use", "Testing fuel used"
        TESTING_RETURN = "testing_return", "Testing fuel returned"
        ADJUSTMENT = "adjustment", "Approved adjustment"

    tank = models.ForeignKey(
        Tank,
        on_delete=models.PROTECT,
        related_name="inventory_ledger",
    )
    transaction_type = models.CharField(
        max_length=16,
        choices=TransactionType.choices,
    )
    quantity_delta = models.DecimalField(max_digits=12, decimal_places=3)
    delivery = models.OneToOneField(
        FuelDelivery,
        on_delete=models.PROTECT,
        related_name="ledger_entry",
        null=True,
        blank=True,
    )
    report = models.ForeignKey(
        "duties.DutyReport",
        on_delete=models.PROTECT,
        related_name="fuel_inventory_entries",
        null=True,
        blank=True,
    )
    adjustment = models.OneToOneField(
        FuelInventoryAdjustment,
        on_delete=models.PROTECT,
        related_name="ledger_entry",
        null=True,
        blank=True,
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="fuel_inventory_transactions",
    )
    notes = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(quantity_delta=0),
                name="inventory_ledger_nonzero",
            ),
            models.UniqueConstraint(
                fields=("report", "tank", "transaction_type"),
                condition=Q(report__isnull=False),
                name="inventory_report_tank_type_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("tank", "-created_at"),
                name="inventory_tank_date_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.transaction_type == self.TransactionType.OPENING:
            valid_source = not any(
                (self.delivery_id, self.report_id, self.adjustment_id)
            )
            if self.quantity_delta < 0:
                errors["quantity_delta"] = "Opening stock cannot be negative."
        elif self.transaction_type == self.TransactionType.DELIVERY:
            valid_source = bool(self.delivery_id) and not any(
                (self.report_id, self.adjustment_id)
            )
            if self.delivery_id:
                if self.delivery.tank_id != self.tank_id:
                    errors["delivery"] = "The delivery must belong to this tank."
                if self.quantity_delta != self.delivery.quantity_litres:
                    errors["quantity_delta"] = "Delivery movement must match its invoice."
        elif self.transaction_type in (
            self.TransactionType.SALE,
            self.TransactionType.TESTING_USE,
            self.TransactionType.TESTING_RETURN,
        ):
            valid_source = bool(self.report_id) and not any(
                (self.delivery_id, self.adjustment_id)
            )
            if self.report_id and self.report.station_id != self.tank.station_id:
                errors["report"] = "The report and tank must belong to the same station."
            if self.transaction_type in (
                self.TransactionType.SALE,
                self.TransactionType.TESTING_USE,
            ) and self.quantity_delta >= 0:
                errors["quantity_delta"] = "Fuel consumption must be a negative movement."
            if (
                self.transaction_type == self.TransactionType.TESTING_RETURN
                and self.quantity_delta <= 0
            ):
                errors["quantity_delta"] = "Returned testing fuel must be positive."
        else:
            valid_source = bool(self.adjustment_id) and not any(
                (self.delivery_id, self.report_id)
            )
            if self.adjustment_id:
                if self.adjustment.tank_id != self.tank_id:
                    errors["adjustment"] = "The adjustment must belong to this tank."
                if self.adjustment.status != FuelInventoryAdjustment.Status.APPROVED:
                    errors["adjustment"] = "Only approved adjustments enter the ledger."
                if self.quantity_delta != self.adjustment.quantity_delta:
                    errors["quantity_delta"] = "Movement must match the approved adjustment."
                if self.recorded_by_id != self.adjustment.decided_by_id:
                    errors["recorded_by"] = (
                        "The adjustment approver must record its ledger movement."
                    )
        if not valid_source:
            errors["transaction_type"] = "Choose exactly one matching source record."
        if self.recorded_by_id and not self.recorded_by.is_active:
            errors["recorded_by"] = "Inactive users cannot record inventory movements."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Fuel inventory ledger entries are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Fuel inventory ledger entries are immutable.")

    def __str__(self):
        return f"{self.tank.code} · {self.quantity_delta} L · {self.get_transaction_type_display()}"


class Nozzle(models.Model):
    station = models.ForeignKey(
        "stations.Station",
        on_delete=models.PROTECT,
        related_name="nozzles",
    )
    tank = models.ForeignKey(
        Tank,
        on_delete=models.PROTECT,
        related_name="nozzles",
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
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["station__name", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=("station", "code"),
                name="nozzle_station_code_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["station", "tank"], name="nozzle_station_tank_idx"),
        ]

    def clean(self):
        if self.station_id and self.tank_id and self.tank.station_id != self.station_id:
            raise ValidationError({"tank": "Choose a tank at this station."})

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    @property
    def fuel_type(self):
        return self.tank.fuel_type

    def __str__(self):
        return f"{self.station.code} · Nozzle {self.code} ({self.tank.fuel_type.code})"


class FuelPrice(models.Model):
    station = models.ForeignKey(
        "stations.Station",
        on_delete=models.PROTECT,
        related_name="fuel_prices",
    )
    fuel_type = models.ForeignKey(
        FuelType,
        on_delete=models.PROTECT,
        related_name="prices",
    )
    price_per_litre = models.DecimalField(max_digits=10, decimal_places=2)
    effective_from = models.DateTimeField()
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_fuel_prices",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=("station", "fuel_type", "effective_from"),
                name="fuel_price_effective_time_uniq",
            ),
            models.CheckConstraint(
                condition=Q(price_per_litre__gt=0),
                name="fuel_price_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=["station", "fuel_type", "-effective_from"],
                name="fuel_price_station_date_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.station_id and self.fuel_type_id:
            if self.fuel_type.owner_id not in (None, self.station.owner_id):
                errors["fuel_type"] = "Choose a fuel type available to this Owner."
            elif not self.fuel_type.is_active:
                errors["fuel_type"] = "Choose an active fuel type."
        if self.recorded_by_id and self.station_id:
            if self.recorded_by.role != User.Role.OWNER:
                errors["recorded_by"] = "Only an Owner can record station fuel prices."
            elif self.recorded_by_id != self.station.owner_id:
                errors["recorded_by"] = "Only this station's Owner can record its prices."
            elif not self.recorded_by.is_active or not self.recorded_by.is_approved:
                errors["recorded_by"] = "An active, approved Owner must record the price."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return (
            f"{self.station.code} · {self.fuel_type.code} "
            f"₹{self.price_per_litre}/L from {self.effective_from:%Y-%m-%d %H:%M}"
        )
