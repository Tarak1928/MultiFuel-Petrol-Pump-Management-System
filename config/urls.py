"""URL configuration for MultiFuel."""

from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
    path("stations/", include("stations.urls")),
    path("employees/", include("employees.urls")),
    path("fuel/", include("fuel.urls")),
    path("duties/", include("duties.urls")),
    path("", include("accounts.urls")),
]
