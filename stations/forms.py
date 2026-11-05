from django import forms

from .models import Station


class StationCodeField(forms.CharField):
    def to_python(self, value):
        return super().to_python(value).upper()


class StationForm(forms.ModelForm):
    code = StationCodeField(
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "autocomplete": "off",
                "placeholder": "e.g. MF-001",
            }
        ),
    )

    class Meta:
        model = Station
        fields = (
            "name",
            "code",
            "address",
            "city",
            "state",
            "postal_code",
            "contact_number",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.TextInput(attrs={"class": "form-control"}),
            "city": forms.TextInput(attrs={"class": "form-control"}),
            "state": forms.TextInput(attrs={"class": "form-control"}),
            "postal_code": forms.TextInput(attrs={"class": "form-control"}),
            "contact_number": forms.TextInput(attrs={"class": "form-control"}),
        }

    def clean_code(self):
        return self.cleaned_data["code"]
