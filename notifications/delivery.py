import logging
import smtplib
from functools import partial

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction

from .models import Notification


logger = logging.getLogger(__name__)


def _send_notification_email(notification_id):
    notification = Notification.objects.select_related("recipient").get(pk=notification_id)
    try:
        send_mail(
            subject=f"MultiFuel: {notification.title}",
            message=(
                f"{notification.message}\n\nOpen MultiFuel: "
                f"{settings.SITE_URL.rstrip('/')}{notification.url}"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[notification.recipient.email],
            fail_silently=False,
        )
    except (OSError, smtplib.SMTPException):
        logger.exception("Email delivery failed for notification %s", notification_id)
        Notification.objects.filter(pk=notification_id).update(
            email_status=Notification.EmailStatus.FAILED
        )
    else:
        Notification.objects.filter(pk=notification_id).update(
            email_status=(
                Notification.EmailStatus.CONSOLE_ONLY
                if settings.EMAIL_BACKEND
                == "django.core.mail.backends.console.EmailBackend"
                else Notification.EmailStatus.SENT
            )
        )


def notify_user(recipient, title, message, url=""):
    if recipient is None or not recipient.is_active:
        return None
    email_status = (
        Notification.EmailStatus.PENDING
        if recipient.email
        else Notification.EmailStatus.NO_ADDRESS
    )
    notification = Notification.objects.create(
        recipient=recipient,
        title=title,
        message=message,
        url=url,
        email_status=email_status,
    )
    if recipient.email:
        transaction.on_commit(partial(_send_notification_email, notification.pk))
    return notification
