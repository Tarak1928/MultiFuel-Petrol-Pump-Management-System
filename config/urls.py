"""URL configuration for MultiFuel."""

import logging

from django.contrib import admin
from django.db import DatabaseError, connection
from django.http import HttpResponse
from django.urls import include, path


logger = logging.getLogger(__name__)


def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("Application health check could not reach the database.")
        return HttpResponse("Service unavailable", status=503)
    return HttpResponse("OK")


urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("admin/", admin.site.urls),
    path("stations/", include("stations.urls")),
    path("employees/", include("employees.urls")),
    path("fuel/", include("fuel.urls")),
    path("duties/", include("duties.urls")),
    path("credits/", include("credits.urls")),
    path("expenses/", include("expenses.urls")),
    path("reports/", include("reports.urls")),
    path("notifications/", include("notifications.urls")),
    path("", include("accounts.urls")),
]
