import uuid
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import F, Q, Sum

from accounts.models import User
from duties.models import DutyFuelEntry, DutyReport
from employees.models import ManagerAssignment
from fuel.models import FuelPrice, FuelType
from stations.models import Station

from duties.pricing import effective_price_time_for_report


class CreditCustomer(models.Model):
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="credit_customers",
    )
    customer_code = models.CharField(
        max_length=20,
        validators=[
            RegexValidator(
                regex=r"^[A-Z0-9][A-Z0-9_-]{1,19}$",
                message="Use 2–20 uppercase letters, digits, hyphens, or underscores.",
            )
        ],
    )
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    vehicle_number = models.CharField(max_length=20, blank=True)
    vehicle_type = models.CharField(max_length=60, blank=True)
    credit_limit = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    credit_days = models.PositiveSmallIntegerField(default=14)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "customer_code"]
        constraints = [
            models.UniqueConstraint(
                fields=("station", "customer_code"),
                name="credit_cust_station_code_uq",
            ),
            models.CheckConstraint(
                condition=Q(credit_limit__gte=0),
                name="credit_customer_limit_nonneg",
            ),
        ]
        indexes = [
            models.Index(
                fields=("station", "is_active"),
                name="credit_cust_station_act_idx",
            ),
        ]

    def clean(self):
        if self.station_id and not self.station.is_active and not self.pk:
            raise ValidationError(
                {"station": "New credit customers require an active station."}
            )
        if self.credit_limit is not None and self.credit_limit < 0:
            raise ValidationError({"credit_limit": "Credit limit cannot be negative."})
        if self.credit_days is not None and self.credit_days < 0:
            raise ValidationError({"credit_days": "Credit terms cannot be negative."})

    def save(self, *args, **kwargs):
        self.customer_code = self.customer_code.strip().upper()
        self.name = self.name.strip()
        self.vehicle_number = self.vehicle_number.strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def outstanding_balance(self):
        balance = (
            self.credit_sales.annotate(
                amount_repaid=Sum("repayments__amount", default=Decimal("0.00"))
            )
            .aggregate(total=Sum(F("sale_amount") - F("amount_repaid")))["total"]
        )
        return balance or Decimal("0.00")

    @property
    def available_credit(self):
        return max(Decimal("0.00"), self.credit_limit - self.outstanding_balance)

    def __str__(self):
        return f"{self.name} ({self.customer_code})"


