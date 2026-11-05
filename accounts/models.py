"""Account models for MultiFuel."""

from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.db import models


class MultiFuelUserManager(DjangoUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.OWNER)
        extra_fields.setdefault("is_approved", True)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = "OWNER", "Owner"
        MANAGER = "MANAGER", "Manager"
        DSMS = "DSMS", "DSMS staff"

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.DSMS,
        db_index=True,
    )
    is_approved = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether an Owner has approved this account for application access.",
    )
    requested_owner = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="registration_requests",
        blank=True,
        null=True,
    )
    approved_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="approved_accounts",
        blank=True,
        null=True,
    )

    objects = MultiFuelUserManager()

    class Meta:
        ordering = ["username"]

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"
