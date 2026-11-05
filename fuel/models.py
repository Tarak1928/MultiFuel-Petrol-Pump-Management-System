from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q

from accounts.models import User


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
