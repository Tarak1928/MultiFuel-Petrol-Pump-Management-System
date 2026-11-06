from decimal import Decimal

from django.db import migrations


def backfill_submitted_report_reviews(apps, schema_editor):
    DutyReport = apps.get_model("duties", "DutyReport")
    DutyLubricantEntry = apps.get_model("duties", "DutyLubricantEntry")
    DutyReportReview = apps.get_model("duties", "DutyReportReview")
    database = schema_editor.connection.alias

    reports = (
        DutyReport.objects.using(database)
        .filter(status="submitted")
        .select_related("employee")
        .iterator()
    )
    for report in reports:
        stock_snapshot = {}
        entries = DutyLubricantEntry.objects.using(database).filter(
            report_id=report.pk
        ).values_list("lubricant_id", "quantity")
        for lubricant_id, quantity in entries:
            key = str(lubricant_id)
            stock_snapshot[key] = str(
                Decimal(stock_snapshot.get(key, "0.000")) + quantity
            )
        review = DutyReportReview.objects.using(database).create(
            report_id=report.pk,
            actor_id=report.employee.user_id,
            action="submitted",
            from_status="draft",
            to_status="submitted",
            stock_snapshot=stock_snapshot,
        )
        DutyReportReview.objects.using(database).filter(pk=review.pk).update(
            created_at=report.submitted_at
        )


class Migration(migrations.Migration):

    dependencies = [
        ("duties", "0006_alter_dutyreport_status_dutyreportreview"),
    ]

    operations = [
        migrations.RunPython(
            backfill_submitted_report_reviews,
            migrations.RunPython.noop,
        ),
    ]
