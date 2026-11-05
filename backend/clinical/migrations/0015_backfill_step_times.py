"""Give the pre-assessments that already exist the little the record can
honestly say: the Consent step began when the row was created, and a completed
one finished its Review step when it was completed (its start is unknown, so
it stays empty and no duration is shown). Nothing else is invented.
Noop reverse (the rows go with 0014)."""
from django.db import migrations


def backfill_step_times(apps, schema_editor):
    PreAssessment = apps.get_model("clinical", "PreAssessment")
    StepTime = apps.get_model("clinical", "PreAssessmentStepTime")
    for pa in PreAssessment.objects.all().iterator():
        StepTime.objects.get_or_create(
            pre_assessment=pa, step="consent",
            defaults={"started_at": pa.created_at})
        if pa.status == "completed" and pa.completed_at:
            StepTime.objects.get_or_create(
                pre_assessment=pa, step="review",
                defaults={"started_at": None, "done_at": pa.completed_at})


class Migration(migrations.Migration):

    dependencies = [
        ("clinical", "0014_preassessmentsteptime"),
    ]

    operations = [
        migrations.RunPython(backfill_step_times, migrations.RunPython.noop),
    ]
