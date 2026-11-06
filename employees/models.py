from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
)
from django.db import models
from django.db.models import Q, Sum

from accounts.models import User
from stations.models import Station


class Employee(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_profile",
    )
    employee_code = models.CharField(
        max_length=20,
        validators=[
            RegexValidator(
                regex=r"^[A-Z0-9][A-Z0-9_-]{1,19}$",
                message="Use 2–20 uppercase letters, digits, hyphens, or underscores.",
            )
        ],
    )
    phone = models.CharField(max_length=20, blank=True)
    hire_date = models.DateField()
    basic_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    assigned_station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="dsms_employees",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["employee_code"]
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "employee_code"),
                name="employee_owner_code_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["owner", "is_active"], name="employee_owner_active_idx"),
        ]

    def clean(self):
        errors = {}
        if self.owner_id:
            if self.owner.role != User.Role.OWNER or not self.owner.is_approved:
                errors["owner"] = "Employees must be managed by an approved Owner."
        if self.user_id:
            if self.user.role not in (User.Role.MANAGER, User.Role.DSMS):
                errors["user"] = "Only Manager and DSMS accounts can have employee profiles."
            if not self.user.is_approved or self.user.approved_by_id != self.owner_id:
                errors["user"] = "This account was not approved by this Owner."
            if self.user.role == User.Role.DSMS:
                if not self.assigned_station_id:
                    errors["assigned_station"] = "DSMS staff must be assigned to one station."
                elif self.assigned_station.owner_id != self.owner_id:
                    errors["assigned_station"] = "Choose one of your own stations."
            elif self.assigned_station_id:
                errors["assigned_station"] = (
                    "Manager station access is managed through station assignments."
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.employee_code} — {self.user.get_full_name() or self.user.username}"


class ManagerAssignment(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="manager_assignments",
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="manager_assignments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["station__name"]
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "station"),
                name="manager_station_assignment_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["station", "employee"], name="manager_station_employee_idx"),
        ]

    def clean(self):
        errors = {}
        if self.employee_id:
            if self.employee.user.role != User.Role.MANAGER:
                errors["employee"] = "Only Manager employees can have station assignments."
            if not self.employee.is_active:
                errors["employee"] = "Inactive employees cannot receive station assignments."
        if self.station_id and self.employee_id:
            if self.station.owner_id != self.employee.owner_id:
                errors["station"] = "Choose a station managed by this employee's Owner."
            if not self.station.is_active:
                errors["station"] = "New assignments must use an active station."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.employee} — {self.station}"


class Attendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LEAVE = "leave", "On leave"

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    work_date = models.DateField()
    status = models.CharField(max_length=12, choices=Status.choices)
    clock_in = models.TimeField(blank=True, null=True)
    clock_out = models.TimeField(blank=True, null=True)
    overtime_hours = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    notes = models.CharField(max_length=500, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="recorded_attendance",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-work_date", "employee__employee_code"]
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "work_date"),
                name="employee_attendance_date_uniq",
            ),
            models.CheckConstraint(
                condition=Q(overtime_hours__gte=0),
                name="attendance_overtime_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=("station", "-work_date"),
                name="attendance_station_date_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.employee_id and self.station_id:
            if self.employee.owner_id != self.station.owner_id:
                errors["station"] = "Choose a station belonging to this employee's Owner."
            elif self.employee.user.role == User.Role.DSMS:
                if self.employee.assigned_station_id != self.station_id:
                    errors["station"] = "DSMS attendance must use the assigned station."
            elif self.employee.user.role == User.Role.MANAGER:
                if not ManagerAssignment.objects.filter(
                    employee=self.employee,
                    station=self.station,
                ).exists():
                    errors["station"] = "Assign this Manager to the station first."
        if self.recorded_by_id and self.station_id:
            if (
                self.recorded_by.role == User.Role.OWNER
                and self.recorded_by_id != self.station.owner_id
            ):
                errors["recorded_by"] = "Only this station's Owner can record attendance."
            elif self.recorded_by.role == User.Role.MANAGER:
                assigned = ManagerAssignment.objects.filter(
                    employee__user=self.recorded_by,
                    employee__is_active=True,
                    station=self.station,
                    station__is_active=True,
                ).exists()
                if not assigned:
                    errors["recorded_by"] = (
                        "Only a Manager assigned to this station can record attendance."
                    )
            elif self.recorded_by.role not in (User.Role.OWNER, User.Role.MANAGER):
                errors["recorded_by"] = "Only the Owner or an assigned Manager can record attendance."
        if bool(self.clock_in) != bool(self.clock_out):
            errors["clock_out"] = "Enter both clock-in and clock-out times."
        if self.status != self.Status.PRESENT and (
            self.clock_in or self.clock_out or self.overtime_hours
        ):
            errors["status"] = (
                "Clock times and overtime can only be recorded for present employees."
            )
        if self.employee_id and self.work_date:
            if Payroll.objects.filter(
                employee=self.employee,
                year=self.work_date.year,
                month=self.work_date.month,
            ).exists():
                errors["work_date"] = (
                    "Attendance is locked after payroll has been generated for that month."
                )
            approved_leave = LeaveRequest.objects.filter(
                employee=self.employee,
                status=LeaveRequest.Status.APPROVED,
                start_date__lte=self.work_date,
                end_date__gte=self.work_date,
            ).exists()
            if approved_leave and self.status != self.Status.LEAVE:
                errors["status"] = "Approved leave must be recorded as on leave."
            elif self.status == self.Status.LEAVE and not approved_leave:
                errors["status"] = "Record leave attendance only for approved leave."
        if self.recorded_by_id and (
            not self.recorded_by.is_active or not self.recorded_by.is_approved
        ):
            errors["recorded_by"] = "Only active, approved staff can record attendance."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values("work_date").first()
            if previous is None:
                raise ValidationError("The attendance record no longer exists.")
            dates = (previous["work_date"], self.work_date)
            if any(
                Payroll.objects.filter(
                    employee=self.employee,
                    year=work_date.year,
                    month=work_date.month,
                ).exists()
                for work_date in dates
            ):
                raise ValidationError(
                    "Attendance is locked after payroll has been generated for that month."
                )
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if Payroll.objects.filter(
            employee=self.employee,
            year=self.work_date.year,
            month=self.work_date.month,
        ).exists():
            raise ValidationError(
                "Attendance is locked after payroll has been generated for that month."
            )
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} · {self.work_date} · {self.get_status_display()}"


