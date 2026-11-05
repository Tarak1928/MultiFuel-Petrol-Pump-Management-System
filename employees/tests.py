from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from stations.models import Station

from .models import Employee, ManagerAssignment


class EmployeeAssignmentTests(TestCase):
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
        self.station = Station.objects.create(
            name="Central Fuel",
            code="MF-001",
            address="10 Main Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411001",
            owner=self.owner,
        )
        self.second_station = Station.objects.create(
            name="North Fuel",
            code="MF-002",
            address="20 North Road",
            city="Pune",
            state="Maharashtra",
            postal_code="411002",
            owner=self.owner,
        )
        self.other_station = Station.objects.create(
            name="Other Fuel",
            code="OT-001",
            address="1 Other Road",
            city="Mumbai",
            state="Maharashtra",
            postal_code="400001",
            owner=self.other_owner,
        )
        self.manager = self.make_staff("manager", User.Role.MANAGER, self.owner)
        self.dsms = self.make_staff("dsms", User.Role.DSMS, self.owner)
        self.other_manager = self.make_staff(
            "other-manager",
            User.Role.MANAGER,
            self.other_owner,
        )

    @staticmethod
    def make_staff(username, role, owner):
        return User.objects.create_user(
            username=username,
            password="A-safe-test-password-412!",
            role=role,
            is_approved=True,
            requested_owner=owner,
            approved_by=owner,
        )

    def post_employee(self, user, code, **extra):
        data = {
            "user": user.pk,
            "employee_code": code,
            "phone": "02012345678",
            "hire_date": "2026-11-05",
            "assigned_station": "",
            "is_active": "on",
        }
        data.update(extra)
        return self.client.post(reverse("employees:employee-create"), data)

    def test_owner_assigns_manager_to_multiple_owned_stations(self):
        self.client.force_login(self.owner)

        response = self.post_employee(
            self.manager,
            "emp-001",
            manager_stations=[self.station.pk, self.second_station.pk],
        )

        self.assertRedirects(response, reverse("employees:employee-list"))
        employee = Employee.objects.get(user=self.manager)
        self.assertEqual(employee.employee_code, "EMP-001")
        self.assertEqual(employee.owner, self.owner)
        self.assertSetEqual(
            set(employee.manager_assignments.values_list("station_id", flat=True)),
            {self.station.pk, self.second_station.pk},
        )
        self.assertIsNone(employee.assigned_station)

    def test_manager_requires_at_least_one_station(self):
        self.client.force_login(self.owner)

        response = self.post_employee(self.manager, "EMP-001")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assign each Manager to at least one station.")
        self.assertFalse(Employee.objects.filter(user=self.manager).exists())

    def test_owner_assigns_dsms_to_exactly_one_station(self):
        self.client.force_login(self.owner)

        response = self.post_employee(
            self.dsms,
            "EMP-002",
            assigned_station=self.station.pk,
        )

        self.assertRedirects(response, reverse("employees:employee-list"))
        employee = Employee.objects.get(user=self.dsms)
        self.assertEqual(employee.assigned_station, self.station)
        self.assertFalse(employee.manager_assignments.exists())

    def test_dsms_cannot_be_assigned_to_multiple_stations(self):
        self.client.force_login(self.owner)

        response = self.post_employee(
            self.dsms,
            "EMP-002",
            assigned_station=self.station.pk,
            manager_stations=[self.second_station.pk],
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DSMS staff use the single-station assignment field.")
        self.assertFalse(Employee.objects.filter(user=self.dsms).exists())

    def test_employee_form_cannot_assign_another_owners_account_or_station(self):
        self.client.force_login(self.owner)

        response = self.post_employee(
            self.other_manager,
            "EMP-003",
            manager_stations=[self.other_station.pk],
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(user=self.other_manager).exists())

    def test_owner_cannot_assign_manager_to_another_owners_station(self):
        self.client.force_login(self.owner)

        response = self.post_employee(
            self.manager,
            "EMP-003",
            manager_stations=[self.other_station.pk],
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Employee.objects.filter(user=self.manager).exists())

    def test_owner_cannot_edit_another_owners_employee_profile(self):
        employee = Employee.objects.create(
            owner=self.other_owner,
            user=self.other_manager,
            employee_code="EMP-004",
            hire_date="2026-01-01",
        )
        ManagerAssignment.objects.create(
            employee=employee,
            station=self.other_station,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("employees:employee-update", args=[employee.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_manager_dashboard_shows_only_assigned_active_stations(self):
        employee = Employee.objects.create(
            owner=self.owner,
            user=self.manager,
            employee_code="EMP-005",
            hire_date="2026-01-01",
        )
        ManagerAssignment.objects.create(employee=employee, station=self.station)
        self.client.force_login(self.manager)

        response = self.client.get(reverse("dashboard-manager"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central Fuel")
        self.assertNotContains(response, "North Fuel")

    def test_dsms_dashboard_shows_assigned_station(self):
        Employee.objects.create(
            owner=self.owner,
            user=self.dsms,
            employee_code="EMP-006",
            hire_date="2026-01-01",
            assigned_station=self.station,
        )
        self.client.force_login(self.dsms)

        response = self.client.get(reverse("dashboard-dsms"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Central Fuel")

    def test_inactive_employee_cannot_access_dashboard(self):
        employee = Employee.objects.create(
            owner=self.owner,
            user=self.manager,
            employee_code="EMP-007",
            hire_date="2026-01-01",
            is_active=False,
        )
        ManagerAssignment.objects.create(
            employee=employee,
            station=self.station,
        )
        self.client.force_login(self.manager)

        response = self.client.get(reverse("dashboard-manager"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('dashboard-manager')}",
        )

    def test_owner_can_deactivate_employee_with_post_only(self):
        employee = Employee.objects.create(
            owner=self.owner,
            user=self.dsms,
            employee_code="EMP-008",
            hire_date="2026-01-01",
            assigned_station=self.station,
        )
        self.client.force_login(self.owner)
        status_url = reverse(
            "employees:employee-toggle-status",
            args=[employee.pk],
        )

        response = self.client.get(status_url)

        self.assertEqual(response.status_code, 405)
        employee.refresh_from_db()
        self.assertTrue(employee.is_active)

        response = self.client.post(status_url)

        self.assertRedirects(response, reverse("employees:employee-list"))
        employee.refresh_from_db()
        self.assertFalse(employee.is_active)

    def test_employee_list_is_limited_to_current_owner(self):
        Employee.objects.create(
            owner=self.other_owner,
            user=self.other_manager,
            employee_code="EMP-009",
            hire_date="2026-01-01",
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("employees:employee-list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "other-manager")
