from django import forms
from django.utils import timezone

from accounts.models import User
from employees.models import Employee
from stations.models import Station

from .models import Expense


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = (
            "station",
            "category",
            "amount",
            "expense_date",
            "payee",
            "reference",
            "description",
        )
        widgets = {
            "station": forms.Select(attrs={"class": "form-select"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
            ),
            "expense_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"},
                format="%Y-%m-%d",
            ),
            "payee": forms.TextInput(attrs={"class": "form-control"}),
            "reference": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
        }

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if user.role == User.Role.OWNER:
            stations = Station.objects.filter(owner=user, is_active=True)
        elif user.role == User.Role.MANAGER:
            stations = Station.objects.filter(
                manager_assignments__employee__user=user,
                manager_assignments__employee__is_active=True,
                is_active=True,
            )
        else:
            employee = Employee.objects.filter(user=user, is_active=True).first()
            stations = (
                Station.objects.filter(
                    pk=employee.assigned_station_id,
                    is_active=True,
                )
                if employee and employee.assigned_station_id
                else Station.objects.none()
            )
        self.fields["station"].queryset = stations.distinct()
        self.fields["expense_date"].initial = timezone.localdate()
        if user.role != User.Role.OWNER:
            self.fields["category"].choices = [
                choice
                for choice in self.fields["category"].choices
                if choice[0] != Expense.Category.SALARY
            ]
        self.fields["description"].help_text = (
            "Record each business expense once here. Duty-report expense totals "
            "are for cash reconciliation and are not imported automatically."
        )


class ExpenseDecisionForm(forms.Form):
    action = forms.ChoiceField(
        choices=(("approve", "Approve"), ("reject", "Reject")),
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    decision_note = forms.CharField(
        max_length=500,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Required review note",
            }
        ),
    )
