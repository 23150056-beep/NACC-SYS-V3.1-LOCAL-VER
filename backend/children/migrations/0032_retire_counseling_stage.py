from django.db import migrations, models


def counseling_to_pre_assessment(apps, schema_editor):
    Child = apps.get_model("children", "Child")
    Child.objects.filter(case_status="counseling").update(case_status="pre_assessment")


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0031_intake_token"),
    ]

    operations = [
        migrations.RunPython(counseling_to_pre_assessment, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="child",
            name="case_status",
            field=models.CharField(
                choices=[("pre_assessment", "Pre-Assessment"), ("terminated", "Terminated")],
                default="pre_assessment", max_length=20),
        ),
    ]
