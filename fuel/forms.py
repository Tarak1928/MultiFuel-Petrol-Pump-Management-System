from django import forms
from django.core.validators import RegexValidator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from stations.models import Station

from .models import (
    FuelDelivery,
    FuelInventoryAdjustment,
    FuelPrice,
    FuelType,
    Nozzle,
    Tank,
)


class UppercaseCodeField(forms.CharField):
    def to_python(self, value):
        return super().to_python(value).upper()


def available_fuel_types(owner):
    return FuelType.objects.filter(is_active=True).filter(
        Q(owner__isnull=True) | Q(owner=owner)
    )


class FuelTypeForm(forms.ModelForm):
    code = UppercaseCodeField(
        max_length=20,
        validators=[
            RegexValidator(
                regex=r"^[A-Z0-9][A-Z0-9_-]{1,19}$",
                message="Use 2–20 uppercase letters, digits, hyphens, or underscores.",
            )
        ],
        widget=forms.TextInput(
            attrs={"class": "form-control", "autocomplete": "off"}
        ),
    )

    class Meta:
        model = FuelType
        fields = ("name", "code")
        widgets = {"name": forms.TextInput(attrs={"class": "form-control"})}

    def __init__(self, owner, *args, **kwargs):
        self.owner = owner
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        if FuelType.objects.filter(code=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A fuel type with this code already exists.")
        return code

    @transaction.atomic
    def save(self, commit=True):
        fuel_type = super().save(commit=False)
        fuel_type.owner = self.owner
        if commit:
            fuel_type.save()
        return fuel_type


class TankForm(forms.ModelForm):
    code = UppercaseCodeField(
        max_length=20,
        validators=[
            RegexValidator(
                regex=r"^[A-Z0-9][A-Z0-9_-]{1,19}$",
                message="Use 2–20 uppercase letters, digits, hyphens, or underscores.",
            )
        ],
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Tank
        fields = (
            "fuel_type",
            "code",
            "capacity_litres",
            "current_stock_litres",
            "reorder_level_litres",
            "is_active",
        )
        widgets = {
            "fuel_type": forms.Select(attrs={"class": "form-select"}),
            "capacity_litres": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.001", "step": "0.001"}
            ),
            "current_stock_litres": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.001"}
            ),
            "reorder_level_litres": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.001"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, owner, station, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            instance = Tank(station=station)
            kwargs["instance"] = instance
        else:
            instance.station = station
        super().__init__(*args, **kwargs)
        self.station = station
        fuel_types = available_fuel_types(owner)
        if instance.pk and instance.fuel_type_id:
            fuel_types |= FuelType.objects.filter(pk=instance.fuel_type_id)
        self.fields["fuel_type"].queryset = fuel_types
        if instance.pk:
            self.fields["current_stock_litres"].disabled = True
            self.fields["current_stock_litres"].help_text = (
                "Current stock is read-only. Record deliveries or approved "
                "adjustments from station inventory."
            )

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        tanks = Tank.objects.filter(station=self.station, code=code)
        if self.instance.pk:
            tanks = tanks.exclude(pk=self.instance.pk)
        if tanks.exists():
            raise forms.ValidationError("A tank with this code already exists at this station.")
        return code


class NozzleForm(forms.ModelForm):
    code = UppercaseCodeField(
        max_length=20,
        validators=[
            RegexValidator(
                regex=r"^[A-Z0-9][A-Z0-9_-]{1,19}$",
                message="Use 2–20 uppercase letters, digits, hyphens, or underscores.",
            )
        ],
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Nozzle
        fields = ("tank", "code", "is_active")
        widgets = {
            "tank": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, station, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            instance = Nozzle(station=station)
            kwargs["instance"] = instance
        else:
            instance.station = station
        super().__init__(*args, **kwargs)
        self.station = station
        tanks = Tank.objects.filter(station=station, is_active=True)
        if instance.pk and instance.tank_id:
            tanks |= Tank.objects.filter(pk=instance.tank_id)
        self.fields["tank"].queryset = tanks.select_related("fuel_type")

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        nozzles = Nozzle.objects.filter(station=self.station, code=code)
        if self.instance.pk:
            nozzles = nozzles.exclude(pk=self.instance.pk)
        if nozzles.exists():
            raise forms.ValidationError(
                "A nozzle with this code already exists at this station."
            )
        return code


class FuelPriceForm(forms.ModelForm):
    effective_from = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"class": "form-control", "type": "datetime-local"},
        ),
    )

    class Meta:
        model = FuelPrice
        fields = ("fuel_type", "price_per_litre", "effective_from")
        widgets = {
            "fuel_type": forms.Select(attrs={"class": "form-select"}),
            "price_per_litre": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
            ),
        }

    def __init__(self, owner, station, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            instance = FuelPrice(station=station, recorded_by=owner)
            kwargs["instance"] = instance
        else:
            instance.station = station
            instance.recorded_by = owner
        super().__init__(*args, **kwargs)
        self.station = station
        self.owner = owner
        self.fields["fuel_type"].queryset = available_fuel_types(owner)
        if not self.is_bound and not instance.pk:
            self.initial.setdefault("effective_from", timezone.localtime())

    def clean(self):
        cleaned_data = super().clean()
        fuel_type = cleaned_data.get("fuel_type")
        effective_from = cleaned_data.get("effective_from")
        if fuel_type:
            if fuel_type.owner_id not in (None, self.owner.pk):
                self.add_error("fuel_type", "Choose a fuel type available to this Owner.")
            if not fuel_type.is_active:
                self.add_error("fuel_type", "Choose an active fuel type.")
        if fuel_type and effective_from:
            existing_prices = FuelPrice.objects.filter(
                station=self.station,
                fuel_type=fuel_type,
                effective_from=effective_from,
            )
            if self.instance.pk:
                existing_prices = existing_prices.exclude(pk=self.instance.pk)
            if existing_prices.exists():
                self.add_error(
                    "effective_from",
                    "A price for this fuel type is already recorded at that time.",
                )
        return cleaned_data

    @transaction.atomic
    def save(self, commit=True):
        price = super().save(commit=False)
        price.station = self.station
        price.recorded_by = self.owner
        if commit:
            price.save()
        return price


class FuelDeliveryForm(forms.ModelForm):
    received_at = forms.DateTimeField(
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"class": "form-control", "type": "datetime-local"},
        ),
    )

    class Meta:
        model = FuelDelivery
        fields = (
            "tank",
            "invoice_number",
            "invoice_line_number",
            "supplier_name",
            "supplier_gstin",
            "supplier_phone",
            "supplier_address",
            "invoice_date",
            "received_at",
            "quantity_litres",
            "cost_per_litre",
        )
        widgets = {
            "tank": forms.Select(attrs={"class": "form-select"}),
            "invoice_number": forms.TextInput(attrs={"class": "form-control"}),
            "invoice_line_number": forms.NumberInput(
                attrs={"class": "form-control", "min": "1", "step": "1"}
            ),
            "supplier_name": forms.TextInput(attrs={"class": "form-control"}),
            "supplier_gstin": forms.TextInput(attrs={"class": "form-control"}),
            "supplier_phone": forms.TextInput(attrs={"class": "form-control"}),
            "supplier_address": forms.TextInput(attrs={"class": "form-control"}),
            "invoice_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
            "quantity_litres": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.001", "step": "0.001"}
            ),
            "cost_per_litre": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
        }

    def __init__(self, station, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.station = station
        self.fields["tank"].queryset = Tank.objects.filter(
            station=station,
            is_active=True,
        ).select_related("fuel_type")
        self.fields["invoice_date"].input_formats = ["%Y-%m-%d"]
        if not self.is_bound:
            self.initial.setdefault("received_at", timezone.localtime())

    def clean_invoice_number(self):
        return self.cleaned_data["invoice_number"].strip().upper()

    def clean(self):
        cleaned_data = super().clean()
        tank = cleaned_data.get("tank")
        quantity = cleaned_data.get("quantity_litres")
        invoice_number = cleaned_data.get("invoice_number")
        line_number = cleaned_data.get("invoice_line_number")
        if invoice_number and line_number:
            duplicates = FuelDelivery.objects.filter(
                station=self.station,
                invoice_number__iexact=invoice_number,
                invoice_line_number=line_number,
            )
            if self.instance.pk:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                self.add_error(
                    "invoice_number",
                    "This invoice line has already been recorded at the station.",
                )
        if tank and quantity and tank.current_stock_litres + quantity > tank.capacity_litres:
            self.add_error(
                "quantity_litres",
                "This delivery would exceed the tank's remaining capacity.",
            )
        return cleaned_data


class FuelInventoryAdjustmentForm(forms.Form):
    tank = forms.ModelChoiceField(
        queryset=Tank.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    adjustment_type = forms.ChoiceField(
        choices=FuelInventoryAdjustment.Type.choices,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity_delta = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.001"}
        ),
        help_text="Use a negative amount for stock loss and a positive amount for stock gain.",
    )
    reason = forms.CharField(
        max_length=500,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "maxlength": 500}
        ),
    )

    def __init__(self, station, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tank"].queryset = Tank.objects.filter(
            station=station,
            is_active=True,
        ).select_related("fuel_type")

    def clean_quantity_delta(self):
        quantity_delta = self.cleaned_data["quantity_delta"]
        if quantity_delta == 0:
            raise forms.ValidationError("An inventory adjustment must be non-zero.")
        return quantity_delta

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get("adjustment_type")
            in (
                FuelInventoryAdjustment.Type.LEAKAGE,
                FuelInventoryAdjustment.Type.EVAPORATION,
            )
            and cleaned_data.get("quantity_delta", 0) > 0
        ):
            self.add_error(
                "quantity_delta",
                "Leakage and evaporation adjustments must reduce tank stock.",
            )
        return cleaned_data


class FuelInventoryAdjustmentDecisionForm(forms.Form):
    action = forms.ChoiceField(
        choices=(("approve", "Approve"), ("reject", "Reject")),
        widget=forms.HiddenInput,
    )
    decision_note = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 2, "maxlength": 500}
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get("action") == "reject"
            and not (cleaned_data.get("decision_note") or "").strip()
        ):
            self.add_error("decision_note", "Give a reason when rejecting an adjustment.")
        return cleaned_data
