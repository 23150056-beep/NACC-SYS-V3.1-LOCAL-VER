"""Category "Without Known Parents" is "Foundling" (owner, 14 Nov 2026).

The same answer under a new name, so the records are moved rather than left
holding a value the form no longer offers - the way children 0021 moved
"Orphan". This is a VALUE in a column, not a column: the release still serving
during the deploy keeps working, because its serializer lets a record's own
unchanged category through whatever the list says. Reversible: Foundling goes
back to Without Known Parents.

Case study final snapshots are copies of what was signed and are not touched
(a reprint of an old final still prints the old word); ActivityLog labels are
history and are left as they are.
"""
from django.db import migrations, models

OLD, NEW = "Without Known Parents", "Foundling"


def forwards(apps, schema_editor):
    apps.get_model("children", "Child").objects.filter(case_category=OLD).update(case_category=NEW)


def backwards(apps, schema_editor):
    apps.get_model("children", "Child").objects.filter(case_category=NEW).update(case_category=OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0034_merge_counseling_retirement_and_answer_mailed_at"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="child",
            name="case_category",
            field=models.CharField(blank=True, choices=[
                ("Surrendered", "Surrendered"), ("Abandoned", "Abandoned"),
                ("Dependent", "Dependent"), ("Neglected", "Neglected"),
                ("Foundling", "Foundling"), ("Orphaned", "Orphaned"),
            ], max_length=50),
        ),
    ]