class LeaveRequest(models.Model):
    class Type(models.TextChoices):
        ANNUAL = "annual", "Annual leave"
        SICK = "sick", "Sick leave"
        PERSONAL = "personal", "Personal leave"
        UNPAID = "unpaid", "Unpaid leave"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="leave_requests",
    )
    leave_type = models.CharField(max_length=12, choices=Type.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.CharField(max_length=500)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_leave_requests",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_employee_leave_requests",
        blank=True,
        null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    review_note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-requested_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__gte=models.F("start_date")),
                name="leave_request_dates_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status="pending",
                        reviewed_by__isnull=True,
                        reviewed_at__isnull=True,
                    )
                    | (
                        ~Q(status="pending")
                        & Q(reviewed_by__isnull=False)
                        & Q(reviewed_at__isnull=False)
                    )
                ),
                name="leave_review_matches_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=("employee", "status", "start_date"),
                name="leave_employee_status_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.end_date and self.start_date and self.end_date < self.start_date:
            errors["end_date"] = "Leave must end on or after its start date."
        if self.employee_id and self.requested_by_id:
            if self.requested_by_id != self.employee.user_id:
                errors["requested_by"] = "Employees can only request their own leave."
        if self.employee_id and self.status in (self.Status.PENDING, self.Status.APPROVED):
            overlapping = LeaveRequest.objects.filter(
                employee=self.employee,
                status__in=(self.Status.PENDING, self.Status.APPROVED),
                start_date__lte=self.end_date,
                end_date__gte=self.start_date,
            )
            if self.pk:
                overlapping = overlapping.exclude(pk=self.pk)
            if overlapping.exists():
                errors["start_date"] = (
                    "This period overlaps another pending or approved leave request."
                )
        if self.status == self.Status.PENDING:
            if self.reviewed_by_id or self.reviewed_at:
                errors["status"] = "Pending leave cannot have a review recorded."
        elif self.status in (self.Status.APPROVED, self.Status.REJECTED):
            if not self.reviewed_by_id or not self.reviewed_at:
                errors["status"] = "A leave decision requires a reviewer and timestamp."
            elif self.employee_id:
                station_ids = _employee_station_ids(self.employee)
                if self.reviewed_by.role == User.Role.OWNER:
                    valid_reviewer = self.reviewed_by_id == self.employee.owner_id
                elif self.reviewed_by.role == User.Role.MANAGER:
                    valid_reviewer = ManagerAssignment.objects.filter(
                        employee__user=self.reviewed_by,
                        employee__is_active=True,
                        station_id__in=station_ids,
                        station__is_active=True,
                    ).exists()
                else:
                    valid_reviewer = False
                if not valid_reviewer:
                    errors["reviewed_by"] = (
                        "Only the employee's Owner or an assigned Manager can review leave."
                    )
            if self.status == self.Status.APPROVED and self.employee_id:
                if Attendance.objects.filter(
                    employee=self.employee,
                    status=Attendance.Status.PRESENT,
                    work_date__range=(self.start_date, self.end_date),
                ).exists():
                    errors["status"] = (
                        "Leave cannot be approved while present attendance exists in that period."
                    )
        if not (self.reason or "").strip():
            errors["reason"] = "Provide a reason for the leave request."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values(
                "employee_id",
                "leave_type",
                "start_date",
                "end_date",
                "reason",
                "requested_by_id",
                "status",
            ).first()
            if previous is None:
                raise ValidationError("The leave request no longer exists.")
            if any(
                previous[field] != getattr(self, field)
                for field in (
                    "employee_id",
                    "leave_type",
                    "start_date",
                    "end_date",
                    "reason",
                    "requested_by_id",
                )
            ):
                raise ValidationError("Leave requests cannot be edited after submission.")
            if (
                previous["status"] != self.Status.PENDING
                or self.status not in (self.Status.APPROVED, self.Status.REJECTED)
            ):
                raise ValidationError("A leave request can only be decided once.")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} · {self.start_date}–{self.end_date}"

    def delete(self, *args, **kwargs):
        raise ValidationError("Leave requests cannot be deleted.")


