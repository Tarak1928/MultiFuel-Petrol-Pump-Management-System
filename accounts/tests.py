from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import User


class UserModelTests(TestCase):
    def test_new_user_defaults_to_dsms_role(self):
        user = User.objects.create_user(username="staff", password="safe-test-password")

        self.assertEqual(user.role, User.Role.DSMS)
        self.assertEqual(user.get_role_display(), "DSMS staff")

    def test_role_can_be_assigned_to_owner(self):
        user = User.objects.create_user(
            username="operator",
            password="safe-test-password",
            role=User.Role.OWNER,
        )

        self.assertEqual(user.role, User.Role.OWNER)

    def test_superuser_defaults_to_approved_owner(self):
        user = User.objects.create_superuser(
            username="owner",
            email="owner@example.test",
            password="safe-test-password",
        )

        self.assertEqual(user.role, User.Role.OWNER)
        self.assertTrue(user.is_approved)


class RegistrationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="pump-owner",
            email="owner@example.test",
            password="A-safe-test-password-412!",
        )

    def test_registration_creates_pending_non_owner_account(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "newstaff",
                "first_name": "New",
                "last_name": "Staff",
                "email": "staff@example.test",
                "role": User.Role.DSMS,
                "requested_owner": self.owner.pk,
                "password1": "A-safe-test-password-412!",
                "password2": "A-safe-test-password-412!",
            },
        )

        user = User.objects.get(username="newstaff")
        self.assertRedirects(response, reverse("login"))
        self.assertFalse(user.is_approved)
        self.assertFalse(user.is_staff)
        self.assertEqual(user.requested_owner, self.owner)

    def test_registration_cannot_create_an_owner(self):
        response = self.client.post(
            reverse("register"),
            {
                "username": "selfowner",
                "first_name": "Self",
                "last_name": "Owner",
                "email": "owner@example.test",
                "role": User.Role.OWNER,
                "requested_owner": self.owner.pk,
                "password1": "A-safe-test-password-412!",
                "password2": "A-safe-test-password-412!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="selfowner").exists())


class AuthenticationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="staff",
            password="A-safe-test-password-412!",
            role=User.Role.DSMS,
        )

    def test_pending_user_cannot_log_in(self):
        response = self.client.post(
            reverse("login"),
            {"username": "staff", "password": "A-safe-test-password-412!"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_manager_can_access_only_the_manager_dashboard(self):
        manager = User.objects.create_user(
            username="manager",
            password="A-safe-test-password-412!",
            role=User.Role.MANAGER,
            is_approved=True,
        )
        self.client.force_login(manager)

        response = self.client.get(reverse("dashboard-manager"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Station access is not assigned yet")

    def test_approved_user_lands_on_role_dashboard(self):
        self.user.is_approved = True
        self.user.save(update_fields=["is_approved"])

        response = self.client.post(
            reverse("login"),
            {"username": "staff", "password": "A-safe-test-password-412!"},
        )

        self.assertRedirects(response, reverse("dashboard-dsms"))

    def test_logout_requires_post(self):
        self.user.is_approved = True
        self.user.save(update_fields=["is_approved"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("logout"))

        self.assertEqual(response.status_code, 405)

    def test_authenticated_user_cannot_access_another_role_dashboard(self):
        self.user.is_approved = True
        self.user.save(update_fields=["is_approved"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard-owner"))

        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_cannot_access_dashboard(self):
        response = self.client.get(reverse("dashboard-dsms"))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('dashboard-dsms')}",
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_page_sends_reset_email_for_registered_user(self):
        self.user.is_approved = True
        self.user.email = "staff@example.test"
        self.user.save(update_fields=["is_approved", "email"])

        response = self.client.post(
            reverse("password-reset"),
            {"email": "staff@example.test"},
        )

        self.assertRedirects(response, reverse("password-reset-done"))
        self.assertEqual(len(mail.outbox), 1)


class ApprovalTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser(
            username="owner",
            email="owner@example.test",
            password="A-safe-test-password-412!",
        )
        self.pending_user = User.objects.create_user(
            username="pending",
            password="A-safe-test-password-412!",
            role=User.Role.MANAGER,
            requested_owner=self.owner,
        )

    def test_owner_can_approve_pending_user(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("approve-account", args=[self.pending_user.pk])
        )

        self.assertRedirects(response, reverse("dashboard-owner"))
        self.pending_user.refresh_from_db()
        self.assertTrue(self.pending_user.is_approved)
        self.assertEqual(self.pending_user.approved_by, self.owner)

    def test_owner_cannot_approve_another_owners_registration(self):
        other_owner = User.objects.create_superuser(
            username="other-owner",
            email="other@example.test",
            password="A-safe-test-password-412!",
        )
        self.pending_user.requested_owner = other_owner
        self.pending_user.save(update_fields=["requested_owner"])
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("approve-account", args=[self.pending_user.pk])
        )

        self.assertEqual(response.status_code, 404)
        self.pending_user.refresh_from_db()
        self.assertFalse(self.pending_user.is_approved)

    def test_non_owner_cannot_approve_pending_user(self):
        manager = User.objects.create_user(
            username="manager",
            password="A-safe-test-password-412!",
            role=User.Role.MANAGER,
            is_approved=True,
        )
        self.client.force_login(manager)

        response = self.client.post(
            reverse("approve-account", args=[self.pending_user.pk])
        )

        self.assertEqual(response.status_code, 403)
        self.pending_user.refresh_from_db()
        self.assertFalse(self.pending_user.is_approved)
