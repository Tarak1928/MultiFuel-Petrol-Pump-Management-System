from django import forms
from django.core.validators import RegexValidator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from stations.models import Station

from .models import FuelPrice, FuelType, Nozzle, Tank


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
