import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.forms import BaseInlineFormSet, inlineformset_factory

from fuel.models import Nozzle

from .models import (
    DutyFuelEntry,
    DutyLubricantEntry,
    DutyReport,
    Lubricant,
    Shift,
)
from .pricing import effective_price_for_report


class UppercaseCodeField(forms.CharField):
    def to_python(self, value):
        return super().to_python(value).strip().upper()


class ShiftForm(forms.ModelForm):
    class Meta:
        model = Shift
        fields = ("name", "start_time", "end_time", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "start_time": forms.TimeInput(
                attrs={"class": "form-control", "type": "time"},
                format="%H:%M",
            ),
            "end_time": forms.TimeInput(
                attrs={"class": "form-control", "type": "time"},
                format="%H:%M",
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, station, *args, **kwargs):
        self.station = station
        instance = kwargs.get("instance")
        if instance is None:
            kwargs["instance"] = Shift(station=station)
        else:
            instance.station = station
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        shifts = Shift.objects.filter(station=self.station, name__iexact=name)
        if self.instance.pk:
            shifts = shifts.exclude(pk=self.instance.pk)
        if shifts.exists():
            raise forms.ValidationError("A shift with this name already exists at this station.")
        return name

    def save(self, commit=True):
        shift = super().save(commit=False)
        shift.station = self.station
        if commit:
            shift.save()
        return shift


class DutyReportForm(forms.ModelForm):
    notes = forms.CharField(
        required=False,
        max_length=2000,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 4, "maxlength": 2000}
        ),
    )

    class Meta:
        model = DutyReport
        fields = ("shift", "report_date", "notes")
        widgets = {
            "shift": forms.Select(attrs={"class": "form-select"}),
            "report_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
        }

    def __init__(self, station, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            instance = DutyReport(station=station)
            kwargs["instance"] = instance
        else:
            instance.station = station
        super().__init__(*args, **kwargs)
        self.station = station
        shifts = Shift.objects.filter(station=station, is_active=True)
        if instance.pk and instance.shift_id:
            shifts |= Shift.objects.filter(pk=instance.shift_id)
        self.fields["shift"].queryset = shifts
        if not shifts.exists():
            self.fields["shift"].help_text = (
                "No active shifts are configured. Ask your Owner to set up a shift."
            )
        self.fields["report_date"].input_formats = ["%Y-%m-%d"]
        self.fields["report_date"].initial = instance.report_date

    def clean(self):
        cleaned_data = super().clean()
        if self.instance.pk and self.instance.fuel_entries.exists():
            saved_values = DutyReport.objects.filter(pk=self.instance.pk).values(
                "report_date",
                "shift_id",
            ).get()
            if cleaned_data.get("report_date") != saved_values["report_date"]:
                self.add_error(
                    "report_date",
                    "A report date cannot change after nozzle readings are added.",
                )
            shift = cleaned_data.get("shift")
            if shift and shift.pk != saved_values["shift_id"]:
                self.add_error(
                    "shift",
                    "A report shift cannot change after nozzle readings are added.",
                )
        return cleaned_data


class DutyReportCreateForm(DutyReportForm):
    submission_token = forms.UUIDField(
        initial=uuid.uuid4,
        widget=forms.HiddenInput,
    )


class DutyFuelEntryForm(forms.ModelForm):
    nozzle = forms.ModelChoiceField(
        queryset=Nozzle.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    opening_reading = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "min": "0", "step": "0.001"}
        ),
    )
    closing_reading = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "min": "0", "step": "0.001"}
        ),
    )
    testing_litres = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=0,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "min": "0", "step": "0.001"}
        ),
        initial=0,
    )

    class Meta:
        model = DutyFuelEntry
        fields = ("nozzle", "opening_reading", "closing_reading", "testing_litres")

    def __init__(self, station, report, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.station = station
        self.report = report
        nozzles = Nozzle.objects.filter(
            station=station,
            is_active=True,
        ).select_related("tank", "tank__fuel_type")
        if self.instance.pk and self.instance.nozzle_id:
            nozzles |= Nozzle.objects.filter(pk=self.instance.nozzle_id).select_related(
                "tank",
                "tank__fuel_type",
            )
        self.fields["nozzle"].queryset = nozzles
        self.price_record = None

    def clean(self):
        cleaned_data = super().clean()
        nozzle = cleaned_data.get("nozzle")
        if not nozzle:
            return cleaned_data
        if not nozzle.is_active and (
            not self.instance.pk or self.instance.nozzle_id != nozzle.pk
        ):
            self.add_error("nozzle", "Choose an active nozzle.")
            return cleaned_data

        if (
            self.instance.pk
            and self.instance.nozzle_id == nozzle.pk
            and self.instance.fuel_price_id
        ):
            self.price_record = self.instance.fuel_price
        else:
            self.price_record = effective_price_for_report(self.report, nozzle)
        if not self.price_record:
            self.add_error(
                "nozzle",
                "No fuel price is effective for this nozzle at the shift start time.",
            )
        else:
            self.instance.report = self.report
            self.instance.nozzle = nozzle
            self.instance.fuel_price = self.price_record
            self.instance.price_per_litre = self.price_record.price_per_litre
        return cleaned_data

    def save(self, commit=True):
        entry = super().save(commit=False)
        entry.report = self.report
        if self.price_record:
            entry.fuel_price = self.price_record
            entry.price_per_litre = self.price_record.price_per_litre
        if commit:
            entry.save()
        return entry


class BaseDutyFuelEntryFormSet(BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs.update(station=self.instance.station, report=self.instance)
        return kwargs

    def clean(self):
        seen_nozzles = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if form.cleaned_data.get("DELETE"):
                continue
            nozzle = form.cleaned_data.get("nozzle")
            if not nozzle:
                continue
            if nozzle.pk in seen_nozzles:
                raise ValidationError("A nozzle can only be recorded once per duty report.")
            seen_nozzles.add(nozzle.pk)
        super().clean()


DutyFuelEntryFormSet = inlineformset_factory(
    DutyReport,
    DutyFuelEntry,
    form=DutyFuelEntryForm,
    formset=BaseDutyFuelEntryFormSet,
    fields=("nozzle", "opening_reading", "closing_reading", "testing_litres"),
    extra=1,
    can_delete=True,
    max_num=50,
    validate_max=True,
)


class LubricantForm(forms.ModelForm):
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
        model = Lubricant
        fields = ("code", "name", "unit", "current_stock", "selling_price", "is_active")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "unit": forms.TextInput(attrs={"class": "form-control"}),
            "current_stock": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.001"}
            ),
            "selling_price": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, station, *args, **kwargs):
        self.station = station
        instance = kwargs.get("instance")
        if instance is None:
            kwargs["instance"] = Lubricant(station=station)
        else:
            instance.station = station
        super().__init__(*args, **kwargs)

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        products = Lubricant.objects.filter(station=self.station, code=code)
        if self.instance.pk:
            products = products.exclude(pk=self.instance.pk)
        if products.exists():
            raise forms.ValidationError(
                "A lubricant with this code already exists at this station."
            )
        return code

    def save(self, commit=True):
        product = super().save(commit=False)
        product.station = self.station
        if commit:
            product.save()
        return product


