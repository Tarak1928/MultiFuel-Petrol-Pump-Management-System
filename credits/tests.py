import uuid
from datetime import date, datetime, time
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from duties.models import DutyFuelEntry, DutyReport, PaymentCollection, Shift
from employees.models import Employee, ManagerAssignment
from fuel.models import FuelPrice, FuelType, Nozzle, Tank
from stations.models import Station

from .models import (
    Advance,
    CreditCustomer,
    CreditRepayment,
    CreditSale,
    OTPTransaction,
)


class CreditOperationsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="credit-owner",
            email="credit-owner@example.test",
            password="test-password-123",
        )
        self.other_owner = User.objects.create_superuser(
            username="credit-other-owner",
            email="credit-other-owner@example.test",
            password="test-password-123",
        )
        self.station = self.make_station(self.owner, "Central Credit", "CC-01")
        self.other_station = self.make_station(
            self.other_owner,
            "Other Credit",
            "CC-02",
        )
        self.dsms = self.make_staff("credit-dsms", User.Role.DSMS, self.owner)
        self.manager = self.make_staff("credit-manager", User.Role.MANAGER, self.owner)
        self.other_manager = self.make_staff(
            "credit-other-manager",
            User.Role.MANAGER,
            self.other_owner,
        )
        self.employee = self.make_employee(self.owner, self.dsms, self.station, "CE-01")
        self.manager_employee = self.make_employee(
            self.owner,
            self.manager,
            None,
            "CE-02",
        )
        self.other_manager_employee = self.make_employee(
            self.other_owner,
            self.other_manager,
            None,
            "CE-03",
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
            code="CT-01",
            capacity_litres=10000,
        )
        self.nozzle = Nozzle.objects.create(
            station=self.station,
            tank=self.tank,
            code="CN-01",
        )
        self.price = FuelPrice.objects.create(
            station=self.station,
            fuel_type=self.fuel_type,
            price_per_litre=Decimal("100.50"),
            effective_from=timezone.make_aware(
                datetime.combine(date(2026, 11, 5), time(0, 0)),
                timezone.get_current_timezone(),
            ),
            recorded_by=self.owner,
        )
        self.customer = self.make_customer(
            self.station,
            "TRUCK-01",
            "Fleet Customer",
            credit_limit="10000.00",
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
            email=f"{username}@example.test",
            password="test-password-123",
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
            hire_date=date(2026, 1, 1),
            assigned_station=station,
        )

    @staticmethod
    def make_customer(station, code, name, *, credit_limit="10000.00"):
        return CreditCustomer.objects.create(
            station=station,
            customer_code=code,
            name=name,
            phone="9876543210",
            vehicle_number="MH12AB1234",
            vehicle_type="Truck",
            credit_limit=credit_limit,
            credit_days=14,
        )

    def create_report(self, *, submitted=False, station=None, employee=None, shift=None):
        station = station or self.station
        employee = employee or self.employee
        shift = shift or self.shift
        report = DutyReport.objects.create(
            station=station,
            employee=employee,
            shift=shift,
            report_date=date(2026, 11, 5),
        )
        if station == self.station:
            DutyFuelEntry.objects.create(
                report=report,
                nozzle=self.nozzle,
                fuel_price=self.price,
                opening_reading=Decimal("1000.000"),
                closing_reading=Decimal("1120.000"),
                testing_litres=Decimal("2.000"),
                price_per_litre=Decimal("100.50"),
            )
        if submitted:
            report.status = DutyReport.Status.SUBMITTED
            report.submitted_at = timezone.now()
            report.save(update_fields=("status", "submitted_at", "updated_at"))
        return report

    def create_sale(self, report, *, customer=None, litres="50.000"):
        return CreditSale.objects.create(
            report=report,
            customer=customer or self.customer,
            fuel_type=self.fuel_type,
            fuel_price=self.price,
            litres=Decimal(litres),
            price_per_litre=self.price.price_per_litre,
            vehicle_number=(customer or self.customer).vehicle_number,
            due_date=report.report_date.replace(day=19),
            recorded_by=report.employee.user,
        )

    def test_owner_can_create_station_customer_and_manager_can_view_only_assigned_station(self):
        self.client.force_login(self.owner)
        url = reverse("credits:customer-create", args=[self.station.pk])
        response = self.client.post(
            url,
            {
                "customer_code": " fleet-02 ",
                "name": "Second Fleet",
                "phone": "9123456789",
                "address": "Industrial Road",
                "vehicle_number": "mh14xy6789",
                "vehicle_type": "Tanker",
                "credit_limit": "5000.00",
                "credit_days": "30",
                "is_active": "on",
            },
        )

        customer = CreditCustomer.objects.get(customer_code="FLEET-02")
        self.assertRedirects(
            response,
            reverse("credits:customer-detail", args=[customer.pk]),
        )
        self.assertEqual(customer.vehicle_number, "MH14XY6789")

        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(
                reverse("credits:customer-list", args=[self.station.pk])
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                reverse("credits:customer-list", args=[self.other_station.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("credits:customer-create", args=[self.station.pk])).status_code,
            403,
        )

    def test_credit_sale_uses_shift_price_snapshot_and_is_idempotent(self):
        report = self.create_report()
        self.client.force_login(self.dsms)
        url = reverse("credits:report-credit-sale-create", args=[report.pk])
        token = str(uuid.uuid4())
        data = {
            "customer": self.customer.pk,
            "fuel_type": self.fuel_type.pk,
            "litres": "50.000",
            "submission_token": token,
        }

        first = self.client.post(url, data)
        repeated = self.client.post(url, data)

        sale = CreditSale.objects.get(report=report)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(CreditSale.objects.filter(report=report).count(), 1)
        self.assertEqual(sale.price_per_litre, Decimal("100.50"))
        self.assertEqual(sale.sale_amount, Decimal("5025.00"))
        self.assertEqual(sale.vehicle_number, "MH12AB1234")
        self.assertEqual(sale.due_date, date(2026, 11, 19))
        collection = PaymentCollection.objects.get(report=report)
        self.assertEqual(collection.credit_sales_total, Decimal("5025.00"))
        summary = collection.reconciliation_summary()
        self.assertEqual(summary["expected_collections"], Decimal("6834.00"))

    def test_credit_sale_enforces_credit_limit_and_report_fuel_volume(self):
        report = self.create_report()
        limited_customer = self.make_customer(
            self.station,
            "LIMIT-01",
            "Limited Customer",
            credit_limit="1000.00",
        )
        self.client.force_login(self.dsms)
        url = reverse("credits:report-credit-sale-create", args=[report.pk])

        response = self.client.post(
            url,
            {
                "customer": limited_customer.pk,
                "fuel_type": self.fuel_type.pk,
                "litres": "20.000",
                "submission_token": str(uuid.uuid4()),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("customer", response.context["form"].errors)
        self.assertFalse(CreditSale.objects.exists())

        response = self.client.post(
            url,
            {
                "customer": self.customer.pk,
                "fuel_type": self.fuel_type.pk,
                "litres": "200.000",
                "submission_token": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("litres", response.context["form"].errors)
        self.assertFalse(CreditSale.objects.exists())

    def test_credit_sale_can_be_corrected_only_while_report_is_editable(self):
        report = self.create_report()
        sale = self.create_sale(report, litres="10.000")
        self.client.force_login(self.dsms)
        url = reverse("credits:credit-sale-update", args=[sale.pk])
        response = self.client.post(
            url,
            {
                "customer": self.customer.pk,
                "fuel_type": self.fuel_type.pk,
                "litres": "20.000",
                "submission_token": str(sale.submission_token),
            },
        )

        self.assertRedirects(
            response,
            reverse("duties:report-detail", args=[report.pk]),
        )
        sale.refresh_from_db()
        self.assertEqual(sale.litres, Decimal("20.000"))
        self.assertEqual(sale.sale_amount, Decimal("2010.00"))
        self.assertEqual(CreditSale.objects.filter(report=report).count(), 1)

        response = self.client.post(
            url,
            {
                "customer": self.customer.pk,
                "fuel_type": self.fuel_type.pk,
                "litres": "30.000",
                "submission_token": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("submission_token", response.context["form"].errors)
        sale.refresh_from_db()
        self.assertEqual(sale.litres, Decimal("20.000"))

        report.status = DutyReport.Status.SUBMITTED
        report.submitted_at = timezone.now()
        report.save(update_fields=("status", "submitted_at", "updated_at"))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_credit_repayments_reduce_balance_without_becoming_new_sales(self):
        report = self.create_report()
        sale = self.create_sale(report)
        collection = PaymentCollection.objects.create(report=report)
        report.status = DutyReport.Status.SUBMITTED
        report.submitted_at = timezone.now()
        report.save(update_fields=("status", "submitted_at", "updated_at"))
        self.client.force_login(self.manager)
        url = reverse("credits:repayment-create", args=[sale.pk])

        partial_response = self.client.post(
            url,
            {
                "amount": "2000.00",
                "payment_method": CreditRepayment.PaymentMethod.UPI,
                "reference": "UPI-PART-1",
                "submission_token": str(uuid.uuid4()),
            },
        )

        sale.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(partial_response.status_code, 302)
        self.assertEqual(sale.outstanding_amount, Decimal("3025.00"))
        self.assertEqual(self.customer.outstanding_balance, Decimal("3025.00"))
        self.assertEqual(collection.duty_sales_total, Decimal("11859.00"))
        self.assertEqual(collection.reconciliation_summary()["actual_collections"], Decimal("0.00"))
        self.assertEqual(CreditRepayment.objects.count(), 1)

        full_response = self.client.post(
            url,
            {
                "amount": "3025.00",
                "payment_method": CreditRepayment.PaymentMethod.CASH,
                "reference": "CASH-FINAL-1",
                "submission_token": str(uuid.uuid4()),
            },
        )
        self.assertEqual(full_response.status_code, 302)
        sale.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(sale.outstanding_amount, Decimal("0.00"))
        self.assertEqual(self.customer.outstanding_balance, Decimal("0.00"))
        self.assertEqual(CreditRepayment.objects.count(), 2)

    def test_overpayment_and_dsms_repayment_are_rejected(self):
        report = self.create_report()
        sale = self.create_sale(report, litres="10.000")
        report.status = DutyReport.Status.SUBMITTED
        report.submitted_at = timezone.now()
        report.save(update_fields=("status", "submitted_at", "updated_at"))
        self.client.force_login(self.manager)
        url = reverse("credits:repayment-create", args=[sale.pk])
        response = self.client.post(
            url,
            {
                "amount": "6000.00",
                "payment_method": CreditRepayment.PaymentMethod.CASH,
                "reference": "",
                "submission_token": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("amount", response.context["form"].errors)
        self.assertFalse(CreditRepayment.objects.exists())

        self.client.force_login(self.dsms)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_advance_and_otp_records_flow_into_reconciliation_once(self):
        report = self.create_report()
        self.client.force_login(self.dsms)

        advance_url = reverse("credits:report-advance-list", args=[report.pk])
        advance_response = self.client.post(
            advance_url,
            {
                "amount": "500.00",
                "reference": "ADV-001",
                "description": "Carry-forward customer advance",
            },
        )
        otp_url = reverse("credits:report-otp-list", args=[report.pk])
        otp_response = self.client.post(
            otp_url,
            {
                "transaction_reference": "GW-OTP-001",
                "amount": "250.00",
                "provider": "Payment gateway",
            },
        )

        self.assertEqual(advance_response.status_code, 302)
        self.assertEqual(otp_response.status_code, 302)
        self.assertEqual(Advance.objects.filter(report=report).count(), 1)
        self.assertEqual(OTPTransaction.objects.filter(report=report).count(), 1)
        collection = PaymentCollection.objects.get(report=report)
        self.assertEqual(collection.advances_total, Decimal("500.00"))
        self.assertEqual(collection.otp_total, Decimal("250.00"))
        self.assertEqual(
            collection.reconciliation_summary()["actual_collections"],
            Decimal("750.00"),
        )

        duplicate = self.client.post(
            otp_url,
            {
                "transaction_reference": "GW-OTP-001",
                "amount": "250.00",
                "provider": "Payment gateway",
            },
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn(
            "transaction_reference",
            duplicate.context["form"].errors,
        )
        self.assertEqual(OTPTransaction.objects.filter(report=report).count(), 1)

        other_report = self.create_report()
        duplicated_reference = self.client.post(
            reverse("credits:report-otp-list", args=[other_report.pk]),
            {
                "transaction_reference": "GW-OTP-001",
                "amount": "250.00",
                "provider": "Payment gateway",
            },
        )
        self.assertEqual(duplicated_reference.status_code, 200)
        self.assertEqual(OTPTransaction.objects.count(), 1)

    def test_report_and_customer_station_boundaries_are_enforced(self):
        other_customer = self.make_customer(
            self.other_station,
            "OTHER-01",
            "Other Station",
        )
        report = self.create_report()
        self.client.force_login(self.dsms)

        response = self.client.post(
            reverse("credits:report-credit-sale-create", args=[report.pk]),
            {
                "customer": other_customer.pk,
                "fuel_type": self.fuel_type.pk,
                "litres": "5.000",
                "submission_token": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("customer", response.context["form"].errors)
        self.assertFalse(CreditSale.objects.exists())

        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("credits:customer-detail", args=[other_customer.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_overdue_report_and_customer_statement_show_remaining_balance(self):
        report = self.create_report()
        sale = self.create_sale(report, litres="10.000")
        sale.due_date = date(2026, 10, 1)
        sale.save()
        report.status = DutyReport.Status.SUBMITTED
        report.submitted_at = timezone.now()
        report.save(update_fields=("status", "submitted_at", "updated_at"))
        CreditRepayment.objects.create(
            sale=sale,
            amount=Decimal("200.00"),
            payment_method=CreditRepayment.PaymentMethod.BANK_TRANSFER,
            received_by=self.owner,
        )
        self.client.force_login(self.owner)

        overdue_response = self.client.get(
            reverse("credits:overdue-report", args=[self.station.pk])
        )
        statement_response = self.client.get(
            reverse("credits:customer-detail", args=[self.customer.pk])
        )

        self.assertContains(overdue_response, "₹805.00")
        self.assertContains(statement_response, "₹805.00")
        self.assertContains(statement_response, "Bank transfer")