class EmployeeAdvance(models.Model):
    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="salary_advances",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="employee_salary_advances",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    issued_date = models.DateField()
    reference = models.CharField(max_length=40)
    notes = models.CharField(max_length=500, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="issued_employee_advances",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["issued_date", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=("owner", "reference"),
                name="employee_advance_owner_ref_uniq",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="employee_advance_positive",
            ),
        ]
        indexes = [
            models.Index(
                fields=("employee", "issued_date"),
                name="employee_advance_date_idx",
            ),
        ]

    @property
    def repaid_amount(self):
        return self.repayments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    @property
    def outstanding_amount(self):
        return self.amount - self.repaid_amount

    def clean(self):
        errors = {}
        if (
            self.employee_id
            and self.owner_id
            and self.owner_id != self.employee.owner_id
        ):
            errors["owner"] = "The advance Owner must own the employee profile."
        if self.issued_by_id and (
            self.issued_by.role != User.Role.OWNER
            or self.issued_by_id != self.owner_id
            or not self.issued_by.is_active
            or not self.issued_by.is_approved
        ):
            errors["issued_by"] = "Only the employee's active Owner can issue advances."
        if not (self.reference or "").strip():
            errors["reference"] = "Enter an advance reference."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Employee advance records are immutable.")
        self.reference = self.reference.strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} · ₹{self.amount} advance"


