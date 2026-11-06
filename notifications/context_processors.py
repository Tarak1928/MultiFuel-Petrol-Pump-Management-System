from .models import Notification


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {"unread_notification_count": 0}
    return {
        "unread_notification_count": Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True,
        ).count()
    }
