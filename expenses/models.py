from datetime import date
from calendar import monthrange
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from accounts.models import User
from employees.models import Employee, ManagerAssignment, Payroll
from stations.models import Station


class Expense(models.Model):
    class Category(models.TextChoices):
        ELECTRICITY = "electricity", "Electricity"
        MAINTENANCE = "maintenance", "Maintenance"
        SALARY = "salary", "Salary"
        TRANSPORT = "transport", "Transport"
        EQUIPMENT = "equipment", "Equipment"
        CLEANING = "cleaning", "Cleaning"
        MISCELLANEOUS = "miscellaneous", "Miscellaneous"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="expenses",
    )
    category = models.CharField(max_length=16, choices=Category.choices)
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    expense_date = models.DateField()
    payee = models.CharField(max_length=120, blank=True)
    reference = models.CharField(max_length=50, blank=True)
    description = models.CharField(max_length=500)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_station_expenses",
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="decided_station_expenses",
        blank=True,
        null=True,
    )
    decided_at = models.DateTimeField(blank=True, null=True)
    decision_note = models.CharField(max_length=500, blank=True)
    payroll = models.OneToOneField(
        Payroll,
        on_delete=models.PROTECT,
        related_name="salary_expense",
        blank=True,
        null=True,
    )

    class Meta:
        ordering = ["-expense_date", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="station_expense_amount_positive",
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
                name="station_expense_decision_matches_status",
            ),
            models.UniqueConstraint(
                fields=("station", "reference"),
                condition=~Q(reference=""),
                name="station_expense_reference_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=("station", "-expense_date", "status"),
                name="exp_station_date_status_idx",
            ),
            models.Index(
                fields=("category", "expense_date"),
                name="expense_category_date_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.station_id and not self.station.is_active and not self.pk:
            errors["station"] = "New expenses require an active station."
        if self.recorded_by_id and self.station_id:
            if not self.recorded_by.is_active or not self.recorded_by.is_approved:
                errors["recorded_by"] = "Only active, approved staff can record expenses."
            elif self.recorded_by.role == User.Role.OWNER:
                if self.recorded_by_id != self.station.owner_id:
                    errors["recorded_by"] = "Only this station's Owner can record expenses."
            elif self.recorded_by.role == User.Role.MANAGER:
                if not ManagerAssignment.objects.filter(
                    employee__user=self.recorded_by,
                    employee__is_active=True,
                    station=self.station,
                    station__is_active=True,
                ).exists():
                    errors["recorded_by"] = (
                        "Only a Manager assigned to this station can record expenses."
                    )
            elif self.recorded_by.role == User.Role.DSMS:
                if not Employee.objects.filter(
                    user=self.recorded_by,
                    is_active=True,
                    assigned_station_id=self.station_id,
                ).exists():
                    errors["recorded_by"] = (
                        "DSMS staff can record expenses only at their assigned station."
                    )
            else:
                errors["recorded_by"] = (
                    "Only the Owner, an assigned Manager, or assigned DSMS staff can record expenses."
                )

        if self.status == self.Status.PENDING:
            if self.decided_by_id or self.decided_at:
                errors["status"] = "Pending expenses cannot have a decision recorded."
            if self.recorded_by_id and self.recorded_by.role == User.Role.OWNER:
                errors["status"] = "Owner-entered expenses must be approved immediately."
        elif self.status in (self.Status.APPROVED, self.Status.REJECTED):
            if not self.decided_by_id or not self.decided_at:
                errors["status"] = "An expense decision requires an Owner and timestamp."
            elif (
                self.decided_by.role != User.Role.OWNER
                or not self.station_id
                or self.decided_by_id != self.station.owner_id
                or not self.decided_by.is_active
                or not self.decided_by.is_approved
            ):
                errors["decided_by"] = "Only this station's active Owner can decide expenses."
            if (
                self.status == self.Status.APPROVED
                and self.recorded_by_id
                and self.recorded_by.role == User.Role.OWNER
                and self.decided_by_id != self.recorded_by_id
            ):
                errors["decided_by"] = (
                    "Owner-entered expenses must be approved by the recording Owner."
                )

        if self.payroll_id:
            payroll = self.payroll
            last_day = monthrange(payroll.year, payroll.month)[1]
            if self.category != self.Category.SALARY:
                errors["category"] = "Payroll expenses must use the salary category."
            if self.station_id != payroll.station_id:
                errors["station"] = "The salary expense must use the payroll station."
            if self.amount != payroll.gross_pay:
                errors["amount"] = "The salary expense must match gross payroll."
            if self.expense_date != date(payroll.year, payroll.month, last_day):
                errors["expense_date"] = "Salary expenses use the payroll month's final date."
            if payroll.status != Payroll.Status.PAID:
                errors["payroll"] = "Only paid payroll can be posted as a salary expense."
            if (
                self.status != self.Status.APPROVED
                or self.recorded_by_id != payroll.created_by_id
                or self.decided_by_id != payroll.created_by_id
            ):
                errors["payroll"] = (
                    "Payroll salary expenses must be approved by the payroll Owner."
                )
        elif self.category == self.Category.SALARY and self.recorded_by_id:
            if self.recorded_by.role != User.Role.OWNER:
                errors["category"] = (
                    "Only an Owner can record a manual salary expense."
                )

        if not (self.description or "").strip():
            errors["description"] = "Provide a description for the expense."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "station_id",
                "category",
                "amount",
                "expense_date",
                "payee",
                "reference",
                "description",
                "recorded_by_id",
                "payroll_id",
                "status",
            ).first()
            if previous is None:
                raise ValidationError("The expense record no longer exists.")
            if any(
                previous[field] != getattr(self, field)
                for field in (
                    "station_id",
                    "category",
                    "amount",
                    "expense_date",
                    "payee",
                    "reference",
                    "description",
                    "recorded_by_id",
                    "payroll_id",
                )
            ):
                raise ValidationError("Recorded expense details are immutable.")
            if (
                previous["status"] != self.Status.PENDING
                or self.status not in (self.Status.APPROVED, self.Status.REJECTED)
            ):
                raise ValidationError("An expense can only be decided once.")
        self.reference = self.reference.strip().upper()
        self.description = self.description.strip()
        self.payee = self.payee.strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Expense records cannot be deleted.")

    def __str__(self):
        return f"{self.station.code} · {self.get_category_display()} · ₹{self.amount}"
