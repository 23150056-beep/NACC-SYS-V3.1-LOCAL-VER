"""Release the children who were signed off before this module existed.

The handoff row is written by a post_save signal, so it fires only for
assessments completed after the adoption app was installed. Every child signed
off before that has no row, and the banner built from those rows is the only
thing telling staff somebody is waiting.

Running once is exactly right here, unlike the stage seeder: after this, the
signal has every new completion covered and there is nothing left for a second
run to find.
"""
from django.db import migrations

from adoption.seeding import backfill_handoffs

# Frozen copies. A historical model carries no class constants, so these cannot
# be read from clinical.PreAssessment / children.Child at run time — and a
# migration should be pinned to the values as they were regardless. A test
# asserts these still match the models, so a rename cannot leave this quietly
# matching nothing.
COMPLETED = "completed"
ACTIVE = "active"


def backfill(apps, schema_editor):
    backfill_handoffs(
        apps.get_model("adoption", "Handoff"),
        apps.get_model("children", "Child"),
        apps.get_model("clinical", "PreAssessment"),
        completed_status=COMPLETED,
        active_status=ACTIVE,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("adoption", "0003_seed_stages"),
        ("children", "0018_remove_child_guardian_delete_guardian"),
        ("clinical", "0010_selfreportflag"),
    ]

    operations = [
        # No reverse: these rows are the record of who released a child and
        # when. Deleting them on an unapply would lose that.
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
