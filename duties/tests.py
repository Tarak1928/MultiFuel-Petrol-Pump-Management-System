import uuid
from datetime import date, datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from employees.models import Employee, ManagerAssignment
from fuel.models import FuelPrice, FuelType, Nozzle, Tank
from stations.models import Station

from .models import (
    CashDenomination,
    DutyFuelEntry,
    DutyLubricantEntry,
    DutyReport,
    Lubricant,
    PaymentCollection,
    ReconciliationAdjustment,
    ReconciliationSettings,
    Shift,
)


class ShiftAndDutyReportTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="owner",
            email="owner@example.test",
            password="test-password",
        )
        self.other_owner = User.objects.create_superuser(
            username="other-owner",
            email="other@example.test",
            password="test-password",
        )
        self.station = self.make_station(self.owner, "Central Fuel", "MF-001")
        self.second_station = self.make_station(self.owner, "North Fuel", "MF-002")
        self.other_station = self.make_station(self.other_owner, "Other Fuel", "OT-001")
        self.dsms = self.make_staff("dsms", User.Role.DSMS, self.owner)
        self.other_dsms = self.make_staff("other-dsms", User.Role.DSMS, self.owner)
        self.manager = self.make_staff("manager", User.Role.MANAGER, self.owner)
        self.other_manager = self.make_staff("other-manager", User.Role.MANAGER, self.other_owner)
        self.employee = self.make_employee(
            self.owner,
            self.dsms,
            self.station,
            "EMP-001",
        )
        self.second_employee = self.make_employee(
            self.owner,
            self.other_dsms,
            self.second_station,
            "EMP-002",
        )
        self.manager_employee = self.make_employee(
            self.owner,
            self.manager,
            None,
            "EMP-003",
        )
        self.other_manager_employee = self.make_employee(
            self.other_owner,
            self.other_manager,
            None,
            "EMP-004",
        )
        ManagerAssignment.objects.create(
            employee=self.manager_employee,
            station=self.station,
        )
        ManagerAssignment.objects.create(
            employee=self.other_manager_employee,
            station=self.other_station,
        )
        self.shift = Shift.objects.create(
            station=self.station,
            name="Morning",
            start_time=time(6, 0),
            end_time=time(14, 0),
        )
        self.second_shift = Shift.objects.create(
            station=self.second_station,
            name="Morning",
            start_time=time(6, 0),
            end_time=time(14, 0),
        )
        self.other_shift = Shift.objects.create(
            station=self.other_station,
            name="Morning",
            start_time=time(6, 0),
            end_time=time(14, 0),
        )
        self.fuel_type = FuelType.objects.get(code="MS")
        self.tank = Tank.objects.create(
            station=self.station,
            fuel_type=self.fuel_type,
            code="T-01",
            capacity_litres=10000,
        )
        self.nozzle = Nozzle.objects.create(
            station=self.station,
            tank=self.tank,
            code="N-01",
        )
        self.fuel_price = FuelPrice.objects.create(
            station=self.station,
            fuel_type=self.fuel_type,
            price_per_litre="100.50",
            effective_from=timezone.make_aware(
                datetime.combine(date(2026, 11, 5), time(0, 0)),
                timezone.get_current_timezone(),
            ),
            recorded_by=self.owner,
        )
        self.lubricant = Lubricant.objects.create(
            station=self.station,
            code="OIL-1",
            name="Engine Oil",
            unit="bottle",
            current_stock="10.000",
            selling_price="250.00",
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

    @staticmethod
    def make_employee(owner, user, station, code):
        return Employee.objects.create(
            owner=owner,
            user=user,
            employee_code=code,
            hire_date="2026-01-01",
            assigned_station=station,
        )

    def report_data(self, shift=None, token=None):
        return {
            "shift": (shift or self.shift).pk,
            "report_date": "2026-11-05",
            "notes": "Opening handover completed.",
            "submission_token": str(token or uuid.uuid4()),
        }

    def fuel_entry_data(
        self,
        *,
        nozzle=None,
        opening="1000.000",
        closing="1120.000",
        testing="2.000",
    ):
        return {
            "fuel_entries-TOTAL_FORMS": "1",
            "fuel_entries-INITIAL_FORMS": "0",
            "fuel_entries-MIN_NUM_FORMS": "0",
            "fuel_entries-MAX_NUM_FORMS": "50",
            "fuel_entries-0-nozzle": (nozzle or self.nozzle).pk,
            "fuel_entries-0-opening_reading": opening,
            "fuel_entries-0-closing_reading": closing,
            "fuel_entries-0-testing_litres": testing,
        }

    def lubricant_entry_data(self, *, product=None, quantity="2.000"):
        return {
            "lubricant_entries-TOTAL_FORMS": "1",
            "lubricant_entries-INITIAL_FORMS": "0",
            "lubricant_entries-MIN_NUM_FORMS": "0",
            "lubricant_entries-MAX_NUM_FORMS": "50",
            "lubricant_entries-0-lubricant": (product or self.lubricant).pk,
            "lubricant_entries-0-quantity": quantity,
        }

    def create_report(self, employee, shift, *, status=DutyReport.Status.DRAFT):
        return DutyReport.objects.create(
            station=shift.station,
            employee=employee,
            shift=shift,
            report_date="2026-11-05",
            status=status,
            submitted_at=(
                "2026-11-05T07:00:00Z"
                if status != DutyReport.Status.DRAFT
                else None
            ),
        )

    def create_report_with_fuel_sale(self, *, status=DutyReport.Status.DRAFT):
        report = self.create_report(self.employee, self.shift)
        DutyFuelEntry.objects.create(
            report=report,
            nozzle=self.nozzle,
            fuel_price=self.fuel_price,
            opening_reading="1000.000",
            closing_reading="1120.000",
            testing_litres="2.000",
            price_per_litre=Decimal("100.50"),
        )
        if status != DutyReport.Status.DRAFT:
            report.status = status
            report.submitted_at = timezone.now()
            report.save(update_fields=("status", "submitted_at", "updated_at"))
        return report

    def test_owner_can_create_shift_and_duplicate_name_is_rejected(self):
        self.client.force_login(self.owner)
        url = reverse("duties:shift-create", args=[self.station.pk])
        data = {
            "name": "Evening",
            "start_time": "14:00",
            "end_time": "22:00",
            "is_active": "on",
        }

        response = self.client.post(url, data)

        self.assertRedirects(
            response,
            reverse("duties:shift-list", args=[self.station.pk]),
        )
        shift = Shift.objects.get(station=self.station, name="Evening")
        self.assertEqual(shift.start_time, time(14, 0))

        response = self.client.post(
            url,
            {**data, "name": "morning"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("name", response.context["form"].errors)
        self.assertEqual(Shift.objects.filter(station=self.station).count(), 2)

    def test_owner_cannot_create_shift_for_suspended_station(self):
        self.station.is_active = False
        self.station.save(update_fields=("is_active", "updated_at"))
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("duties:shift-create", args=[self.station.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_station_shift_access_is_scoped_to_owner_and_manager_assignments(self):
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse("duties:shift-list", args=[self.station.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Morning")

        response = self.client.get(
            reverse("duties:shift-list", args=[self.second_station.pk])
        )
        self.assertEqual(response.status_code, 404)

        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("duties:shift-list", args=[self.other_station.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_dsms_can_create_draft_and_submit_it(self):
        self.client.force_login(self.dsms)
        create_response = self.client.post(
            reverse("duties:report-create"),
            self.report_data(),
        )

        report = DutyReport.objects.get(employee=self.employee)
        self.assertRedirects(
            create_response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        self.assertEqual(report.status, DutyReport.Status.DRAFT)

        entry_response = self.client.post(
            reverse("duties:report-fuel-entries", args=[report.pk]),
            self.fuel_entry_data(),
        )
        self.assertRedirects(
            entry_response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        entry = DutyFuelEntry.objects.get(report=report, nozzle=self.nozzle)
        self.assertEqual(entry.metered_litres, Decimal("120.000"))
        self.assertEqual(entry.actual_sale_litres, Decimal("118.000"))
        self.assertEqual(entry.price_per_litre, Decimal("100.50"))
        self.assertEqual(entry.sale_amount, Decimal("11859.00"))
        self.lubricant.refresh_from_db()
        self.assertEqual(self.lubricant.current_stock, Decimal("10.000"))

        lubricant_response = self.client.post(
            reverse("duties:report-lubricant-entries", args=[report.pk]),
            self.lubricant_entry_data(),
        )
        self.assertRedirects(
            lubricant_response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        lubricant_entry = DutyLubricantEntry.objects.get(report=report)
        self.assertEqual(lubricant_entry.unit_price, Decimal("250.00"))
        self.assertEqual(lubricant_entry.sale_amount, Decimal("500.00"))
        self.lubricant.refresh_from_db()
        self.assertEqual(self.lubricant.current_stock, Decimal("10.000"))

        detail_response = self.client.get(
            reverse("duties:report-detail", args=[report.pk])
        )
        self.assertContains(detail_response, "Edit draft")
        self.assertContains(detail_response, "₹11859.00")
        self.assertContains(detail_response, "₹12359.00")

        submit_response = self.client.post(
            reverse("duties:report-submit", args=[report.pk])
        )
        report.refresh_from_db()
        self.assertRedirects(
            submit_response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        self.assertEqual(report.status, DutyReport.Status.SUBMITTED)
        self.assertIsNotNone(report.submitted_at)
        self.lubricant.refresh_from_db()
        self.assertEqual(self.lubricant.current_stock, Decimal("8.000"))

        repeated_submit = self.client.post(
            reverse("duties:report-submit", args=[report.pk])
        )
        self.assertRedirects(
            repeated_submit,
            reverse("duties:report-detail", args=[report.pk]),
        )
        self.lubricant.refresh_from_db()
        self.assertEqual(self.lubricant.current_stock, Decimal("8.000"))

        edit_response = self.client.get(
            reverse("duties:report-update", args=[report.pk])
        )
        self.assertEqual(edit_response.status_code, 403)

    def test_repeated_create_post_with_same_token_is_idempotent(self):
        self.client.force_login(self.dsms)
        data = self.report_data(token=uuid.uuid4())
        create_url = reverse("duties:report-create")

        first_response = self.client.post(create_url, data)
        second_response = self.client.post(create_url, data)

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(DutyReport.objects.filter(employee=self.employee).count(), 1)
        self.assertEqual(first_response.url, second_response.url)

    def test_multiple_duty_reports_per_employee_per_day_are_allowed(self):
        self.client.force_login(self.dsms)

        first_response = self.client.post(
            reverse("duties:report-create"),
            self.report_data(),
        )
        second_response = self.client.post(
            reverse("duties:report-create"),
            self.report_data(),
        )

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(
            DutyReport.objects.filter(
                employee=self.employee,
                report_date="2026-11-05",
            ).count(),
            2,
        )

    def test_dsms_cannot_create_report_for_another_station_shift(self):
        self.client.force_login(self.dsms)

        response = self.client.post(
            reverse("duties:report-create"),
            self.report_data(shift=self.second_shift),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("shift", response.context["form"].errors)
        self.assertFalse(DutyReport.objects.exists())

    def test_readings_reject_closing_below_opening_and_testing_above_metered(self):
        report = self.create_report(self.employee, self.shift)
        self.client.force_login(self.dsms)
        entry_url = reverse("duties:report-fuel-entries", args=[report.pk])

        response = self.client.post(
            entry_url,
            self.fuel_entry_data(closing="999.000"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DutyFuelEntry.objects.exists())

        response = self.client.post(
            entry_url,
            self.fuel_entry_data(testing="121.000"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(DutyFuelEntry.objects.exists())

    def test_entry_selects_price_effective_at_shift_start(self):
        FuelPrice.objects.create(
            station=self.station,
            fuel_type=self.fuel_type,
            price_per_litre="101.00",
            effective_from=timezone.make_aware(
                datetime.combine(date(2026, 11, 5), time(8, 0)),
                timezone.get_current_timezone(),
            ),
            recorded_by=self.owner,
        )
        report = self.create_report(self.employee, self.shift)
        self.client.force_login(self.dsms)

        response = self.client.post(
            reverse("duties:report-fuel-entries", args=[report.pk]),
            self.fuel_entry_data(),
        )

        self.assertRedirects(
            response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        entry = DutyFuelEntry.objects.get(report=report)
        self.assertEqual(entry.fuel_price, self.fuel_price)
        self.assertEqual(entry.price_per_litre, Decimal("100.50"))

    def test_entry_requires_a_price_effective_at_shift_start(self):
        report = DutyReport.objects.create(
            station=self.station,
            employee=self.employee,
            shift=self.shift,
            report_date=date(2026, 11, 4),
        )
        self.client.force_login(self.dsms)

        response = self.client.post(
            reverse("duties:report-fuel-entries", args=[report.pk]),
            self.fuel_entry_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No fuel price is effective")
        self.assertFalse(DutyFuelEntry.objects.exists())

    def test_a_nozzle_cannot_be_entered_twice_on_one_report(self):
        report = self.create_report(self.employee, self.shift)
        data = self.fuel_entry_data()
        data["fuel_entries-TOTAL_FORMS"] = "2"
        data.update(
            {
                "fuel_entries-1-nozzle": self.nozzle.pk,
                "fuel_entries-1-opening_reading": "2000.000",
                "fuel_entries-1-closing_reading": "2100.000",
                "fuel_entries-1-testing_litres": "0.000",
            }
        )
        self.client.force_login(self.dsms)

        response = self.client.post(
            reverse("duties:report-fuel-entries", args=[report.pk]),
            data,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A nozzle can only be recorded once")
        self.assertFalse(DutyFuelEntry.objects.exists())

    def test_reading_save_keeps_historical_price_snapshot(self):
        report = self.create_report(self.employee, self.shift)
        entry = DutyFuelEntry.objects.create(
            report=report,
            nozzle=self.nozzle,
            fuel_price=self.fuel_price,
            opening_reading="1000.000",
            closing_reading="1120.000",
            testing_litres="2.000",
            price_per_litre=Decimal("100.50"),
        )
        FuelPrice.objects.create(
            station=self.station,
            fuel_type=self.fuel_type,
            price_per_litre="99.00",
            effective_from=timezone.make_aware(
                datetime.combine(date(2026, 11, 5), time(5, 0)),
                timezone.get_current_timezone(),
            ),
            recorded_by=self.owner,
        )

        entry.opening_reading = Decimal("1001.000")
        entry.save()

        entry.refresh_from_db()
        self.assertEqual(entry.fuel_price, self.fuel_price)
        self.assertEqual(entry.price_per_litre, Decimal("100.50"))
        self.assertEqual(entry.sale_amount, Decimal("11758.50"))

    def test_report_cannot_be_submitted_until_every_active_nozzle_has_readings(self):
        report = self.create_report(self.employee, self.shift)
        self.client.force_login(self.dsms)

        response = self.client.post(
            reverse("duties:report-submit", args=[report.pk]),
            follow=True,
        )

        report.refresh_from_db()
        self.assertEqual(report.status, DutyReport.Status.DRAFT)
        self.assertRedirects(
            response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        self.assertContains(response, "Add meter readings for every active nozzle")

    def test_submitted_report_fuel_entries_are_immutable(self):
        report = self.create_report(self.employee, self.shift)
        DutyFuelEntry.objects.create(
            report=report,
            nozzle=self.nozzle,
            fuel_price=self.fuel_price,
            opening_reading="1000.000",
            closing_reading="1120.000",
            testing_litres="2.000",
            price_per_litre=Decimal("100.50"),
        )
        report.status = DutyReport.Status.SUBMITTED
        report.submitted_at = timezone.now()
        report.save()
        self.client.force_login(self.dsms)

        response = self.client.get(
            reverse("duties:report-fuel-entries", args=[report.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_dsms_report_history_is_own_reports_only(self):
        own_report = self.create_report(self.employee, self.shift)
        self.create_report(self.second_employee, self.second_shift)
        self.client.force_login(self.dsms)

        response = self.client.get(reverse("duties:report-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own_report.station.name)
        self.assertNotContains(response, self.second_station.name)

    def test_dsms_cannot_view_another_employees_report(self):
        other_report = self.create_report(self.second_employee, self.second_shift)
        self.client.force_login(self.dsms)

        response = self.client.get(
            reverse("duties:report-detail", args=[other_report.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_manager_report_list_contains_only_assigned_station_reports(self):
        assigned_report = self.create_report(self.employee, self.shift)
        self.create_report(self.second_employee, self.second_shift)
        self.client.force_login(self.manager)

        response = self.client.get(reverse("duties:report-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, assigned_report.station.name)
        self.assertNotContains(response, self.second_station.name)

    def test_owner_cannot_view_another_owners_report(self):
        other_employee = self.make_employee(
            self.other_owner,
            self.make_staff("other-dsms-2", User.Role.DSMS, self.other_owner),
            self.other_station,
            "EMP-005",
        )
        other_report = self.create_report(other_employee, self.other_shift)
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("duties:report-detail", args=[other_report.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_owner_can_add_lubricant_product_and_duplicate_code_is_rejected(self):
        self.client.force_login(self.owner)
        url = reverse("duties:lubricant-create", args=[self.station.pk])
        product_data = {
            "code": "oil-2",
            "name": "Gear Oil",
            "unit": "can",
            "current_stock": "4.000",
            "selling_price": "375.00",
            "is_active": "on",
        }

        response = self.client.post(url, product_data)

        self.assertRedirects(
            response,
            reverse("duties:lubricant-list", args=[self.station.pk]),
        )
        product = Lubricant.objects.get(station=self.station, code="OIL-2")
        self.assertEqual(product.current_stock, Decimal("4.000"))

        response = self.client.post(
            url,
            {**product_data, "code": "oil-1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("code", response.context["form"].errors)
        self.assertEqual(Lubricant.objects.filter(station=self.station).count(), 2)

    def test_lubricant_stock_shortage_keeps_report_draft_and_stock_unchanged(self):
        self.lubricant.current_stock = Decimal("1.000")
        self.lubricant.save(update_fields=("current_stock", "updated_at"))
        report = self.create_report(self.employee, self.shift)
        DutyFuelEntry.objects.create(
            report=report,
            nozzle=self.nozzle,
            fuel_price=self.fuel_price,
            opening_reading="1000.000",
            closing_reading="1120.000",
            testing_litres="2.000",
            price_per_litre=Decimal("100.50"),
        )
        DutyLubricantEntry.objects.create(
            report=report,
            lubricant=self.lubricant,
            quantity=Decimal("2.000"),
            unit_price=Decimal("250.00"),
        )
        self.client.force_login(self.dsms)

        response = self.client.post(
            reverse("duties:report-submit", args=[report.pk]),
            follow=True,
        )

        report.refresh_from_db()
        self.lubricant.refresh_from_db()
        self.assertEqual(report.status, DutyReport.Status.DRAFT)
        self.assertEqual(self.lubricant.current_stock, Decimal("1.000"))
        self.assertContains(response, "Insufficient lubricant stock")

    def test_dsms_cannot_sell_another_stations_lubricant(self):
        other_product = Lubricant.objects.create(
            station=self.second_station,
            code="OIL-2",
            name="Other Engine Oil",
            unit="bottle",
            current_stock="5.000",
            selling_price="300.00",
        )
        report = self.create_report(self.employee, self.shift)
        self.client.force_login(self.dsms)

        response = self.client.post(
            reverse("duties:report-lubricant-entries", args=[report.pk]),
            self.lubricant_entry_data(product=other_product),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("lubricant", response.context["formset"].forms[0].errors)
        self.assertFalse(DutyLubricantEntry.objects.exists())

    def test_existing_lubricant_sale_keeps_its_price_snapshot(self):
        report = self.create_report(self.employee, self.shift)
        self.client.force_login(self.dsms)
        create_response = self.client.post(
            reverse("duties:report-lubricant-entries", args=[report.pk]),
            self.lubricant_entry_data(),
        )
        self.assertRedirects(
            create_response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        entry = DutyLubricantEntry.objects.get(report=report)

        self.lubricant.selling_price = Decimal("300.00")
        self.lubricant.save(update_fields=("selling_price", "updated_at"))
        update_data = {
            "lubricant_entries-TOTAL_FORMS": "1",
            "lubricant_entries-INITIAL_FORMS": "1",
            "lubricant_entries-MIN_NUM_FORMS": "0",
            "lubricant_entries-MAX_NUM_FORMS": "50",
            "lubricant_entries-0-id": entry.pk,
            "lubricant_entries-0-report": report.pk,
            "lubricant_entries-0-lubricant": self.lubricant.pk,
            "lubricant_entries-0-quantity": "3.000",
        }

        response = self.client.post(
            reverse("duties:report-lubricant-entries", args=[report.pk]),
            update_data,
        )

        self.assertRedirects(
            response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        entry.refresh_from_db()
        self.assertEqual(entry.unit_price, Decimal("250.00"))
        self.assertEqual(entry.sale_amount, Decimal("750.00"))

    def test_dsms_collection_form_totals_cash_and_reconciles_report_sales(self):
        report = self.create_report_with_fuel_sale()
        self.client.force_login(self.dsms)
        data = {
            "cash_500_count": "20",
            "cash_200_count": "0",
            "cash_100_count": "0",
            "cash_50_count": "0",
            "cash_20_count": "0",
            "cash_10_count": "0",
            "cash_5_coin_count": "0",
            "cash_2_coin_count": "0",
            "cash_1_coin_count": "0",
            "upi": "1859.00",
            "debit_card": "0",
            "credit_card": "0",
            "credit_sales": "0",
            "advances": "0",
            "otp": "0",
            "expenses": "125.00",
            "expense_notes": "Cleaning supplies",
        }

        response = self.client.post(
            reverse("duties:report-payment-collection", args=[report.pk]),
            data,
        )

        self.assertRedirects(
            response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        collection = PaymentCollection.objects.get(report=report)
        self.assertEqual(collection.cash_total, Decimal("10000.00"))
        self.assertEqual(collection.duty_sales_total, Decimal("11859.00"))
        self.assertEqual(
            collection.reconciliation_summary()["actual_collections"],
            Decimal("11859.00"),
        )
        self.assertEqual(
            collection.reconciliation_summary()["status"],
            PaymentCollection.ReconciliationStatus.MATCHED,
        )
        self.assertEqual(collection.expenses, Decimal("125.00"))
        self.assertEqual(CashDenomination.objects.filter(collection=collection).count(), 9)

        detail = self.client.get(reverse("duties:report-detail", args=[report.pk]))
        self.assertContains(detail, "Matched")
        self.assertContains(detail, "Expenses recorded separately")

    def test_credit_sales_reduce_expected_collections_and_cannot_exceed_sales(self):
        report = self.create_report_with_fuel_sale()
        self.client.force_login(self.dsms)
        response = self.client.post(
            reverse("duties:report-payment-collection", args=[report.pk]),
            {
                "upi": "10000.00",
                "debit_card": "0",
                "credit_card": "0",
                "credit_sales": "12000.00",
                "advances": "0",
                "otp": "0",
                "expenses": "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("credit_sales", response.context["form"].errors)
        self.assertFalse(PaymentCollection.objects.exists())

        collection = PaymentCollection.objects.create(
            report=report,
            upi=Decimal("10000.00"),
            credit_sales=Decimal("1859.00"),
        )
        summary = collection.reconciliation_summary()
        self.assertEqual(summary["expected_collections"], Decimal("10000.00"))
        self.assertEqual(summary["actual_collections"], Decimal("10000.00"))
        self.assertEqual(summary["authorized_adjustments"], Decimal("0.00"))

    def test_reconciliation_settings_control_inclusions_and_tolerance(self):
        report = self.create_report_with_fuel_sale()
        settings = ReconciliationSettings.objects.create(
            station=self.station,
            include_advances=False,
            include_otp=False,
            tolerance_amount=Decimal("1.00"),
        )
        collection = PaymentCollection.objects.create(
            report=report,
            upi=Decimal("1858.00"),
            advances=Decimal("500.00"),
            otp=Decimal("400.00"),
        )
        CashDenomination.objects.create(
            collection=collection,
            denomination=500,
            count=20,
        )

        summary = collection.reconciliation_summary(settings)
        self.assertEqual(summary["actual_collections"], Decimal("11858.00"))
        self.assertEqual(summary["variance"], Decimal("-1.00"))
        self.assertEqual(summary["status"], PaymentCollection.ReconciliationStatus.MATCHED)

        settings.tolerance_amount = Decimal("0.00")
        settings.save()
        summary = collection.reconciliation_summary(settings)
        self.assertEqual(summary["status"], PaymentCollection.ReconciliationStatus.SHORTAGE)

        settings.include_advances = True
        settings.include_otp = True
        settings.save()
        summary = collection.reconciliation_summary(settings)
        self.assertEqual(summary["actual_collections"], Decimal("12758.00"))
        self.assertEqual(summary["status"], PaymentCollection.ReconciliationStatus.EXCESS)

    def test_expenses_are_not_counted_as_collections_or_hidden_as_adjustments(self):
        report = self.create_report_with_fuel_sale()
        collection = PaymentCollection.objects.create(
            report=report,
            upi=Decimal("11859.00"),
            expenses=Decimal("2500.00"),
        )

        summary = collection.reconciliation_summary()

        self.assertEqual(summary["actual_collections"], Decimal("11859.00"))
        self.assertEqual(summary["authorized_adjustments"], Decimal("0.00"))
        self.assertEqual(summary["variance"], Decimal("0.00"))

    def test_owner_can_configure_reconciliation_policy_for_own_station(self):
        self.client.force_login(self.owner)
        url = reverse("duties:reconciliation-settings", args=[self.station.pk])
        response = self.client.post(
            url,
            {
                "include_advances": "on",
                "tolerance_amount": "2.50",
            },
        )
        self.assertRedirects(response, url)
        settings = ReconciliationSettings.objects.get(station=self.station)
        self.assertTrue(settings.include_advances)
        self.assertFalse(settings.include_otp)
        self.assertEqual(settings.tolerance_amount, Decimal("2.50"))

        self.client.force_login(self.other_owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_authorized_adjustment_is_audited_and_does_not_change_collections(self):
        report = self.create_report_with_fuel_sale()
        collection = PaymentCollection.objects.create(
            report=report,
            upi=Decimal("11759.00"),
        )
        report.status = DutyReport.Status.SUBMITTED
        report.submitted_at = timezone.now()
        report.save(update_fields=("status", "submitted_at", "updated_at"))
        self.client.force_login(self.manager)
        url = reverse("duties:reconciliation-adjustment-create", args=[report.pk])

        response = self.client.post(
            url,
            {"amount": "100.00", "reason": "Verified bank receipt"},
        )

        self.assertRedirects(
            response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        adjustment = ReconciliationAdjustment.objects.get(report=report)
        self.assertEqual(adjustment.authorized_by, self.manager)
        self.assertEqual(adjustment.amount, Decimal("100.00"))
        summary = collection.reconciliation_summary()
        self.assertEqual(summary["actual_collections"], Decimal("11759.00"))
        self.assertEqual(summary["authorized_adjustments"], Decimal("100.00"))
        self.assertEqual(summary["variance"], Decimal("0.00"))
        self.assertEqual(summary["status"], PaymentCollection.ReconciliationStatus.MATCHED)

        adjustment.amount = Decimal("200.00")
        with self.assertRaises(ValidationError):
            adjustment.save()

    def test_dsms_cannot_authorize_adjustments_or_edit_submitted_collections(self):
        report = self.create_report_with_fuel_sale(status=DutyReport.Status.SUBMITTED)
        self.client.force_login(self.dsms)

        adjustment_response = self.client.get(
            reverse("duties:reconciliation-adjustment-create", args=[report.pk])
        )
        collection_response = self.client.get(
            reverse("duties:report-payment-collection", args=[report.pk])
        )

        self.assertEqual(adjustment_response.status_code, 403)
        self.assertEqual(collection_response.status_code, 403)

    def test_owner_cannot_authorize_adjustment_for_another_owners_station(self):
        other_employee = self.make_employee(
            self.other_owner,
            self.make_staff("other-dsms-3", User.Role.DSMS, self.other_owner),
            self.other_station,
            "EMP-006",
        )
        report = self.create_report(other_employee, self.other_shift)
        report.status = DutyReport.Status.SUBMITTED
        report.submitted_at = timezone.now()
        report.save(update_fields=("status", "submitted_at", "updated_at"))
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("duties:reconciliation-adjustment-create", args=[report.pk])
        )

        self.assertEqual(response.status_code, 404)
