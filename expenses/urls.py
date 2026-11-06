from django.urls import path

from . import views


app_name = "expenses"

urlpatterns = [
    path("", views.expense_list, name="expense-list"),
    path("new/", views.expense_create, name="expense-create"),
    path(
        "<int:expense_id>/decision/",
        views.expense_decide,
        name="expense-decision",
    ),
    path("accounting/", views.accounting_report, name="accounting-report"),
]
