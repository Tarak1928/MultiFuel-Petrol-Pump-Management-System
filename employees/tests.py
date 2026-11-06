from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from expenses.models import Expense
from stations.models import Station

from .models import (
    Attendance,
    Employee,
    EmployeeAdvance,
    LeaveRequest,
    ManagerAssignment,
    Payroll,
    PayrollAdvanceRepayment,
)


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


class EmployeeWorkforceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="workforce-owner",
            email="workforce-owner@example.test",
            password="test-password",
        )
        self.other_owner = User.objects.create_superuser(
            username="other-workforce-owner",
            email="other-workforce-owner@example.test",
            password="test-password",
        )
        self.station = self.make_station(self.owner, "Workforce Central", "WF-001")
        self.other_station = self.make_station(
            self.owner,
            "Workforce North",
            "WF-002",
        )
        self.other_owner_station = self.make_station(
            self.other_owner,
            "Other Workforce",
            "WF-003",
        )
        self.manager = self.make_staff("workforce-manager", User.Role.MANAGER, self.owner)
        self.dsms = self.make_staff("workforce-dsms", User.Role.DSMS, self.owner)
        self.other_dsms = self.make_staff(
            "other-workforce-dsms",
            User.Role.DSMS,
            self.other_owner,
        )
        self.manager_employee = self.make_employee(
            self.manager,
            "WF-MGR",
            salary="40000.00",
        )
        ManagerAssignment.objects.create(
            employee=self.manager_employee,
            station=self.station,
        )
        self.employee = self.make_employee(
            self.dsms,
            "WF-DSMS",
            station=self.station,
            salary="30000.00",
        )
        self.other_employee = self.make_employee(
            self.other_dsms,
            "WF-OTHER",
            station=self.other_owner_station,
            salary="25000.00",
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

    def make_employee(self, user, code, station=None, salary="0.00"):
        return Employee.objects.create(
            owner=user.approved_by,
            user=user,
            employee_code=code,
            hire_date=date(2026, 1, 1),
            assigned_station=station,
            basic_salary=Decimal(salary),
        )

    def record_attendance(
        self,
        employee,
        station,
        work_date=date(2026, 1, 5),
        overtime="0.00",
        status=Attendance.Status.PRESENT,
    ):
        return Attendance.objects.create(
            employee=employee,
            station=station,
            work_date=work_date,
            status=status,
            overtime_hours=Decimal(overtime),
            recorded_by=self.owner,
        )

    def test_assigned_manager_records_station_attendance(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("employees:attendance-create"),
            {
                "station": self.station.pk,
                "employee": self.employee.pk,
                "work_date": "2026-01-05",
                "status": Attendance.Status.PRESENT,
                "clock_in": "06:00",
                "clock_out": "14:00",
                "overtime_hours": "1.50",
                "notes": "",
            },
        )

        self.assertRedirects(response, reverse("employees:attendance-list"))
        record = Attendance.objects.get()
        self.assertEqual(record.recorded_by, self.manager)
        self.assertEqual(record.overtime_hours, Decimal("1.50"))

    def test_manager_cannot_view_or_record_attendance_outside_assigned_stations(self):
        second_employee = self.make_employee(
            self.make_staff("second-station-dsms", User.Role.DSMS, self.owner),
            "WF-SECOND",
            station=self.other_station,
        )
        other_record = self.record_attendance(
            second_employee,
            self.other_station,
        )
        self.client.force_login(self.manager)

        self.assertEqual(
            self.client.get(
                reverse("employees:attendance-station-list", args=[self.other_station.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("employees:attendance-update", args=[other_record.pk])
            ).status_code,
            404,
        )
        response = self.client.post(
            reverse("employees:attendance-create"),
            {
                "station": self.other_station.pk,
                "employee": second_employee.pk,
                "work_date": "2026-01-06",
                "status": Attendance.Status.PRESENT,
                "overtime_hours": "0",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Attendance.objects.count(), 1)

    def test_dsms_can_view_own_attendance_but_cannot_manage_records(self):
        record = self.record_attendance(self.employee, self.station)
        self.client.force_login(self.dsms)

        response = self.client.get(reverse("employees:attendance-list"))
        self.assertContains(response, record.employee.employee_code)
        self.assertEqual(
            self.client.get(reverse("employees:attendance-create")).status_code,
            403,
        )

    def test_duplicate_attendance_is_rejected(self):
        self.client.force_login(self.owner)
        url = reverse("employees:attendance-create")
        payload = {
            "station": self.station.pk,
            "employee": self.employee.pk,
            "work_date": "2026-01-05",
            "status": Attendance.Status.PRESENT,
            "overtime_hours": "0",
        }
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Attendance.objects.count(), 1)

    def test_dsms_can_request_leave_and_assigned_manager_can_review(self):
        self.client.force_login(self.dsms)
        response = self.client.post(
            reverse("employees:leave-request-create"),
            {
                "leave_type": LeaveRequest.Type.SICK,
                "start_date": "2026-02-02",
                "end_date": "2026-02-03",
                "reason": "Medical appointment",
            },
        )
        self.assertRedirects(response, reverse("employees:leave-list"))
        leave = LeaveRequest.objects.get()

        self.client.force_login(self.manager)
        decision = self.client.post(
            reverse("employees:leave-request-decide", args=[leave.pk]),
            {"action": "approve", "review_note": "Approved"},
        )

        self.assertRedirects(decision, reverse("employees:leave-list"))
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(leave.reviewed_by, self.manager)
        self.assertEqual(leave.review_note, "Approved")

    def test_leave_requests_cannot_overlap_or_be_recorded_for_another_employee(self):
        self.client.force_login(self.dsms)
        request_url = reverse("employees:leave-request-create")
        payload = {
            "leave_type": LeaveRequest.Type.ANNUAL,
            "start_date": "2026-03-01",
            "end_date": "2026-03-03",
            "reason": "Family event",
        }
        self.assertEqual(self.client.post(request_url, payload).status_code, 302)
        response = self.client.post(request_url, payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeaveRequest.objects.count(), 1)
        with self.assertRaises(ValidationError):
            LeaveRequest.objects.create(
                employee=self.other_employee,
                leave_type=LeaveRequest.Type.ANNUAL,
                start_date=date(2026, 3, 1),
                end_date=date(2026, 3, 2),
                reason="Should not be allowed",
                requested_by=self.dsms,
            )

    def test_approved_leave_requires_leave_attendance(self):
        leave = LeaveRequest.objects.create(
            employee=self.employee,
            leave_type=LeaveRequest.Type.ANNUAL,
            start_date=date(2026, 4, 5),
            end_date=date(2026, 4, 5),
            reason="Personal",
            requested_by=self.dsms,
        )
        leave.status = LeaveRequest.Status.APPROVED
        leave.reviewed_by = self.owner
        leave.reviewed_at = timezone.now()
        leave.review_note = "Approved"
        leave.save()

        with self.assertRaises(ValidationError):
            self.record_attendance(
                self.employee,
                self.station,
                work_date=date(2026, 4, 5),
            )
        record = self.record_attendance(
            self.employee,
            self.station,
            work_date=date(2026, 4, 5),
            status=Attendance.Status.LEAVE,
        )
        self.assertEqual(record.status, Attendance.Status.LEAVE)

    def test_payroll_calculates_overtime_and_allocates_advance_repayments(self):
        self.record_attendance(
            self.employee,
            self.station,
            work_date=date(2026, 1, 10),
            overtime="5.00",
        )
        first_advance = EmployeeAdvance.objects.create(
            employee=self.employee,
            owner=self.owner,
            amount=Decimal("1000.00"),
            issued_date=date(2026, 1, 1),
            reference="ADV-001",
            issued_by=self.owner,
        )
        second_advance = EmployeeAdvance.objects.create(
            employee=self.employee,
            owner=self.owner,
            amount=Decimal("500.00"),
            issued_date=date(2026, 1, 2),
            reference="ADV-002",
            issued_by=self.owner,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("employees:payroll-create"),
            {
                "employee": self.employee.pk,
                "station": self.station.pk,
                "year": "2026",
                "month": "1",
                "allowances": "1500.00",
                "overtime_rate": "200.00",
                "other_deductions": "100.00",
                "advance_deduction": "1200.00",
            },
        )

        payroll = Payroll.objects.get(employee=self.employee)
        self.assertRedirects(
            response,
            reverse("employees:payslip", args=[payroll.pk]),
        )
        self.assertEqual(payroll.overtime_hours, Decimal("5.00"))
        self.assertEqual(payroll.overtime_amount, Decimal("1000.00"))
        self.assertEqual(payroll.gross_pay, Decimal("32500.00"))
        self.assertEqual(payroll.net_pay, Decimal("31200.00"))
        self.assertEqual(
            list(
                payroll.advance_repayments.order_by("advance__issued_date").values_list(
                    "advance_id",
                    "amount",
                )
            ),
            [
                (first_advance.pk, Decimal("1000.00")),
                (second_advance.pk, Decimal("200.00")),
            ],
        )
        self.assertEqual(first_advance.outstanding_amount, Decimal("0.00"))
        self.assertEqual(second_advance.outstanding_amount, Decimal("300.00"))

    def test_payroll_is_unique_and_locks_attendance_for_the_period(self):
        record = self.record_attendance(
            self.employee,
            self.station,
            work_date=date(2026, 1, 10),
            overtime="2.00",
        )
        self.client.force_login(self.owner)
        payload = {
            "employee": self.employee.pk,
            "station": self.station.pk,
            "year": "2026",
            "month": "1",
            "allowances": "0",
            "overtime_rate": "100",
            "other_deductions": "0",
            "advance_deduction": "0",
        }
        self.client.post(reverse("employees:payroll-create"), payload)
        record.notes = "Changed after payroll"
        with self.assertRaises(ValidationError):
            record.save()

        duplicate = self.client.post(reverse("employees:payroll-create"), payload)

        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(Payroll.objects.filter(employee=self.employee).count(), 1)

    def test_payroll_rejects_deductions_above_gross_or_advance_balance(self):
        EmployeeAdvance.objects.create(
            employee=self.employee,
            owner=self.owner,
            amount=Decimal("100.00"),
            issued_date=date(2026, 1, 1),
            reference="ADV-LIMIT",
            issued_by=self.owner,
        )
        self.client.force_login(self.owner)
        url = reverse("employees:payroll-create")
        payload = {
            "employee": self.employee.pk,
            "station": self.station.pk,
            "year": "2026",
            "month": "1",
            "allowances": "0",
            "overtime_rate": "0",
            "other_deductions": "0",
            "advance_deduction": "101",
        }
        response = self.client.post(url, payload)
        self.assertContains(response, "Advance deduction cannot exceed")
        self.assertFalse(Payroll.objects.exists())

        payload["advance_deduction"] = "0"
        payload["other_deductions"] = "30001"
        response = self.client.post(url, payload)
        self.assertContains(response, "Deductions cannot exceed gross pay.")
        self.assertFalse(Payroll.objects.exists())

    def test_owner_can_issue_an_employee_advance(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("employees:advance-create"),
            {
                "employee": self.employee.pk,
                "amount": "750.00",
                "issued_date": "2026-01-15",
                "reference": "adv-web-001",
                "notes": "Transport advance",
            },
        )

        self.assertRedirects(response, reverse("employees:advance-list"))
        advance = EmployeeAdvance.objects.get()
        self.assertEqual(advance.reference, "ADV-WEB-001")
        self.assertEqual(advance.owner, self.owner)
        self.assertContains(
            self.client.get(reverse("employees:advance-list")),
            "ADV-WEB-001",
        )

    def test_payslip_is_private_and_owner_can_mark_payroll_paid(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("employees:payroll-create"),
            {
                "employee": self.employee.pk,
                "station": self.station.pk,
                "year": "2026",
                "month": "2",
                "allowances": "0",
                "overtime_rate": "0",
                "other_deductions": "0",
                "advance_deduction": "0",
            },
        )
        payroll = Payroll.objects.get(employee=self.employee)
        self.assertRedirects(response, reverse("employees:payslip", args=[payroll.pk]))
        self.assertContains(
            self.client.get(reverse("employees:payroll-list")),
            "2026-02",
        )

        self.client.force_login(self.other_dsms)
        self.assertEqual(
            self.client.get(reverse("employees:payslip", args=[payroll.pk])).status_code,
            404,
        )
        self.client.force_login(self.dsms)
        self.assertEqual(
            self.client.get(reverse("employees:payslip", args=[payroll.pk])).status_code,
            200,
        )
        self.client.force_login(self.owner)
        paid = self.client.post(
            reverse("employees:payroll-mark-paid", args=[payroll.pk])
        )
        self.assertRedirects(paid, reverse("employees:payslip", args=[payroll.pk]))
        payroll.refresh_from_db()
        self.assertEqual(payroll.status, Payroll.Status.PAID)
        self.assertIsNotNone(payroll.paid_at)
        salary_expense = Expense.objects.get(payroll=payroll)
        self.assertEqual(salary_expense.station, self.station)
        self.assertEqual(salary_expense.amount, payroll.gross_pay)
        self.assertEqual(salary_expense.category, Expense.Category.SALARY)

    def test_manager_cannot_generate_or_view_another_employees_payroll(self):
        self.client.force_login(self.manager)
        self.assertEqual(
            self.client.get(reverse("employees:payroll-create")).status_code,
            403,
        )
        other_payroll = Payroll.objects.create(
            employee=self.other_employee,
            station=self.other_owner_station,
            year=2026,
            month=1,
            basic_salary=self.other_employee.basic_salary,
            created_by=self.other_owner,
        )
        self.assertEqual(
            self.client.get(
                reverse("employees:payslip", args=[other_payroll.pk])
            ).status_code,
            404,
        )
