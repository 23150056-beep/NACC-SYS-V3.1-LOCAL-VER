"""Older open pre-assessments were all created with current_step = "consent"
(0012's default), so the child's page said "Started - Consent" for rows that
were in fact well on. Work the step out from what the row holds. Rows already
completed, or already past Consent, are left alone. Noop reverse."""
from django.db import migrations


def step_for(consent_id, interview_id, instrument_count):
    if consent_id is None:
        return "consent"
    if instrument_count:
        return "problems"
    if interview_id is None:
        return "interview"
    return "instruments"


def backfill_current_step(apps, schema_editor):
    PreAssessment = apps.get_model("clinical", "PreAssessment")
    rows = (PreAssessment.objects.exclude(status="completed")
            .filter(current_step="consent").prefetch_related("instruments"))
    for pa in rows:
        step = step_for(pa.consent_id, pa.interview_id, len(pa.instruments.all()))
        if step != "consent":
            PreAssessment.objects.filter(pk=pa.pk).update(current_step=step)


class Migration(migrations.Migration):

    dependencies = [
        ("clinical", "0012_preassessment_current_step"),
    ]

    operations = [
        migrations.RunPython(backfill_current_step, migrations.RunPython.noop),
    ]
