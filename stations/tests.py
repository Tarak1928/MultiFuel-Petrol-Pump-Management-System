from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import Station


class StationManagementTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            password="A-safe-test-password-412!",
            role=User.Role.OWNER,
            is_approved=True,
        )
        self.other_owner = User.objects.create_user(
            username="other-owner",
            password="A-safe-test-password-412!",
            role=User.Role.OWNER,
            is_approved=True,
        )
        self.station = Station.objects.create(
            name="Central Fuel",
            code="MF-001",
            address="10 Main Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            owner=self.owner,
        )

    def test_owner_can_create_station_and_code_is_normalized(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("stations:station-create"),
            {
                "name": "North Fuel",
                "code": "mf-002",
                "address": "20 North Road",
                "city": "Pune",
                "state": "Maharashtra",
                "postal_code": "411002",
                "contact_number": "02012345678",
            },
        )

        self.assertRedirects(response, reverse("stations:station-list"))
        station = Station.objects.get(code="MF-002")
        self.assertEqual(station.owner, self.owner)

    def test_duplicate_station_code_is_rejected(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("stations:station-create"),
            {
                "name": "Duplicate Fuel",
                "code": "mf-001",
                "address": "20 North Road",
                "city": "Pune",
                "state": "Maharashtra",
                "postal_code": "411002",
                "contact_number": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists")
        self.assertEqual(Station.objects.count(), 1)

    def test_owner_cannot_view_another_owners_station(self):
        self.client.force_login(self.other_owner)

        response = self.client.get(
            reverse("stations:station-update", args=[self.station.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_owner_can_edit_station_without_changing_ownership(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("stations:station-update", args=[self.station.pk]),
            {
                "name": "Central Fuel Updated",
                "code": "mf-001",
                "address": "11 Main Road",
                "city": "Pune",
                "state": "Maharashtra",
                "postal_code": "411001",
                "contact_number": "",
                "owner": self.other_owner.pk,
            },
        )

        self.assertRedirects(response, reverse("stations:station-list"))
        self.station.refresh_from_db()
        self.assertEqual(self.station.name, "Central Fuel Updated")
        self.assertEqual(self.station.owner, self.owner)

    def test_manager_cannot_manage_stations(self):
        manager = User.objects.create_user(
            username="manager",
            password="A-safe-test-password-412!",
            role=User.Role.MANAGER,
            is_approved=True,
        )
        self.client.force_login(manager)

        response = self.client.get(reverse("stations:station-list"))

        self.assertEqual(response.status_code, 403)

    def test_unapproved_owner_cannot_manage_stations(self):
        pending_owner = User.objects.create_user(
            username="pending-owner",
            password="A-safe-test-password-412!",
            role=User.Role.OWNER,
            is_approved=False,
        )
        self.client.force_login(pending_owner)

        response = self.client.get(reverse("stations:station-list"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('stations:station-list')}",
        )

    def test_inactive_owner_session_cannot_manage_stations(self):
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.client.force_login(self.owner)

        response = self.client.get(reverse("stations:station-list"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('stations:station-list')}",
        )

    def test_station_cannot_be_suspended_with_get_request(self):
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("stations:station-toggle-status", args=[self.station.pk])
        )

        self.assertEqual(response.status_code, 405)
        self.station.refresh_from_db()
        self.assertTrue(self.station.is_active)

    def test_owner_can_suspend_and_reactivate_station(self):
        self.client.force_login(self.owner)
        status_url = reverse("stations:station-toggle-status", args=[self.station.pk])

        response = self.client.post(status_url)
        self.assertRedirects(response, reverse("stations:station-list"))
        self.station.refresh_from_db()
        self.assertFalse(self.station.is_active)

        self.client.post(status_url)
        self.station.refresh_from_db()
        self.assertTrue(self.station.is_active)

    def test_station_list_contains_only_current_owners_stations(self):
        Station.objects.create(
            name="Other Fuel",
            code="MF-OTHER",
            address="1 Other Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411003",
            owner=self.other_owner,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("stations:station-list"))

        self.assertContains(response, "Central Fuel")
        self.assertNotContains(response, "Other Fuel")

    def test_owner_dashboard_displays_real_station_counts(self):
        Station.objects.create(
            name="Suspended Fuel",
            code="MF-SUSP",
            address="1 Old Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411004",
            owner=self.owner,
            is_active=False,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("dashboard-owner"))

        self.assertEqual(response.context["station_count"], 2)
        self.assertEqual(response.context["active_station_count"], 1)
        self.assertContains(response, "Manage stations")
