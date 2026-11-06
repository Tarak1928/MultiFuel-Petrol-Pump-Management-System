from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    class EmailStatus(models.TextChoices):
        NO_ADDRESS = "no_address", "No email address"
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        CONSOLE_ONLY = "console_only", "Console only"
        FAILED = "failed", "Failed"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=500)
    url = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(blank=True, null=True)
    email_status = models.CharField(
        max_length=12,
        choices=EmailStatus.choices,
        default=EmailStatus.NO_ADDRESS,
    )

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=("recipient", "-created_at"),
                name="notif_recipient_time_idx",
            ),
            models.Index(
                fields=("recipient", "read_at"),
                name="notif_recipient_read_idx",
            ),
        ]

    @property
    def is_read(self):
        return self.read_at is not None

    def mark_read(self):
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=("read_at",))

    def __str__(self):
        return f"{self.title} — {self.recipient}"
