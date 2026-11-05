from django.contrib.auth.views import LogoutView
from django.urls import path
from django.views.generic import RedirectView

from .views import (
    MultiFuelLoginView,
    MultiFuelPasswordResetCompleteView,
    MultiFuelPasswordResetConfirmView,
    MultiFuelPasswordResetDoneView,
    MultiFuelPasswordResetView,
    approve_account,
    dashboard_redirect,
    dsms_dashboard,
    manager_dashboard,
    owner_dashboard,
    register,
)


urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("login/", MultiFuelLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="login"), name="logout"),
    path("register/", register, name="register"),
    path("dashboard/", dashboard_redirect, name="dashboard"),
    path("dashboard/owner/", owner_dashboard, name="dashboard-owner"),
    path("dashboard/manager/", manager_dashboard, name="dashboard-manager"),
    path("dashboard/dsms/", dsms_dashboard, name="dashboard-dsms"),
    path(
        "accounts/<int:user_id>/approve/",
        approve_account,
        name="approve-account",
    ),
    path(
        "password-reset/",
        MultiFuelPasswordResetView.as_view(),
        name="password-reset",
    ),
    path(
        "password-reset/done/",
        MultiFuelPasswordResetDoneView.as_view(),
        name="password-reset-done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        MultiFuelPasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path(
        "password-reset/complete/",
        MultiFuelPasswordResetCompleteView.as_view(),
        name="password-reset-complete",
    ),
]
