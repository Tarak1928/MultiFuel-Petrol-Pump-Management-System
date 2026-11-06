import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from accounts.models import User


class BootstrapOwnerCommandTests(TestCase):
    def setUp(self):
        self.values = {
            "INITIAL_OWNER_USERNAME": "first-owner",
            "INITIAL_OWNER_EMAIL": "owner@example.com",
            "INITIAL_OWNER_PASSWORD": "A-strong-Owner-password-9402!",
        }

    def test_creates_first_approved_owner_from_environment(self):
        with patch.dict(os.environ, self.values, clear=False):
            call_command("bootstrap_owner", stdout=StringIO())

        owner = User.objects.get(username="first-owner")
        self.assertTrue(owner.is_superuser)
        self.assertTrue(owner.is_staff)
        self.assertTrue(owner.is_approved)
        self.assertEqual(owner.role, User.Role.OWNER)
        self.assertTrue(owner.check_password(self.values["INITIAL_OWNER_PASSWORD"]))

    def test_existing_administrator_is_never_changed(self):
        existing = User.objects.create_superuser(
            username="existing-owner",
            email="existing@example.com",
            password="Existing-strong-password-123!",
        )
        with patch.dict(os.environ, self.values, clear=False):
            output = StringIO()
            call_command("bootstrap_owner", stdout=output)

        existing.refresh_from_db()
        self.assertEqual(User.objects.count(), 1)
        self.assertTrue(existing.check_password("Existing-strong-password-123!"))
        self.assertIn("already exists", output.getvalue())

    def test_missing_initial_owner_values_raise_clear_error(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in self.values:
                os.environ.pop(key, None)
            with self.assertRaisesMessage(CommandError, "INITIAL_OWNER_USERNAME"):
                call_command("bootstrap_owner", stdout=StringIO())

    def test_existing_non_admin_username_is_not_promoted(self):
        User.objects.create_user(
            username="first-owner",
            password="Existing-strong-password-123!",
            email="staff@example.com",
        )
        with patch.dict(os.environ, self.values, clear=False):
            with self.assertRaisesMessage(CommandError, "non-administrator"):
                call_command("bootstrap_owner", stdout=StringIO())

        self.assertFalse(User.objects.get(username="first-owner").is_superuser)
