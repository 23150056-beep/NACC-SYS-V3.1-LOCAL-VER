"""Birth status: "Marital" is "Marital Child", "Non-Marital" is "Non-Marital Child"
(owner, 15 Nov 2026).

The same answers under new names, so the records are moved rather than left
holding values the form no longer offers - the way children 0035 moved
"Without Known Parents". A VALUE in a column, not a column: the release still
serving during the deploy keeps working, because its serializer lets a
record's own unchanged birth status through whatever the list says.
Reversible. "Child" is retired in code only (intake.RETIRED_BIRTH_STATUSES)
and is not touched here; nor are "Unknown" and blank.

Case study final snapshots are copies of what was signed and are not touched
(a reprint of an old final still prints "Marital" / "Non-Marital");
ActivityLog labels are history and are left as they are.
"""
from django.db import migrations, models

RENAMES = {"Marital": "Marital Child", "Non-Marital": "Non-Marital Child"}


def forwards(apps, schema_editor):
    Child = apps.get_model("children", "Child")
    for old, new in RENAMES.items():
        Child.objects.filter(birth_status=old).update(birth_status=new)


def backwards(apps, schema_editor):
    Child = apps.get_model("children", "Child")
    for old, new in RENAMES.items():
        Child.objects.filter(birth_status=new).update(birth_status=old)


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0035_rename_without_known_parents_foundling"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AlterField(
            model_name="child",
            name="birth_status",
            field=models.CharField(blank=True, choices=[
                ("Marital Child", "Marital Child"),
                ("Non-Marital Child", "Non-Marital Child"),
                ("Unknown", "Unknown"),
            ], max_length=20),
        ),
    ]
