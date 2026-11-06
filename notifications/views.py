from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Notification


@login_required
def inbox(request):
    notifications = Notification.objects.filter(recipient=request.user)[:100]
    return render(
        request,
        "notifications/inbox.html",
        {"notifications": notifications},
    )


@login_required
@require_POST
def mark_read(request, notification_id):
    notification = get_object_or_404(
        Notification.objects.filter(recipient=request.user),
        pk=notification_id,
    )
    notification.mark_read()
    if notification.url and url_has_allowed_host_and_scheme(
        notification.url,
        allowed_hosts=None,
    ):
        return redirect(notification.url)
    return redirect("notifications:inbox")


@login_required
@require_POST
def mark_all_read(request):
    Notification.objects.filter(
        recipient=request.user,
        read_at__isnull=True,
    ).update(read_at=timezone.now())
    return redirect("notifications:inbox")
