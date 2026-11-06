from django.urls import path

from . import views


app_name = "credits"

urlpatterns = [
    path(
        "stations/<int:station_id>/customers/",
        views.customer_list,
        name="customer-list",
    ),
    path(
        "stations/<int:station_id>/customers/new/",
        views.customer_create,
        name="customer-create",
    ),
    path(
        "stations/<int:station_id>/overdue/",
        views.overdue_report,
        name="overdue-report",
    ),
    path(
        "customers/<int:customer_id>/",
        views.customer_detail,
        name="customer-detail",
    ),
    path(
        "customers/<int:customer_id>/edit/",
        views.customer_update,
        name="customer-update",
    ),
    path(
        "sales/<int:sale_id>/repayments/new/",
        views.credit_repayment_create,
        name="repayment-create",
    ),
    path(
        "sales/<int:sale_id>/edit/",
        views.credit_sale_update,
        name="credit-sale-update",
    ),
    path(
        "duties/<int:report_id>/credit-sales/new/",
        views.report_credit_sale_create,
        name="report-credit-sale-create",
    ),
    path(
        "duties/<int:report_id>/advances/",
        views.report_advance_list,
        name="report-advance-list",
    ),
    path(
        "duties/<int:report_id>/otp/",
        views.report_otp_list,
        name="report-otp-list",
    ),
]
