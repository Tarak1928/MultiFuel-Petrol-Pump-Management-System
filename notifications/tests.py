from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from .models import Notification


class NotificationInboxTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            password="Secure-owner-password-1!",
            email="owner@example.com",
            role=User.Role.OWNER,
            is_approved=True,
        )
        self.other = User.objects.create_user(
            username="other",
            password="Secure-other-password-1!",
            role=User.Role.OWNER,
            is_approved=True,
        )

    def test_registration_request_notifies_selected_owner(self):
        applicant = User.objects.create_user(
            username="manager-request",
            password="Secure-manager-password-1!",
            email="manager@example.com",
            role=User.Role.MANAGER,
            requested_owner=self.owner,
        )

        notification = Notification.objects.get(recipient=self.owner)

        self.assertEqual(notification.title, "Staff access requested")
        self.assertEqual(notification.email_status, Notification.EmailStatus.PENDING)
        self.assertIn(applicant.username, notification.message)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_registration_notification_sends_email_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            User.objects.create_user(
                username="manager-request",
                password="Secure-manager-password-1!",
                email="manager@example.com",
                role=User.Role.MANAGER,
                requested_owner=self.owner,
            )

        notification = Notification.objects.get(recipient=self.owner)
        self.assertEqual(notification.email_status, Notification.EmailStatus.SENT)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_email_is_not_marked_as_delivered(self):
        with self.captureOnCommitCallbacks(execute=True):
            User.objects.create_user(
                username="console-request",
                password="Secure-manager-password-1!",
                email="manager@example.com",
                role=User.Role.MANAGER,
                requested_owner=self.owner,
            )

        notification = Notification.objects.get(recipient=self.owner)
        self.assertEqual(
            notification.email_status,
            Notification.EmailStatus.CONSOLE_ONLY,
        )

    def test_inbox_and_read_action_are_scoped_to_logged_in_user(self):
        notification = Notification.objects.create(
            recipient=self.owner,
            title="Test alert",
            message="A test alert",
            url="/dashboard/",
        )
        self.client.force_login(self.owner)

        inbox_response = self.client.get(reverse("notifications:inbox"))
        self.assertEqual(inbox_response.status_code, 200)
        self.assertContains(inbox_response, "Test alert")
        self.assertEqual(inbox_response.context["unread_notification_count"], 1)

        read_response = self.client.post(
            reverse("notifications:mark-read", args=(notification.pk,))
        )
        self.assertEqual(read_response.status_code, 302)
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_user_cannot_mark_another_users_notification_read(self):
        notification = Notification.objects.create(
            recipient=self.other,
            title="Private alert",
            message="Only for the other user",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("notifications:mark-read", args=(notification.pk,))
        )

        self.assertEqual(response.status_code, 404)
        notification.refresh_from_db()
        self.assertIsNone(notification.read_at)
