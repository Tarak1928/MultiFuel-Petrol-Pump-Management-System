from datetime import date, datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from duties.models import (
    DutyFuelEntry,
    DutyLubricantEntry,
    DutyReport,
    Lubricant,
    Shift,
)
from employees.models import Employee, ManagerAssignment
from fuel.models import FuelPrice, FuelType, Nozzle, Tank
from stations.models import Station

from .models import Expense


class StationExpenseTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="expense-owner",
            email="expense-owner@example.test",
            password="test-password",
        )
        self.other_owner = User.objects.create_superuser(
            username="other-expense-owner",
            email="other-expense-owner@example.test",
            password="test-password",
        )
        self.station = self.make_station(self.owner, "Expense Central", "EX-001")
        self.other_station = self.make_station(
            self.owner,
            "Expense North",
            "EX-002",
        )
        self.other_owner_station = self.make_station(
            self.other_owner,
            "Other Expense",
            "EX-003",
        )
        self.manager = self.make_staff("expense-manager", User.Role.MANAGER, self.owner)
        self.dsms = self.make_staff("expense-dsms", User.Role.DSMS, self.owner)
        self.other_dsms = self.make_staff(
            "other-expense-dsms",
            User.Role.DSMS,
            self.other_owner,
        )
        self.manager_employee = Employee.objects.create(
            owner=self.owner,
            user=self.manager,
            employee_code="EX-MGR",
            hire_date=date(2026, 1, 1),
        )
        ManagerAssignment.objects.create(
            employee=self.manager_employee,
            station=self.station,
        )
        self.employee = Employee.objects.create(
            owner=self.owner,
            user=self.dsms,
            employee_code="EX-DSMS",
            hire_date=date(2026, 1, 1),
            assigned_station=self.station,
        )
        self.other_employee = Employee.objects.create(
            owner=self.other_owner,
            user=self.other_dsms,
            employee_code="EX-OTHER",
            hire_date=date(2026, 1, 1),
            assigned_station=self.other_owner_station,
        )

    @staticmethod
    def make_station(owner, name, code):
        return Station.objects.create(
            name=name,
            code=code,
            address="10 Main Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            owner=owner,
        )

    @staticmethod
    def make_staff(username, role, owner):
        return User.objects.create_user(
            username=username,
            password="test-password",
            role=role,
            is_approved=True,
            requested_owner=owner,
            approved_by=owner,
        )

    def post_expense(self, user, station, reference="EXP-001"):
        self.client.force_login(user)
        return self.client.post(
            reverse("expenses:expense-create"),
            {
                "station": station.pk,
                "category": Expense.Category.CLEANING,
                "amount": "250.75",
                "expense_date": "2026-01-05",
                "payee": "Clean Services",
                "reference": reference,
                "description": "Monthly station cleaning",
            },
        )

    def test_owner_expense_is_approved_and_reference_is_normalized(self):
        response = self.post_expense(
            self.owner,
            self.station,
            reference="exp-jan-1",
        )

        self.assertRedirects(response, reverse("expenses:expense-list"))
        expense = Expense.objects.get()
        self.assertEqual(expense.reference, "EXP-JAN-1")
        self.assertEqual(expense.status, Expense.Status.APPROVED)
        self.assertEqual(expense.recorded_by, self.owner)
        self.assertEqual(expense.decided_by, self.owner)

    def test_manager_expense_is_pending_until_owner_decides(self):
        response = self.post_expense(self.manager, self.station)
        expense = Expense.objects.get()

        self.assertRedirects(response, reverse("expenses:expense-list"))
        self.assertEqual(expense.status, Expense.Status.PENDING)
        self.assertIsNone(expense.decided_by_id)
        self.client.force_login(self.owner)
        decision = self.client.post(
            reverse("expenses:expense-decision", args=[expense.pk]),
            {"action": "approve", "decision_note": "Invoice checked"},
        )

        self.assertRedirects(decision, reverse("expenses:expense-list"))
        expense.refresh_from_db()
        self.assertEqual(expense.status, Expense.Status.APPROVED)
        self.assertEqual(expense.decided_by, self.owner)
        self.assertEqual(expense.decision_note, "Invoice checked")

    def test_manager_cannot_record_or_view_expenses_at_unassigned_stations(self):
        response = self.post_expense(self.manager, self.other_station)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Expense.objects.exists())

        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(
                reverse("expenses:expense-list"),
                {"station": self.other_station.pk},
            ).status_code,
            404,
        )

    def test_dsms_can_submit_expense_but_cannot_access_accounting(self):
        response = self.post_expense(self.dsms, self.station)
        expense = Expense.objects.get()

        self.assertRedirects(response, reverse("expenses:expense-list"))
        self.assertEqual(expense.status, Expense.Status.PENDING)
        self.client.force_login(self.dsms)
        self.assertEqual(
            self.client.get(reverse("expenses:accounting-report")).status_code,
            403,
        )

    def test_owner_cannot_reuse_an_expense_reference_at_same_station(self):
        first = self.post_expense(self.owner, self.station)
        duplicate = self.post_expense(self.owner, self.station)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(Expense.objects.count(), 1)

    def test_accounting_report_uses_approved_sales_and_expenses_by_station(self):
        fuel_type = FuelType.objects.get(code="MS")
        tank = Tank.objects.create(
            station=self.station,
            fuel_type=fuel_type,
            code="T-01",
            capacity_litres=10000,
            current_stock_litres=1000,
        )
        nozzle = Nozzle.objects.create(
            station=self.station,
            tank=tank,
            code="N-01",
        )
        price = FuelPrice.objects.create(
            station=self.station,
            fuel_type=fuel_type,
            price_per_litre=Decimal("100.00"),
            effective_from=timezone.make_aware(
                datetime.combine(date(2026, 1, 1), time(5, 0))
            ),
            recorded_by=self.owner,
        )
        shift = Shift.objects.create(
            station=self.station,
            name="Morning",
            start_time=time(6, 0),
            end_time=time(14, 0),
        )
        report = DutyReport.objects.create(
            station=self.station,
            employee=self.employee,
            shift=shift,
            report_date=date(2026, 1, 5),
        )
        DutyFuelEntry.objects.create(
            report=report,
            nozzle=nozzle,
            fuel_price=price,
            opening_reading=Decimal("0.000"),
            closing_reading=Decimal("10.000"),
            testing_litres=Decimal("0.000"),
            price_per_litre=Decimal("100.00"),
        )
        lubricant = Lubricant.objects.create(
            station=self.station,
            code="OIL-1",
            name="Engine Oil",
            current_stock=10,
            selling_price=25,
        )
        DutyLubricantEntry.objects.create(
            report=report,
            lubricant=lubricant,
            quantity=Decimal("2.000"),
            unit_price=Decimal("25.00"),
        )
        report.status = DutyReport.Status.APPROVED
        report.submitted_at = timezone.now()
        report.save()
        self.post_expense(self.owner, self.station)
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("expenses:accounting-report"),
            {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["fuel_sales"], Decimal("1000.00"))
        self.assertEqual(response.context["lubricant_sales"], Decimal("50.00"))
        self.assertEqual(response.context["total_expenses"], Decimal("250.75"))
        self.assertEqual(
            response.context["net_after_expenses"],
            Decimal("799.25"),
        )
        self.assertContains(response, "₹1000.00")
        self.assertContains(response, "₹50.00")

    def test_expense_records_are_immutable_after_creation(self):
        self.post_expense(self.owner, self.station)
        expense = Expense.objects.get()
        expense.amount = Decimal("1.00")

        with self.assertRaises(ValidationError):
            expense.save()
