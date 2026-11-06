from decimal import Decimal

from django import forms
from django.core.validators import RegexValidator
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounts.models import User
from stations.models import Station

from .models import (
    Attendance,
    Employee,
    EmployeeAdvance,
    LeaveRequest,
    ManagerAssignment,
    Payroll,
)


class EmployeeCodeField(forms.CharField):
    def to_python(self, value):
        return super().to_python(value).upper()


class EmployeeForm(forms.ModelForm):
    employee_code = EmployeeCodeField(
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
    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Approved staff account",
    )
    manager_stations = forms.ModelMultipleChoiceField(
        queryset=Station.objects.none(),
        required=False,
        label="Manager station assignments",
        help_text=(
            "Managers need one or more active stations. DSMS staff must have "
            "exactly one assigned station."
        ),
        widget=forms.SelectMultiple(attrs={"class": "form-select", "size": 5}),
    )

    class Meta:
        model = Employee
        fields = (
            "user",
            "employee_code",
            "phone",
            "hire_date",
            "basic_salary",
            "assigned_station",
            "is_active",
        )
        widgets = {
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "hire_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
            "assigned_station": forms.Select(attrs={"class": "form-select"}),
            "basic_salary": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def __init__(self, owner, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None:
            instance = Employee(owner=owner)
            kwargs["instance"] = instance
        else:
            instance.owner = owner

        super().__init__(*args, **kwargs)
        self.owner = owner
        self.fields["basic_salary"].required = False
        self.fields["basic_salary"].initial = Decimal("0.00")
        self.fields["user"].queryset = User.objects.filter(
            approved_by=owner,
            is_approved=True,
            role__in=(User.Role.MANAGER, User.Role.DSMS),
        ).filter(employee_profile__isnull=True)
        self.fields["user"].widget.attrs["class"] = "form-select"

        if instance.pk:
            self.fields["user"].queryset |= User.objects.filter(pk=instance.user_id)
            self.fields["user"].initial = instance.user_id
            self.fields["user"].disabled = True
            self.fields["manager_stations"].initial = (
                instance.manager_assignments.values_list("station_id", flat=True)
            )

        stations = Station.objects.filter(owner=owner)
        self.fields["manager_stations"].queryset = stations.filter(is_active=True)
        if instance.pk:
            self.fields["manager_stations"].queryset |= stations.filter(
                manager_assignments__employee=instance
            )

        dsms_stations = stations.filter(is_active=True)
        if instance.pk and instance.assigned_station_id:
            dsms_stations |= stations.filter(pk=instance.assigned_station_id)
        self.fields["assigned_station"].queryset = dsms_stations
        self.fields["assigned_station"].required = False
        self.fields["assigned_station"].empty_label = "Select one station"

    def clean(self):
        cleaned_data = super().clean()
        user = cleaned_data.get("user")
        assigned_station = cleaned_data.get("assigned_station")
        manager_stations = cleaned_data.get("manager_stations") or Station.objects.none()

        if not user:
            return cleaned_data
        if user.approved_by_id != self.owner.pk or not user.is_approved:
            self.add_error("user", "Choose an account approved by this Owner.")
            return cleaned_data

        if user.role == User.Role.DSMS:
            if not assigned_station:
                self.add_error(
                    "assigned_station",
                    "DSMS staff must be assigned to exactly one station.",
                )
            elif (
                not assigned_station.is_active
                and assigned_station.pk != self.instance.assigned_station_id
            ):
                self.add_error("assigned_station", "Choose an active station.")
            if manager_stations:
                self.add_error(
                    "manager_stations",
                    "DSMS staff use the single-station assignment field.",
                )
        elif user.role == User.Role.MANAGER:
            if assigned_station:
                self.add_error(
                    "assigned_station",
                    "Use Manager station assignments instead of a single station.",
                )
            if not manager_stations:
                self.add_error(
                    "manager_stations",
                    "Assign each Manager to at least one station.",
                )
            elif any(not station.is_active for station in manager_stations):
                current_ids = set(
                    self.instance.manager_assignments.values_list("station_id", flat=True)
                )
                if any(
                    not station.is_active and station.pk not in current_ids
                    for station in manager_stations
                ):
                    self.add_error(
                        "manager_stations",
                        "New assignments must use active stations.",
                    )
            if not cleaned_data.get("is_active"):
                current_ids = set(
                    self.instance.manager_assignments.values_list("station_id", flat=True)
                )
                if any(station.pk not in current_ids for station in manager_stations):
                    self.add_error(
                        "manager_stations",
                        "Activate the employee before assigning new stations.",
                    )

        return cleaned_data

    def clean_basic_salary(self):
        return self.cleaned_data.get("basic_salary") or Decimal("0.00")

    @transaction.atomic
    def save(self, commit=True):
        employee = super().save(commit=False)
        employee.owner = self.owner
        if not commit:
            return employee
        employee.save()
        stations = self.cleaned_data["manager_stations"]
        selected_ids = {station.pk for station in stations}
        employee.manager_assignments.exclude(station_id__in=selected_ids).delete()
        existing_ids = set(
            employee.manager_assignments.filter(station_id__in=selected_ids).values_list(
                "station_id",
                flat=True,
            )
        )
        for station in stations:
            if station.pk not in existing_ids:
                assignment = ManagerAssignment(employee=employee, station=station)
                assignment.full_clean()
                ManagerAssignment.objects.get_or_create(
                    employee=employee,
                    station=station,
                )
        return employee


class AttendanceForm(forms.ModelForm):
    class Meta:
        model = Attendance
        fields = (
            "station",
            "employee",
            "work_date",
            "status",
            "clock_in",
            "clock_out",
            "overtime_hours",
            "notes",
        )
        widgets = {
            "station": forms.Select(attrs={"class": "form-select"}),
            "employee": forms.Select(attrs={"class": "form-select"}),
            "work_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
            "status": forms.Select(attrs={"class": "form-select"}),
            "clock_in": forms.TimeInput(
                attrs={"class": "form-control", "type": "time"}
            ),
            "clock_out": forms.TimeInput(
                attrs={"class": "form-control", "type": "time"}
            ),
            "overtime_hours": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.25"}
            ),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if user.role == User.Role.OWNER:
            stations = Station.objects.filter(owner=user, is_active=True)
        else:
            manager = Employee.objects.filter(user=user, is_active=True).first()
            stations = (
                Station.objects.filter(
                    manager_assignments__employee=manager,
                    manager_assignments__employee__is_active=True,
                    is_active=True,
                )
                if manager
                else Station.objects.none()
            )
        self.fields["station"].queryset = stations.distinct()
        if self.instance.pk:
            self.fields["station"].queryset |= Station.objects.filter(
                pk=self.instance.station_id
            )
        station_id = (
            self.data.get("station")
            or self.initial.get("station")
            or getattr(self.instance, "station_id", None)
        )
        employees = Employee.objects.filter(is_active=True)
        if user.role == User.Role.OWNER:
            employees = employees.filter(owner=user)
        else:
            employees = employees.filter(
                owner__in=Employee.objects.filter(user=user).values("owner_id")
            )
        if station_id:
            employees = employees.filter(
                Q(assigned_station_id=station_id)
                | Q(manager_assignments__station_id=station_id)
            )
        else:
            employees = employees.filter(
                Q(assigned_station__in=stations)
                | Q(manager_assignments__station__in=stations)
            )
        self.fields["employee"].queryset = employees.distinct()


class LeaveRequestForm(forms.ModelForm):
    class Meta:
        model = LeaveRequest
        fields = ("leave_type", "start_date", "end_date", "reason")
        widgets = {
            "leave_type": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
            "end_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
            "reason": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }


class EmployeeAdvanceForm(forms.ModelForm):
    class Meta:
        model = EmployeeAdvance
        fields = ("employee", "amount", "issued_date", "reference", "notes")
        widgets = {
            "employee": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
            ),
            "issued_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
            "reference": forms.TextInput(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, owner, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.fields["employee"].queryset = Employee.objects.filter(owner=owner)
        self.fields["issued_date"].initial = timezone.localdate()


class PayrollForm(forms.ModelForm):
    class Meta:
        model = Payroll
        fields = (
            "employee",
            "station",
            "year",
            "month",
            "allowances",
            "overtime_rate",
            "other_deductions",
            "advance_deduction",
        )
        widgets = {
            "employee": forms.Select(attrs={"class": "form-select"}),
            "station": forms.Select(attrs={"class": "form-select"}),
            "year": forms.NumberInput(
                attrs={"class": "form-control", "min": "2000", "max": "9999"}
            ),
            "month": forms.NumberInput(
                attrs={"class": "form-control", "min": "1", "max": "12"}
            ),
            "allowances": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "overtime_rate": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "other_deductions": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "advance_deduction": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
        }

    def __init__(self, owner, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        self.fields["employee"].queryset = Employee.objects.filter(owner=owner)
        station_queryset = Station.objects.filter(owner=owner, is_active=True)
        employee_id = (
            self.data.get("employee")
            or self.initial.get("employee")
            or getattr(self.instance, "employee_id", None)
        )
        employee = Employee.objects.filter(owner=owner, pk=employee_id).first()
        if employee:
            if employee.user.role == User.Role.DSMS:
                station_queryset = station_queryset.filter(
                    pk=employee.assigned_station_id
                )
            else:
                station_queryset = station_queryset.filter(
                    manager_assignments__employee=employee
                )
        self.fields["station"].queryset = station_queryset.distinct()
        today = timezone.localdate()
        self.fields["year"].initial = today.year
        self.fields["month"].initial = today.month
        self.fields["allowances"].initial = 0
        self.fields["overtime_rate"].initial = 0
        self.fields["other_deductions"].initial = 0
        self.fields["advance_deduction"].initial = 0

    def clean(self):
        cleaned_data = super().clean()
        employee = cleaned_data.get("employee")
        station = cleaned_data.get("station")
        year = cleaned_data.get("year")
        month = cleaned_data.get("month")
        if not employee or year is None or month is None:
            return cleaned_data
        if employee.owner_id != self.owner.pk:
            self.add_error("employee", "Choose an employee managed by this Owner.")
            return cleaned_data
        if station:
            if station.owner_id != employee.owner_id:
                self.add_error("station", "Choose a station belonging to this Owner.")
            elif employee.user.role == User.Role.DSMS:
                if employee.assigned_station_id != station.pk:
                    self.add_error("station", "Choose the employee's assigned station.")
            elif not employee.manager_assignments.filter(station=station).exists():
                self.add_error("station", "Choose a station assigned to this Manager.")
        if Payroll.objects.filter(employee=employee, year=year, month=month).exists():
            self.add_error(
                "month",
                "Payroll has already been generated for this employee and month.",
            )
        if month < 1 or month > 12:
            return cleaned_data
        attendance = Attendance.objects.filter(
            employee=employee,
            work_date__year=year,
            work_date__month=month,
        )
        overtime_hours = attendance.aggregate(total=Sum("overtime_hours"))["total"] or 0
        basic_salary = employee.basic_salary
        allowances = cleaned_data.get("allowances") or 0
        overtime_rate = cleaned_data.get("overtime_rate") or 0
        other_deductions = cleaned_data.get("other_deductions") or 0
        advance_deduction = cleaned_data.get("advance_deduction") or 0
        gross = basic_salary + allowances + overtime_hours * overtime_rate
        if other_deductions + advance_deduction > gross:
            self.add_error("other_deductions", "Deductions cannot exceed gross pay.")
        outstanding = sum(
            (
                advance.outstanding_amount
                for advance in EmployeeAdvance.objects.filter(employee=employee)
            ),
            start=0,
        )
        if advance_deduction > outstanding:
            self.add_error(
                "advance_deduction",
                "Advance deduction cannot exceed this employee's outstanding advances.",
            )
        cleaned_data["calculated_overtime_hours"] = overtime_hours
        return cleaned_data
