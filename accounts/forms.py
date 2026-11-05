from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordResetForm,
    UserCreationForm,
)
from django.core.exceptions import ValidationError

from .models import User


class MultiFuelAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_approved:
            raise ValidationError(
                "Your account is awaiting Owner approval.",
                code="account_not_approved",
            )


class ApprovedPasswordResetForm(PasswordResetForm):
    def get_users(self, email):
        return (
            user
            for user in super().get_users(email)
            if user.is_approved
        )


class RegistrationForm(UserCreationForm):
    requested_owner = forms.ModelChoiceField(
        queryset=User.objects.none(),
        label="Petrol pump owner",
        empty_label="Select your Owner",
    )
    role = forms.ChoiceField(
        choices=(
            (User.Role.MANAGER, User.Role.MANAGER.label),
            (User.Role.DSMS, User.Role.DSMS.label),
        )
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "role",
            "requested_owner",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["requested_owner"].queryset = User.objects.filter(
            role=User.Role.OWNER,
            is_active=True,
            is_approved=True,
        )
        self.fields["requested_owner"].widget.attrs["class"] = "form-select"
        self.fields["requested_owner"].help_text = (
            "Choose the Owner responsible for approving and managing your account."
        )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_approved = False
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
            self.save_m2m()
        return user
