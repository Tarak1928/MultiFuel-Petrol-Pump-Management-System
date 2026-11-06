import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User


class Command(BaseCommand):
    help = "Create the first Owner from environment settings if no superuser exists."

    def handle(self, *args, **options):
        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("An administrator already exists; no account was changed.")
            return

        username = os.environ.get("INITIAL_OWNER_USERNAME", "").strip()
        email = os.environ.get("INITIAL_OWNER_EMAIL", "").strip()
        password = os.environ.get("INITIAL_OWNER_PASSWORD", "")
        missing = [
            key
            for key, value in (
                ("INITIAL_OWNER_USERNAME", username),
                ("INITIAL_OWNER_EMAIL", email),
                ("INITIAL_OWNER_PASSWORD", password),
            )
            if not value
        ]
        if missing:
            raise CommandError(
                "Set the following environment values to create the initial Owner: "
                + ", ".join(missing)
            )
        if User.objects.filter(username=username).exists():
            raise CommandError(
                "The initial Owner username is already used by a non-administrator account."
            )

        user = User(username=username, email=email, role=User.Role.OWNER)
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError(
                "The initial Owner password does not meet the configured password policy: "
                + "; ".join(exc.messages)
            ) from exc

        with transaction.atomic():
            User.objects.create_superuser(
                username=username,
                email=email,
                password=password,
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created the initial approved Owner account {username!r}."
            )
        )