class CreditSale(models.Model):
    report = models.ForeignKey(
        DutyReport,
        on_delete=models.PROTECT,
        related_name="credit_sales",
    )
    customer = models.ForeignKey(
        CreditCustomer,
        on_delete=models.PROTECT,
        related_name="credit_sales",
    )
    fuel_type = models.ForeignKey(
        FuelType,
        on_delete=models.PROTECT,
        related_name="credit_sales",
    )
    fuel_price = models.ForeignKey(
        FuelPrice,
        on_delete=models.PROTECT,
        related_name="credit_sales",
    )
    litres = models.DecimalField(max_digits=12, decimal_places=3)
    price_per_litre = models.DecimalField(max_digits=10, decimal_places=2)
    sale_amount = models.DecimalField(
        max_digits=22,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    vehicle_number = models.CharField(max_length=20, blank=True)
    due_date = models.DateField()
    submission_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="recorded_credit_sales",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(litres__gt=0)
                    & Q(price_per_litre__gt=0)
                    & Q(sale_amount__gt=0)
                ),
                name="credit_sale_values_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=("customer", "due_date"),
                name="credit_sale_customer_due_idx",
            ),
            models.Index(
                fields=("report", "fuel_type"),
                name="credit_sale_report_fuel_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.report_id:
            if self.report.status not in (
                DutyReport.Status.DRAFT,
                DutyReport.Status.CORRECTION_REQUESTED,
            ):
                errors["report"] = "Credit sales can only be added to editable reports."
            if self.recorded_by_id:
                if (
                    self.recorded_by.role != User.Role.DSMS
                    or not self.recorded_by.is_active
                    or not self.recorded_by.is_approved
                    or self.report.employee.user_id != self.recorded_by_id
                ):
                    errors["recorded_by"] = (
                        "Only the report's active DSMS employee can record credit sales."
                    )
            if self.customer_id and self.customer.station_id != self.report.station_id:
                errors["customer"] = "Choose a credit customer at the report's station."
            if self.customer_id and not self.customer.is_active:
                errors["customer"] = "Choose an active credit customer."
            if self.customer_id and not self.vehicle_number:
                self.vehicle_number = self.customer.vehicle_number
            if self.customer_id and self.report_id and not self.due_date:
                self.due_date = self.report.report_date + timedelta(
                    days=self.customer.credit_days
                )
            if self.fuel_type_id and self.fuel_type.owner_id not in (
                None,
                self.report.station.owner_id,
            ):
                errors["fuel_type"] = "Choose fuel available at the report's station."
            if self.fuel_type_id and not self.fuel_type.is_active:
                errors["fuel_type"] = "Choose an active fuel type."
            if self.fuel_price_id and self.fuel_type_id:
                if self.fuel_price.station_id != self.report.station_id:
                    errors["fuel_price"] = "The price must belong to this station."
                if self.fuel_price.fuel_type_id != self.fuel_type_id:
                    errors["fuel_price"] = "The price must match the selected fuel type."
                if (
                    self.fuel_price.effective_from
                    > effective_price_time_for_report(self.report)
                ):
                    errors["fuel_price"] = "The price must be effective at shift start."
                if self.price_per_litre != self.fuel_price.price_per_litre:
                    errors["price_per_litre"] = "The price must match the saved price history."
            if (
                self.litres is not None
                and self.price_per_litre is not None
                and self.litres > 0
                and self.price_per_litre > 0
            ):
                self.sale_amount = (self.litres * self.price_per_litre).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )
            if (
                self.customer_id
                and self.sale_amount is not None
                and self.sale_amount > 0
            ):
                other_sales = CreditSale.objects.filter(
                    customer_id=self.customer_id
                ).exclude(pk=self.pk)
                outstanding = (
                    other_sales.annotate(
                        amount_repaid=Sum(
                            "repayments__amount",
                            default=Decimal("0.00"),
                        )
                    )
                    .aggregate(
                        total=Sum(F("sale_amount") - F("amount_repaid"))
                    )["total"]
                    or Decimal("0.00")
                )
                if outstanding + self.sale_amount > self.customer.credit_limit:
                    errors["customer"] = (
                        "This sale would exceed the customer's available credit limit."
                    )
            if self.fuel_type_id and self.litres is not None:
                sold_litres = (
                    DutyFuelEntry.objects.filter(
                        report_id=self.report_id,
                        nozzle__tank__fuel_type_id=self.fuel_type_id,
                    )
                    .aggregate(total=Sum("actual_sale_litres"))["total"]
                    or Decimal("0.000")
                )
                existing_litres = (
                    CreditSale.objects.filter(
                        report_id=self.report_id,
                        fuel_type_id=self.fuel_type_id,
                    )
                    .exclude(pk=self.pk)
                    .aggregate(total=Sum("litres"))["total"]
                    or Decimal("0.000")
                )
                if existing_litres + self.litres > sold_litres:
                    errors["litres"] = (
                        "Credit litres cannot exceed this fuel type's recorded report sales."
                    )
                sold_amount = (
                    DutyFuelEntry.objects.filter(
                        report_id=self.report_id,
                        nozzle__tank__fuel_type_id=self.fuel_type_id,
                    )
                    .aggregate(total=Sum("sale_amount"))["total"]
                    or Decimal("0.00")
                )
                existing_amount = (
                    CreditSale.objects.filter(
                        report_id=self.report_id,
                        fuel_type_id=self.fuel_type_id,
                    )
                    .exclude(pk=self.pk)
                    .aggregate(total=Sum("sale_amount"))["total"]
                    or Decimal("0.00")
                )
                if existing_amount + self.sale_amount > sold_amount:
                    errors["litres"] = (
                        "Credit sale value cannot exceed this fuel type's recorded report sales."
                    )
        if errors:
            raise ValidationError(errors)

    @property
    def amount_repaid(self):
        return (
            self.repayments.aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )

    @property
    def outstanding_amount(self):
        return max(Decimal("0.00"), self.sale_amount - self.amount_repaid)

    @property
    def is_overdue(self):
        from django.utils import timezone

        return self.outstanding_amount > 0 and self.due_date < timezone.localdate()

    def save(self, *args, **kwargs):
        self.vehicle_number = self.vehicle_number.strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.customer} · ₹{self.sale_amount}"


