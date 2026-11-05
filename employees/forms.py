from django import forms
from django.core.validators import RegexValidator
from django.db import transaction

from accounts.models import User
from stations.models import Station

from .models import Employee, ManagerAssignment


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
