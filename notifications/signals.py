from django.db.models.signals import post_save, pre_save

from accounts.models import User
from duties.models import DutyReport
from employees.models import LeaveRequest, Payroll
from expenses.models import Expense
from fuel.models import FuelInventoryAdjustment

from .delivery import notify_user


def _station_managers(station):
    return User.objects.filter(
        employee_profile__manager_assignments__station=station,
        employee_profile__is_active=True,
        role=User.Role.MANAGER,
        is_active=True,
    ).distinct()


def _employee_stations(employee):
    if employee.user.role == User.Role.DSMS:
        return [employee.assigned_station] if employee.assigned_station_id else []
    return [
        assignment.station
        for assignment in employee.manager_assignments.select_related("station")
    ]


def remember_user_approval(sender, instance, **kwargs):
    if instance.pk:
        instance._old_approved = (
            sender.objects.filter(pk=instance.pk)
            .values_list("is_approved", flat=True)
            .first()
        )


def notify_registration_and_approval(sender, instance, created, **kwargs):
    if created and instance.requested_owner_id and not instance.is_approved:
        notify_user(
            instance.requested_owner,
            "Staff access requested",
            f"{instance.get_full_name() or instance.username} requested "
            f"{instance.get_role_display()} access.",
            "/dashboard/owner/",
        )
    elif (
        not created
        and not getattr(instance, "_old_approved", instance.is_approved)
        and instance.is_approved
    ):
        notify_user(
            instance,
            "Account approved",
            "Your MultiFuel account has been approved. You can now sign in.",
            "/dashboard/",
        )


def _remember_status(sender, instance, **kwargs):
    instance._old_notification_status = (
        sender.objects.filter(pk=instance.pk)
        .values_list("status", flat=True)
        .first()
        if instance.pk
        else None
    )


def remember_workflow_status(sender, instance, **kwargs):
    _remember_status(sender, instance)


def notify_workflow_status(sender, instance, created, **kwargs):
    previous = getattr(instance, "_old_notification_status", None)
    if isinstance(instance, DutyReport):
        if instance.status == previous:
            return
        if instance.status in (DutyReport.Status.SUBMITTED, DutyReport.Status.RESUBMITTED):
            recipients = [instance.station.owner, *_station_managers(instance.station)]
            for recipient in recipients:
                notify_user(
                    recipient,
                    "Duty report submitted",
                    f"{instance.station.name}: {instance.report_date} duty report "
                    "is ready for review.",
                    f"/duties/{instance.pk}/",
                )
        elif instance.status in (
            DutyReport.Status.PENDING_VERIFICATION,
            DutyReport.Status.CORRECTION_REQUESTED,
            DutyReport.Status.APPROVED,
        ):
            notify_user(
                instance.employee.user,
                f"Duty report {instance.get_status_display().lower()}",
                f"Your {instance.station.name} duty report for "
                f"{instance.report_date} is {instance.get_status_display().lower()}.",
                f"/duties/{instance.pk}/",
            )
    elif isinstance(instance, Expense):
        if created and instance.status == Expense.Status.PENDING:
            notify_user(
                instance.station.owner,
                "Expense needs approval",
                f"An expense of ₹{instance.amount} at {instance.station.name} "
                "is waiting for your review.",
                "/expenses/",
            )
        elif not created and instance.status != previous:
            notify_user(
                instance.recorded_by,
                f"Expense {instance.get_status_display().lower()}",
                f"Your expense at {instance.station.name} was "
                f"{instance.get_status_display().lower()}.",
                "/expenses/",
            )
    elif isinstance(instance, FuelInventoryAdjustment):
        if created and instance.status == FuelInventoryAdjustment.Status.PENDING:
            notify_user(
                instance.station.owner,
                "Inventory adjustment needs approval",
                f"A stock adjustment for tank {instance.tank.code} at "
                f"{instance.station.name} is waiting for your review.",
                f"/fuel/stations/{instance.station_id}/inventory/",
            )
        elif not created and instance.status != previous:
            notify_user(
                instance.requested_by,
                f"Inventory adjustment {instance.get_status_display().lower()}",
                f"Your stock adjustment for tank {instance.tank.code} was "
                f"{instance.get_status_display().lower()}.",
                f"/fuel/stations/{instance.station_id}/inventory/",
            )
    elif isinstance(instance, LeaveRequest):
        if created and instance.status == LeaveRequest.Status.PENDING:
            recipients = {instance.employee.owner}
            for station in _employee_stations(instance.employee):
                recipients.update(_station_managers(station))
            for recipient in recipients:
                if recipient.pk != instance.requested_by_id:
                    notify_user(
                        recipient,
                        "Leave request needs review",
                        f"{instance.employee.user.get_full_name() or instance.employee.user.username} "
                        f"requested leave from {instance.start_date} to {instance.end_date}.",
                        "/employees/leave/",
                    )
        elif not created and instance.status != previous:
            notify_user(
                instance.requested_by,
                f"Leave request {instance.get_status_display().lower()}",
                f"Your leave request from {instance.start_date} to "
                f"{instance.end_date} was {instance.get_status_display().lower()}.",
                "/employees/leave/",
            )
    elif isinstance(instance, Payroll):
        if (
            not created
            and previous == Payroll.Status.UNPAID
            and instance.status == Payroll.Status.PAID
        ):
            notify_user(
                instance.employee.user,
                "Payroll marked as paid",
                f"Your payroll for {instance.year}-{instance.month:02d} "
                f"has been marked paid (₹{instance.net_pay}).",
                "/employees/payroll/",
            )


pre_save.connect(
    remember_user_approval,
    sender=User,
    dispatch_uid="notifications.remember_user_approval",
)
post_save.connect(
    notify_registration_and_approval,
    sender=User,
    dispatch_uid="notifications.notify_registration_and_approval",
)

for model in (DutyReport, Expense, FuelInventoryAdjustment, LeaveRequest, Payroll):
    pre_save.connect(
        remember_workflow_status,
        sender=model,
        dispatch_uid=f"notifications.remember_status.{model._meta.label_lower}",
    )
    post_save.connect(
        notify_workflow_status,
        sender=model,
        dispatch_uid=f"notifications.notify_status.{model._meta.label_lower}",
    )
