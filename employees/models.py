from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

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
