from datetime import date
from decimal import Decimal
from io import BytesIO

from django.utils import timezone
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from employees.models import Employee, ManagerAssignment
from expenses.models import Expense
from fuel.models import FuelType, InventoryLedger, Tank
from stations.models import Station


class AnalyticsAccessAndExportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            password="Secure-owner-password-1!",
            role=User.Role.OWNER,
            is_approved=True,
        )
        self.other_owner = User.objects.create_user(
            username="other-owner",
            password="Secure-owner-password-2!",
            role=User.Role.OWNER,
            is_approved=True,
        )
        self.station = Station.objects.create(
            name="Central",
            code="CENTRAL",
            address="1 Main Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            owner=self.owner,
        )
        self.other_station = Station.objects.create(
            name="Other",
            code="OTHER",
            address="2 Main Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411002",
            owner=self.other_owner,
        )

    def test_owner_can_view_each_analytics_report(self):
        self.client.force_login(self.owner)
        for kind in ("sales", "inventory", "duty", "workforce"):
            with self.subTest(report=kind):
                response = self.client.get(
                    reverse("reports:analytics"),
                    {"report": kind, "start_date": "2026-01-01", "end_date": "2026-01-31"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["report_kind"], kind)

    def test_manager_only_sees_assigned_stations(self):
        manager_user = User.objects.create_user(
            username="manager",
            password="Secure-manager-password-1!",
            role=User.Role.MANAGER,
            is_approved=True,
            approved_by=self.owner,
        )
        employee = Employee.objects.create(
            owner=self.owner,
            user=manager_user,
            employee_code="MGR01",
            hire_date=date(2026, 1, 1),
        )
        ManagerAssignment.objects.create(employee=employee, station=self.station)
        self.client.force_login(manager_user)

        response = self.client.get(
            reverse("reports:analytics"),
            {"station": self.other_station.pk},
        )

        self.assertEqual(response.status_code, 404)

    def test_all_export_formats_are_generated(self):
        self.client.force_login(self.owner)
        for export_format, content_type in (
            ("csv", "text/csv"),
            (
                "excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            ("pdf", "application/pdf"),
        ):
            with self.subTest(format=export_format):
                response = self.client.get(
                    reverse("reports:analytics"),
                    {"report": "sales", "format": export_format},
                )
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response["Content-Type"].startswith(content_type))
                self.assertIn("attachment;", response["Content-Disposition"])

    def test_invalid_export_format_does_not_export(self):
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("reports:analytics"),
            {"format": "unknown"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Content-Disposition", response)

    def test_sales_report_aggregates_approved_expenses_in_period(self):
        today = timezone.localdate()
        Expense.objects.create(
            station=self.station,
            category=Expense.Category.ELECTRICITY,
            amount=Decimal("1250.00"),
            expense_date=today,
            description="Monthly power bill",
            status=Expense.Status.APPROVED,
            recorded_by=self.owner,
            decided_by=self.owner,
            decided_at=timezone.now(),
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("reports:analytics"),
            {
                "report": "sales",
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row["Approved expenses"], Decimal("1250.00"))
        self.assertEqual(row["Sales less expenses"], Decimal("-1250.00"))

    def test_inventory_report_shows_stock_after_daily_ledger_movements(self):
        fuel_type = FuelType.objects.create(name="Petrol", code="TESTPET")
        tank = Tank.objects.create(
            station=self.station,
            fuel_type=fuel_type,
            code="TANK01",
            capacity_litres=Decimal("1000.000"),
            current_stock_litres=Decimal("100.000"),
            reorder_level_litres=Decimal("25.000"),
        )
        InventoryLedger.objects.create(
            tank=tank,
            transaction_type=InventoryLedger.TransactionType.OPENING,
            quantity_delta=Decimal("100.000"),
            recorded_by=self.owner,
        )
        today = timezone.localdate()
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("reports:analytics"),
            {
                "report": "inventory",
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        row = response.context["rows"][0]
        self.assertEqual(row["Stock after movement (L)"], Decimal("100.000"))
        self.assertEqual(row["Transactions"], 1)

    def test_text_that_looks_like_a_spreadsheet_formula_is_safe_in_exports(self):
        self.station.name = "=2+2"
        self.station.save(update_fields=("name",))
        self.client.force_login(self.owner)
        url = reverse("reports:analytics")

        csv_response = self.client.get(url, {"format": "csv"})
        self.assertIn(b"'=2+2", csv_response.content)

        excel_response = self.client.get(url, {"format": "excel"})
        from openpyxl import load_workbook

        workbook = load_workbook(BytesIO(excel_response.content))
        self.assertEqual(workbook.active["A2"].value, "=2+2")
        self.assertEqual(workbook.active["A2"].data_type, "s")
