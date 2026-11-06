import uuid
from datetime import timedelta

from django import forms
from django.core.validators import RegexValidator
from django.db.models import Q, Sum

from duties.models import PaymentCollection
from duties.pricing import effective_price_time_for_report
from fuel.models import FuelPrice, FuelType

from .models import (
    Advance,
    CreditCustomer,
    CreditRepayment,
    CreditSale,
    OTPTransaction,
)


class UppercaseCustomerCodeField(forms.CharField):
    def to_python(self, value):
        return super().to_python(value).strip().upper()


class CreditCustomerForm(forms.ModelForm):
    customer_code = UppercaseCustomerCodeField(
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
        model = CreditCustomer
        fields = (
            "customer_code",
            "name",
            "phone",
            "address",
            "vehicle_number",
            "vehicle_type",
            "credit_limit",
            "credit_days",
            "is_active",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "vehicle_number": forms.TextInput(
                attrs={"class": "form-control", "autocomplete": "off"}
            ),
            "vehicle_type": forms.TextInput(attrs={"class": "form-control"}),
            "credit_limit": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "credit_days": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "1"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, station, *args, **kwargs):
        self.station = station
        instance = kwargs.get("instance")
        if instance is None:
            kwargs["instance"] = CreditCustomer(station=station)
        else:
            instance.station = station
        super().__init__(*args, **kwargs)

    def clean_customer_code(self):
        code = self.cleaned_data["customer_code"].strip().upper()
        customers = CreditCustomer.objects.filter(
            station=self.station,
            customer_code=code,
        )
        if self.instance.pk:
            customers = customers.exclude(pk=self.instance.pk)
        if customers.exists():
            raise forms.ValidationError(
                "A customer with this code already exists at the station."
            )
        return code

    def clean(self):
        cleaned_data = super().clean()
        limit = cleaned_data.get("credit_limit")
        if (
            self.instance.pk
            and limit is not None
            and limit < self.instance.outstanding_balance
        ):
            self.add_error(
                "credit_limit",
                "Credit limit cannot be lower than the customer's outstanding balance.",
            )
        return cleaned_data

    def save(self, commit=True):
        customer = super().save(commit=False)
        customer.station = self.station
        if commit:
            customer.save()
        return customer


class CreditSaleForm(forms.ModelForm):
    submission_token = forms.UUIDField(
        initial=uuid.uuid4,
        widget=forms.HiddenInput,
    )

    class Meta:
        model = CreditSale
        fields = ("customer", "fuel_type", "litres")
        widgets = {
            "customer": forms.Select(attrs={"class": "form-select"}),
            "fuel_type": forms.Select(attrs={"class": "form-select"}),
            "litres": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.001", "step": "0.001"}
            ),
        }

    def __init__(self, report, user, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            kwargs["instance"] = CreditSale(
                report=report,
                recorded_by=user,
            )
        else:
            instance.report = report
            instance.recorded_by = user
        super().__init__(*args, **kwargs)
        self.report = report
        if self.instance.pk:
            self.fields["submission_token"].initial = self.instance.submission_token
        self.fields["customer"].queryset = CreditCustomer.objects.filter(
            station=report.station,
            is_active=True,
        )
        self.fields["fuel_type"].queryset = FuelType.objects.filter(
            Q(owner__isnull=True) | Q(owner_id=report.station.owner_id),
            is_active=True,
        )

    def clean(self):
        cleaned_data = super().clean()
        customer = cleaned_data.get("customer")
        fuel_type = cleaned_data.get("fuel_type")
        if customer and fuel_type:
            effective_from = effective_price_time_for_report(self.report)
            price = (
                FuelPrice.objects.filter(
                    station=self.report.station,
                    fuel_type=fuel_type,
                    effective_from__lte=effective_from,
                )
                .order_by("-effective_from", "-pk")
                .first()
            )
            if price is None:
                self.add_error(
                    "fuel_type",
                    "No fuel price is effective for this type at the shift start.",
                )
            else:
                self.instance.report = self.report
                self.instance.customer = customer
                self.instance.fuel_type = fuel_type
                self.instance.fuel_price = price
                self.instance.price_per_litre = price.price_per_litre
                self.instance.vehicle_number = customer.vehicle_number
                self.instance.due_date = self.report.report_date + timedelta(
                    days=customer.credit_days
                )
        return cleaned_data

    def clean_submission_token(self):
        token = self.cleaned_data["submission_token"]
        if self.instance.pk and token != self.instance.submission_token:
                    raise forms.ValidationError(
                        "The submission token cannot be changed when editing a sale."
                    )
        return token

    def save(self, commit=True):
        sale = super().save(commit=False)
        sale.report = self.report
        sale.recorded_by = self.instance.recorded_by
        sale.submission_token = self.cleaned_data["submission_token"]
        if commit:
            sale.save()
            collection, _ = PaymentCollection.objects.get_or_create(
                report=self.report
            )
            collection.credit_sales = self.report.credit_sales.aggregate(
                total=Sum("sale_amount")
            )["total"]
            collection.save(update_fields=("credit_sales", "updated_at"))
        return sale


class CreditRepaymentForm(forms.ModelForm):
    submission_token = forms.UUIDField(
        initial=uuid.uuid4,
        widget=forms.HiddenInput,
    )

    class Meta:
        model = CreditRepayment
        fields = ("amount", "payment_method", "reference")
        widgets = {
            "amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
            ),
            "payment_method": forms.Select(attrs={"class": "form-select"}),
            "reference": forms.TextInput(
                attrs={"class": "form-control", "maxlength": "100"}
            ),
        }

    def __init__(self, sale, user, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            kwargs["instance"] = CreditRepayment(
                sale=sale,
                received_by=user,
            )
        else:
            instance.sale = sale
            instance.received_by = user
        super().__init__(*args, **kwargs)
        self.sale = sale

    def save(self, commit=True):
        repayment = super().save(commit=False)
        repayment.sale = self.sale
        repayment.received_by = self.instance.received_by
        repayment.submission_token = self.cleaned_data["submission_token"]
        if commit:
            repayment.save()
        return repayment


class AdvanceForm(forms.ModelForm):
    class Meta:
        model = Advance
        fields = ("amount", "reference", "description")
        widgets = {
            "amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
            ),
            "reference": forms.TextInput(
                attrs={"class": "form-control", "maxlength": "100"}
            ),
            "description": forms.TextInput(
                attrs={"class": "form-control", "maxlength": "255"}
            ),
        }

    def __init__(self, report, user, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            kwargs["instance"] = Advance(report=report, recorded_by=user)
        else:
            instance.report = report
            instance.recorded_by = user
        super().__init__(*args, **kwargs)
        self.report = report
        self.user = user

    def clean_reference(self):
        reference = self.cleaned_data["reference"].strip()
        advances = Advance.objects.filter(
            reference=reference,
        )
        if self.instance.pk:
            advances = advances.exclude(pk=self.instance.pk)
        if advances.exists():
            raise forms.ValidationError(
                "This advance reference is already recorded on the report."
            )
        return reference

    def save(self, commit=True):
        advance = super().save(commit=False)
        advance.report = self.report
        advance.recorded_by = self.user
        if commit:
            advance.save()
            collection, _ = PaymentCollection.objects.get_or_create(
                report=self.report
            )
            collection.advances = self.report.advances.aggregate(
                total=Sum("amount")
            )["total"]
            collection.save(update_fields=("advances", "updated_at"))
        return advance


class OTPTransactionForm(forms.ModelForm):
    class Meta:
        model = OTPTransaction
        fields = ("transaction_reference", "amount", "provider")
        widgets = {
            "transaction_reference": forms.TextInput(
                attrs={"class": "form-control", "maxlength": "100"}
            ),
            "amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
            ),
            "provider": forms.TextInput(
                attrs={"class": "form-control", "maxlength": "60"}
            ),
        }

    def __init__(self, report, user, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            kwargs["instance"] = OTPTransaction(report=report, recorded_by=user)
        else:
            instance.report = report
            instance.recorded_by = user
        super().__init__(*args, **kwargs)
        self.report = report
        self.user = user

    def clean(self):
        cleaned_data = super().clean()
        reference = cleaned_data.get("transaction_reference", "").strip()
        provider = cleaned_data.get("provider", "").strip()
        if not reference:
            return cleaned_data
        transactions = OTPTransaction.objects.filter(
            provider=provider,
            transaction_reference=reference,
        )
        if self.instance.pk:
            transactions = transactions.exclude(pk=self.instance.pk)
        if transactions.exists():
            self.add_error(
                "transaction_reference",
                "This transaction reference is already recorded at the station.",
            )
        return cleaned_data

    def save(self, commit=True):
        otp_transaction = super().save(commit=False)
        otp_transaction.report = self.report
        otp_transaction.recorded_by = self.user
        if commit:
            otp_transaction.save()
            collection, _ = PaymentCollection.objects.get_or_create(
                report=self.report
            )
            collection.otp = self.report.otp_transactions.aggregate(
                total=Sum("amount")
            )["total"]
            collection.save(update_fields=("otp", "updated_at"))
        return otp_transaction