class CreditRepayment(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        UPI = "upi", "UPI"
        DEBIT_CARD = "debit_card", "Debit card"
        CREDIT_CARD = "credit_card", "Credit card"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        OTHER = "other", "Other"

    sale = models.ForeignKey(
        CreditSale,
        on_delete=models.PROTECT,
        related_name="repayments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    reference = models.CharField(max_length=100, blank=True)
    submission_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    received_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="received_credit_repayments",
    )
    repaid_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-repaid_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="credit_repayment_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=("sale", "repaid_at"),
                name="credit_repayment_sale_date_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.sale_id:
            if self.sale.report.status not in (
                DutyReport.Status.SUBMITTED,
                DutyReport.Status.APPROVED,
            ):
                errors["sale"] = "Repayments require a submitted duty report."
            existing = CreditRepayment.objects.filter(sale_id=self.sale_id).exclude(
                pk=self.pk
            )
            paid = existing.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            if self.amount is not None and paid + self.amount > self.sale.sale_amount:
                errors["amount"] = "Repayment cannot exceed the remaining sale balance."
        if self.received_by_id and self.sale_id:
            user = self.received_by
            if not user.is_active or not user.is_approved:
                errors["received_by"] = "Only active, approved staff can record repayments."
            elif user.role == User.Role.OWNER:
                if user.pk != self.sale.report.station.owner_id:
                    errors["received_by"] = "Only the station Owner can record this repayment."
            elif user.role == User.Role.MANAGER:
                assigned = ManagerAssignment.objects.filter(
                    employee__user=user,
                    employee__is_active=True,
                    station_id=self.sale.report.station_id,
                    station__is_active=True,
                ).exists()
                if not assigned:
                    errors["received_by"] = (
                        "Only an assigned Manager can record this repayment."
                    )
            else:
                errors["received_by"] = "Only the Owner or an assigned Manager can record repayments."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"₹{self.amount} repayment for {self.sale}"


class Advance(models.Model):
    report = models.ForeignKey(
        DutyReport,
        on_delete=models.PROTECT,
        related_name="advances",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="recorded_report_advances",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=("reference",),
                name="advance_reference_unique",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="report_advance_positive",
            ),
        ]

    def clean(self):
        errors = {}
        if self.report_id and self.report.status not in (
            DutyReport.Status.DRAFT,
            DutyReport.Status.CORRECTION_REQUESTED,
        ):
            errors["report"] = "Advances can only be added to editable reports."
        if self.recorded_by_id and self.report_id:
            if (
                self.recorded_by.role != User.Role.DSMS
                or not self.recorded_by.is_active
                or not self.recorded_by.is_approved
                or not self.report.employee.user_id == self.recorded_by_id
            ):
                errors["recorded_by"] = "Only the report's active DSMS employee can record advances."
        if not (self.reference or "").strip():
            errors["reference"] = "Enter an advance reference."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.reference = self.reference.strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"₹{self.amount} advance · {self.report}"


class OTPTransaction(models.Model):
    report = models.ForeignKey(
        DutyReport,
        on_delete=models.PROTECT,
        related_name="otp_transactions",
    )
    transaction_reference = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    provider = models.CharField(max_length=60, blank=True)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="recorded_otp_transactions",
    )
    confirmed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-confirmed_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "transaction_reference"),
                name="otp_provider_ref_unique",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="report_otp_amount_positive",
            ),
        ]

    def clean(self):
        errors = {}
        if self.report_id and self.report.status not in (
            DutyReport.Status.DRAFT,
            DutyReport.Status.CORRECTION_REQUESTED,
        ):
            errors["report"] = "OTP transactions can only be added to editable reports."
        if self.recorded_by_id and self.report_id:
            if (
                self.recorded_by.role != User.Role.DSMS
                or not self.recorded_by.is_active
                or not self.recorded_by.is_approved
                or self.report.employee.user_id != self.recorded_by_id
            ):
                errors["recorded_by"] = "Only the report's active DSMS employee can record OTP transactions."
        if not (self.transaction_reference or "").strip():
            errors["transaction_reference"] = "Enter the transaction reference."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.transaction_reference = self.transaction_reference.strip()
        self.provider = self.provider.strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.transaction_reference} · ₹{self.amount}"
