from functools import wraps

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .models import User


def role_required(*roles):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped_view(request, *args, **kwargs):
            employee = getattr(request.user, "employee_profile", None)
            if (
                not request.user.is_active
                or not request.user.is_approved
                or (employee and not employee.is_active)
            ):
                logout(request)
                messages.error(
                    request,
                    "Your account or employee profile is inactive or not approved.",
                )
                return redirect("login")
            if request.user.role not in roles:
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapped_view

    return decorator