class DutyLubricantEntryForm(forms.ModelForm):
    lubricant = forms.ModelChoiceField(
        queryset=Lubricant.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    quantity = forms.DecimalField(
        max_digits=12,
        decimal_places=3,
        min_value=0.001,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "min": "0.001", "step": "0.001"}
        ),
    )

    class Meta:
        model = DutyLubricantEntry
        fields = ("lubricant", "quantity")

    def __init__(self, station, report, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.station = station
        self.report = report
        products = Lubricant.objects.filter(
            station=station,
            is_active=True,
        )
        if self.instance.pk and self.instance.lubricant_id:
            products |= Lubricant.objects.filter(pk=self.instance.lubricant_id)
        self.fields["lubricant"].queryset = products
        self.fields["lubricant"].label_from_instance = (
            lambda product: (
                f"{product.name} · {product.current_stock} {product.unit} in stock"
            )
        )
        self.unit_price = None

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get("lubricant")
        if not product:
            return cleaned_data
        if not product.is_active and (
            not self.instance.pk or self.instance.lubricant_id != product.pk
        ):
            self.add_error("lubricant", "Choose an active product.")
            return cleaned_data
        if (
            self.instance.pk
            and self.instance.lubricant_id == product.pk
        ):
            self.unit_price = self.instance.unit_price
        else:
            self.unit_price = product.selling_price
        self.instance.report = self.report
        self.instance.lubricant = product
        self.instance.unit_price = self.unit_price
        return cleaned_data

    def save(self, commit=True):
        entry = super().save(commit=False)
        entry.report = self.report
        entry.unit_price = self.unit_price
        if commit:
            entry.save()
        return entry


class BaseDutyLubricantFormSet(BaseInlineFormSet):
    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs.update(station=self.instance.station, report=self.instance)
        return kwargs

    def clean(self):
        seen_products = set()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            product = form.cleaned_data.get("lubricant")
            if not product:
                continue
            if product.pk in seen_products:
                raise ValidationError(
                    "A lubricant product can only be recorded once per duty report."
                )
            seen_products.add(product.pk)
        super().clean()


DutyLubricantEntryFormSet = inlineformset_factory(
    DutyReport,
    DutyLubricantEntry,
    form=DutyLubricantEntryForm,
    formset=BaseDutyLubricantFormSet,
    fields=("lubricant", "quantity"),
    extra=1,
    can_delete=True,
    max_num=50,
    validate_max=True,
)