class Payroll(models.Model):
    class Status(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"

    employee = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        related_name="payroll_records",
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.PROTECT,
        related_name="payroll_records",
        blank=True,
        null=True,
    )
    year = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(2000), MaxValueValidator(9999)]
    )
    month = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    basic_salary = models.DecimalField(max_digits=12, decimal_places=2)
    allowances = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    overtime_hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    overtime_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    overtime_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    gross_pay = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    other_deductions = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    advance_deduction = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    net_pay = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    status = models.CharField(
        max_length=8,
        choices=Status.choices,
        default=Status.UNPAID,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="generated_payroll",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-year", "-month", "employee__employee_code"]
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "year", "month"),
                name="employee_payroll_period_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    Q(month__gte=1)
                    & Q(month__lte=12)
                    & Q(basic_salary__gte=0)
                    & Q(allowances__gte=0)
                    & Q(overtime_hours__gte=0)
                    & Q(overtime_rate__gte=0)
                    & Q(overtime_amount__gte=0)
                    & Q(gross_pay__gte=0)
                    & Q(other_deductions__gte=0)
                    & Q(advance_deduction__gte=0)
                    & Q(net_pay__gte=0)
                ),
                name="payroll_values_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="unpaid", paid_at__isnull=True)
                    | Q(status="paid", paid_at__isnull=False)
                ),
                name="payroll_payment_matches_status",
            ),
        ]
        indexes = [
            models.Index(
                fields=("employee", "-year", "-month"),
                name="payroll_employee_period_idx",
            ),
        ]

    def clean(self):
        errors = {}
        if self.employee_id and self.created_by_id:
            if (
                self.created_by.role != User.Role.OWNER
                or self.created_by_id != self.employee.owner_id
                or not self.created_by.is_active
                or not self.created_by.is_approved
            ):
                errors["created_by"] = "Only the employee's active Owner can generate payroll."
        if self.employee_id and self.station_id:
            if self.station.owner_id != self.employee.owner_id:
                errors["station"] = "Choose a station belonging to the employee's Owner."
            elif self.employee.user.role == User.Role.DSMS:
                if self.employee.assigned_station_id != self.station_id:
                    errors["station"] = "DSMS payroll must use the employee's assigned station."
            elif not ManagerAssignment.objects.filter(
                employee=self.employee,
                station=self.station,
            ).exists():
                errors["station"] = "Choose a station assigned to this Manager."
        elif self.employee_id and not self.station_id:
            errors["station"] = "Choose a station to allocate this payroll expense."
        if self.net_pay < 0:
            errors["other_deductions"] = "Deductions cannot exceed gross pay."
        if self.employee_id and not self.pk:
            outstanding = sum(
                (
                    advance.outstanding_amount
                    for advance in EmployeeAdvance.objects.filter(
                        employee=self.employee
                    )
                ),
                start=Decimal("0.00"),
            )
            if self.advance_deduction > outstanding:
                errors["advance_deduction"] = (
                    "Advance deduction cannot exceed outstanding employee advances."
                )
        if self.status == self.Status.PAID and self.advance_deduction:
            recorded_repayments = self.advance_repayments.aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00")
            if recorded_repayments != self.advance_deduction:
                errors["advance_deduction"] = (
                    "Paid payroll must include its full advance repayment allocation."
                )
        if self.status == self.Status.UNPAID and self.paid_at:
            errors["paid_at"] = "Unpaid payroll cannot have a payment date."
        if self.status == self.Status.PAID and not self.paid_at:
            errors["paid_at"] = "Record the date when payroll was paid."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.overtime_amount = (
            self.overtime_hours * self.overtime_rate
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        self.gross_pay = self.basic_salary + self.allowances + self.overtime_amount
        self.net_pay = (
            self.gross_pay - self.other_deductions - self.advance_deduction
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).values().first()
            if previous is None:
                raise ValidationError("The payroll record no longer exists.")
            immutable_fields = (
                "employee_id",
                "station_id",
                "year",
                "month",
                "basic_salary",
                "allowances",
                "overtime_hours",
                "overtime_rate",
                "overtime_amount",
                "gross_pay",
                "other_deductions",
                "advance_deduction",
                "net_pay",
                "created_by_id",
            )
            if any(previous[field] != getattr(self, field) for field in immutable_fields):
                raise ValidationError("Generated payroll cannot be edited.")
            if (
                previous["status"] != self.Status.UNPAID
                or self.status != self.Status.PAID
                or not self.paid_at
            ):
                raise ValidationError("Payroll can only be marked paid once.")
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.employee} · {self.year}-{self.month:02d}"


class PayrollAdvanceRepayment(models.Model):
    payroll = models.ForeignKey(
        Payroll,
        on_delete=models.PROTECT,
        related_name="advance_repayments",
    )
    advance = models.ForeignKey(
        EmployeeAdvance,
        on_delete=models.PROTECT,
        related_name="repayments",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["advance__issued_date", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=("payroll", "advance"),
                name="payroll_advance_repayment_uniq",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=0),
                name="payroll_advance_repayment_positive",
            ),
        ]

    def clean(self):
        errors = {}
        if self.payroll_id and self.advance_id:
            if self.payroll.employee_id != self.advance.employee_id:
                errors["advance"] = "Repayment and payroll must belong to the same employee."
            if self.payroll.status != Payroll.Status.UNPAID:
                errors["payroll"] = "Repayments can only be added to unpaid payroll."
            if self.amount > self.advance.outstanding_amount:
                errors["amount"] = "Repayment exceeds the outstanding advance balance."
            prior_repayments = self.payroll.advance_repayments.aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00")
            if prior_repayments + self.amount > self.payroll.advance_deduction:
                errors["amount"] = "Repayments exceed the payroll advance deduction."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Payroll advance repayments are immutable.")
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Payroll advance repayments are immutable.")

    def __str__(self):
        return f"{self.advance.reference} · ₹{self.amount}"


def _employee_station_ids(employee):
    if employee.user.role == User.Role.DSMS:
        return [employee.assigned_station_id] if employee.assigned_station_id else []
    return list(
        employee.manager_assignments.values_list("station_id", flat=True)
    )
