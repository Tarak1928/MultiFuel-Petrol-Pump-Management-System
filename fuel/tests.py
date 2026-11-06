from datetime import date
from decimal import Decimal
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from employees.models import Employee, ManagerAssignment
from stations.models import Station

from .models import (
    FuelDelivery,
    FuelInventoryAdjustment,
    FuelPrice,
    FuelType,
    InventoryLedger,
    Nozzle,
    Tank,
)


class FuelSetupTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="owner",
            email="owner@example.test",
            password="A-safe-test-password-412!",
        )
        self.other_owner = User.objects.create_superuser(
            username="other-owner",
            email="other@example.test",
            password="A-safe-test-password-412!",
        )
        self.station = self.make_station(self.owner, "Central Fuel", "MF-001")
        self.other_station = self.make_station(
            self.other_owner,
            "Other Fuel",
            "OT-001",
        )
        self.ms = FuelType.objects.get(code="MS")
        self.hsd = FuelType.objects.get(code="HSD")

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

    def post_tank(self, station, fuel_type, code="T-01", **extra):
        data = {
            "fuel_type": fuel_type.pk,
            "code": code,
            "capacity_litres": "10000.000",
            "current_stock_litres": "2500.000",
            "reorder_level_litres": "1000.000",
            "is_active": "on",
        }
        data.update(extra)
        return self.client.post(
            reverse("fuel:tank-create", args=[station.pk]),
            data,
        )

    def test_default_ms_and_hsd_types_are_available(self):
        self.assertEqual(self.ms.name, "MS (Petrol)")
        self.assertEqual(self.hsd.name, "HSD (Diesel)")
        self.assertIsNone(self.ms.owner_id)
        self.assertIsNone(self.hsd.owner_id)

    def test_owner_can_add_private_fuel_type(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("fuel:fuel-type-create"),
            {"name": "Premium Petrol", "code": "xp-95"},
        )

        self.assertRedirects(response, reverse("fuel:fuel-type-list"))
        fuel_type = FuelType.objects.get(code="XP-95")
        self.assertEqual(fuel_type.owner, self.owner)

    def test_custom_fuel_types_are_not_visible_to_other_owners(self):
        private_type = FuelType.objects.create(
            name="Owner Blend",
            code="OWN-1",
            owner=self.owner,
        )
        self.client.force_login(self.other_owner)

        response = self.client.get(reverse("fuel:fuel-type-list"))

        self.assertContains(response, "MS (Petrol)")
        self.assertNotContains(response, private_type.name)

    def test_manager_cannot_manage_fuel_setup(self):
        manager = User.objects.create_user(
            username="manager",
            password="A-safe-test-password-412!",
            role=User.Role.MANAGER,
            is_approved=True,
        )
        self.client.force_login(manager)

        response = self.client.get(
            reverse("fuel:station-fuel", args=[self.station.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_owner_cannot_open_another_owners_fuel_setup(self):
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("fuel:station-fuel", args=[self.other_station.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_owner_can_create_tank_and_code_is_normalized(self):
        self.client.force_login(self.owner)

        response = self.post_tank(self.station, self.ms, code="t-01")

        self.assertRedirects(
            response,
            reverse("fuel:station-fuel", args=[self.station.pk]),
        )
        tank = Tank.objects.get(station=self.station)
        self.assertEqual(tank.code, "T-01")
        self.assertEqual(tank.current_stock_litres, 2500)
        opening = InventoryLedger.objects.get(
            tank=tank,
            transaction_type=InventoryLedger.TransactionType.OPENING,
        )
        self.assertEqual(opening.quantity_delta, Decimal("2500.000"))

    def test_tank_stock_is_read_only_after_initial_setup(self):
        tank = Tank.objects.create(
            station=self.station,
            fuel_type=self.ms,
            code="T-01",
            capacity_litres=10000,
            current_stock_litres=2500,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("fuel:tank-update", args=[self.station.pk, tank.pk]),
            {
                "fuel_type": self.ms.pk,
                "code": tank.code,
                "capacity_litres": "10000.000",
                "current_stock_litres": "0.000",
                "reorder_level_litres": "1000.000",
                "is_active": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("fuel:station-fuel", args=[self.station.pk]),
        )
        tank.refresh_from_db()
        self.assertEqual(tank.current_stock_litres, Decimal("2500.000"))

    def test_owner_records_invoice_delivery_and_stock_ledger_atomically(self):
        tank = Tank.objects.create(
            station=self.station,
            fuel_type=self.ms,
            code="T-01",
            capacity_litres=10000,
            current_stock_litres=2500,
        )
        self.client.force_login(self.owner)
        url = reverse("fuel:fuel-delivery-create", args=[self.station.pk])
        data = {
            "tank": tank.pk,
            "invoice_number": " inv-2026-01 ",
            "invoice_line_number": "1",
            "supplier_name": "Reliable Fuel Supply",
            "supplier_gstin": "27ABCDE1234F1Z5",
            "supplier_phone": "9876543210",
            "supplier_address": "Pune depot",
            "invoice_date": "2026-11-05",
            "received_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            "quantity_litres": "500.000",
            "cost_per_litre": "90.25",
        }

        response = self.client.post(url, data)

        self.assertRedirects(
            response,
            reverse("fuel:station-inventory", args=[self.station.pk]),
        )
        delivery = FuelDelivery.objects.get(station=self.station)
        tank.refresh_from_db()
        self.assertEqual(delivery.invoice_number, "INV-2026-01")
        self.assertEqual(delivery.total_cost, Decimal("45125.00"))
        self.assertEqual(tank.current_stock_litres, Decimal("3000.000"))
        ledger = InventoryLedger.objects.get(delivery=delivery)
        self.assertEqual(ledger.quantity_delta, Decimal("500.000"))

        duplicate = self.client.post(url, data)
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn("invoice_number", duplicate.context["form"].errors)
        self.assertEqual(FuelDelivery.objects.count(), 1)

    def test_delivery_cannot_exceed_tank_capacity(self):
        tank = Tank.objects.create(
            station=self.station,
            fuel_type=self.ms,
            code="T-01",
            capacity_litres=10000,
            current_stock_litres=9500,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("fuel:fuel-delivery-create", args=[self.station.pk]),
            {
                "tank": tank.pk,
                "invoice_number": "INV-OVER",
                "invoice_line_number": "1",
                "supplier_name": "Reliable Fuel Supply",
                "invoice_date": "2026-11-05",
                "received_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
                "quantity_litres": "600.000",
                "cost_per_litre": "90.25",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("quantity_litres", response.context["form"].errors)
        tank.refresh_from_db()
        self.assertEqual(tank.current_stock_litres, Decimal("9500.000"))
        self.assertFalse(FuelDelivery.objects.exists())

    def test_manager_adjustments_require_owner_approval_before_stock_changes(self):
        tank = Tank.objects.create(
            station=self.station,
            fuel_type=self.ms,
            code="T-01",
            capacity_litres=10000,
            current_stock_litres=2500,
        )
        manager = User.objects.create_user(
            username="manager",
            password="test-password",
            role=User.Role.MANAGER,
            is_approved=True,
            requested_owner=self.owner,
            approved_by=self.owner,
        )
        employee = Employee.objects.create(
            owner=self.owner,
            user=manager,
            employee_code="MGR-01",
            hire_date=date(2026, 1, 1),
        )
        ManagerAssignment.objects.create(employee=employee, station=self.station)
        adjustment_url = reverse(
            "fuel:inventory-adjustment-create",
            args=[self.station.pk],
        )
        payload = {
            "tank": tank.pk,
            "adjustment_type": FuelInventoryAdjustment.Type.EVAPORATION,
            "quantity_delta": "-10.000",
            "reason": "Measured evaporation loss",
        }
        self.client.force_login(manager)
        response = self.client.post(adjustment_url, payload)

        self.assertRedirects(
            response,
            reverse("fuel:station-inventory", args=[self.station.pk]),
        )
        adjustment = FuelInventoryAdjustment.objects.get()
        self.assertEqual(adjustment.status, FuelInventoryAdjustment.Status.PENDING)
        tank.refresh_from_db()
        self.assertEqual(tank.current_stock_litres, Decimal("2500.000"))

        self.client.force_login(self.owner)
        decision_url = reverse(
            "fuel:inventory-adjustment-decide",
            args=[self.station.pk, adjustment.pk],
        )
        approved = self.client.post(
            decision_url,
            {"action": "approve", "decision_note": "Verified tank dip."},
        )
        self.assertRedirects(
            approved,
            reverse("fuel:station-inventory", args=[self.station.pk]),
        )
        adjustment.refresh_from_db()
        tank.refresh_from_db()
        self.assertEqual(adjustment.status, FuelInventoryAdjustment.Status.APPROVED)
        self.assertEqual(adjustment.decided_by, self.owner)
        self.assertEqual(tank.current_stock_litres, Decimal("2490.000"))
        self.assertEqual(
            InventoryLedger.objects.get(adjustment=adjustment).quantity_delta,
            Decimal("-10.000"),
        )

    def test_manager_inventory_access_is_station_scoped_and_shows_low_stock(self):
        Tank.objects.create(
            station=self.station,
            fuel_type=self.ms,
            code="T-01",
            capacity_litres=10000,
            current_stock_litres=1000,
            reorder_level_litres=1000,
        )
        manager = User.objects.create_user(
            username="manager",
            password="test-password",
            role=User.Role.MANAGER,
            is_approved=True,
            requested_owner=self.owner,
            approved_by=self.owner,
        )
        employee = Employee.objects.create(
            owner=self.owner,
            user=manager,
            employee_code="MGR-02",
            hire_date=date(2026, 1, 1),
        )
        ManagerAssignment.objects.create(employee=employee, station=self.station)
        self.client.force_login(manager)

        response = self.client.get(
            reverse("fuel:station-inventory", args=[self.station.pk])
        )

        self.assertContains(response, "Low fuel stock")
        self.assertContains(response, "T-01")
        self.assertEqual(len(response.context["low_stock_tanks"]), 1)
        self.assertNotContains(response, "Record delivery")

    def test_tank_code_must_be_unique_within_station(self):
        Tank.objects.create(
            station=self.station,
            fuel_type=self.ms,
            code="T-01",
            capacity_litres=10000,
        )
        self.client.force_login(self.owner)

        response = self.post_tank(self.station, self.ms, code="t-01")

        self.assertEqual(response.status_code, 200)
        self.assertIn("code", response.context["form"].errors)
        self.assertEqual(Tank.objects.filter(station=self.station).count(), 1)

    def test_tank_stock_cannot_exceed_capacity(self):
        self.client.force_login(self.owner)

        response = self.post_tank(
            self.station,
            self.ms,
            current_stock_litres="10001.000",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Tank.objects.exists())

    def test_owner_cannot_use_another_owners_custom_fuel_type(self):
        private_type = FuelType.objects.create(
            name="Private Diesel",
            code="PD-1",
            owner=self.other_owner,
        )
        self.client.force_login(self.owner)

        response = self.post_tank(self.station, private_type)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Tank.objects.exists())

    def test_nozzle_must_use_a_tank_at_its_station(self):
        own_tank = Tank.objects.create(
            station=self.station,
            fuel_type=self.ms,
            code="T-01",
            capacity_litres=10000,
        )
        other_tank = Tank.objects.create(
            station=self.other_station,
            fuel_type=self.hsd,
            code="T-01",
            capacity_litres=10000,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("fuel:nozzle-create", args=[self.station.pk]),
            {"tank": other_tank.pk, "code": "N-01", "is_active": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Nozzle.objects.exists())

        response = self.client.post(
            reverse("fuel:nozzle-create", args=[self.station.pk]),
            {"tank": own_tank.pk, "code": "N-01", "is_active": "on"},
        )

        self.assertRedirects(
            response,
            reverse("fuel:station-fuel", args=[self.station.pk]),
        )
        self.assertEqual(Nozzle.objects.get().tank, own_tank)

    def test_nozzle_code_must_be_unique_within_station(self):
        tank = Tank.objects.create(
            station=self.station,
            fuel_type=self.ms,
            code="T-01",
            capacity_litres=10000,
        )
        Nozzle.objects.create(station=self.station, tank=tank, code="N-01")
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("fuel:nozzle-create", args=[self.station.pk]),
            {"tank": tank.pk, "code": "n-01", "is_active": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("code", response.context["form"].errors)
        self.assertEqual(Nozzle.objects.filter(station=self.station).count(), 1)

    def test_price_updates_append_and_history_is_displayed(self):
        self.client.force_login(self.owner)
        price_url = reverse("fuel:fuel-price-create", args=[self.station.pk])
        now = timezone.localtime().replace(second=0, microsecond=0)
        first_effective = now - timedelta(hours=2)
        second_effective = now - timedelta(hours=1)

        first = self.client.post(
            price_url,
            {
                "fuel_type": self.ms.pk,
                "price_per_litre": "101.25",
                "effective_from": first_effective.strftime("%Y-%m-%dT%H:%M"),
            },
        )
        second = self.client.post(
            price_url,
            {
                "fuel_type": self.ms.pk,
                "price_per_litre": "102.50",
                "effective_from": second_effective.strftime("%Y-%m-%dT%H:%M"),
            },
        )

        self.assertRedirects(
            first,
            reverse("fuel:station-fuel", args=[self.station.pk]),
        )
        self.assertRedirects(
            second,
            reverse("fuel:station-fuel", args=[self.station.pk]),
        )
        self.assertEqual(
            FuelPrice.objects.filter(station=self.station, fuel_type=self.ms).count(),
            2,
        )
        latest = FuelPrice.objects.get(price_per_litre="102.50")
        self.assertEqual(latest.recorded_by, self.owner)

        response = self.client.get(
            reverse("fuel:station-fuel", args=[self.station.pk])
        )

        self.assertContains(response, "Price history")
        self.assertContains(response, "101.25")
        self.assertContains(response, "102.50")
        self.assertEqual(response.context["current_prices"][0].pk, latest.pk)

    def test_same_fuel_price_cannot_be_recorded_twice_at_one_effective_time(self):
        self.client.force_login(self.owner)
        effective = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        price_url = reverse("fuel:fuel-price-create", args=[self.station.pk])
        price_data = {
            "fuel_type": self.ms.pk,
            "price_per_litre": "101.25",
            "effective_from": effective,
        }

        self.client.post(price_url, price_data)
        response = self.client.post(price_url, price_data)

        self.assertEqual(response.status_code, 200)
        self.assertIn("effective_from", response.context["form"].errors)
        self.assertEqual(FuelPrice.objects.count(), 1)

    def test_price_cannot_be_zero(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("fuel:fuel-price-create", args=[self.station.pk]),
            {
                "fuel_type": self.ms.pk,
                "price_per_litre": "0.00",
                "effective_from": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(FuelPrice.objects.exists())

    def test_price_recording_owner_must_own_the_station(self):
        price = FuelPrice(
            station=self.station,
            fuel_type=self.ms,
            price_per_litre="101.25",
            effective_from=timezone.now(),
            recorded_by=self.other_owner,
        )

        with self.assertRaises(ValidationError):
            price.full_clean()
