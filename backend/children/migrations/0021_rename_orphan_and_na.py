"""Two values renamed on 24 Sep 2026: category "Orphan" is "Orphaned", and
birth status "N/A" is "Unknown". The same answer under a new name, so the
records are moved rather than left holding a value the form no longer offers.

"Stepparent" goes with them. It was never on the list - the demo seeder wrote
it, misspelling "Step-parent" - and a demo record showing it as a value that
is "no longer offered" would be the seeder's mistake presented as history.
The seeder's "Domestic" and "Relative" are left alone: each could be more
than one of the real types, and guessing would put words in a record.

Birth status "Child" and adoption types "SIBRA" and "ICA Relative" were
RETIRED, not renamed - there is no value they become - so they are left where
they are. The serializer keeps them valid on the records that hold them.
"""
from django.db import migrations

RENAMES = [
    ("case_category", "Orphan", "Orphaned"),
    ("birth_status", "N/A", "Unknown"),
    ("type_of_adoption", "Stepparent", "Step-parent"),
]


def forwards(apps, schema_editor):
    Child = apps.get_model("children", "Child")
    for field, old, new in RENAMES:
        Child.objects.filter(**{field: old}).update(**{field: new})


def backwards(apps, schema_editor):
    # "Step-parent" was a real value before; only the first two go back.
    Child = apps.get_model("children", "Child")
    for field, old, new in RENAMES[:2]:
        Child.objects.filter(**{field: new}).update(**{field: old})


class Migration(migrations.Migration):

    dependencies = [
        ("children", "0020_child_profile_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
