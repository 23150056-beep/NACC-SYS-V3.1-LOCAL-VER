"""Tear down the adoption tracker and the SAMD readiness self-check.

This lives in `children` rather than in either departing app on purpose. A
migration inside `adoption` would be deleted along with it, so a database that
already has the tables would never be told to drop them — the code would go and
the tables would stay, forever, on every deployment that was already running.
A surviving app is the only place a teardown can be reached from.

The `django_migrations` rows go too, and that is the point rather than tidiness.
Django records applied migrations by app label, and it never verifies that the
tables named in those rows exist. Leave `adoption.0001_initial` behind and an
app called `adoption` created at any point in the future is read as already
migrated: Django skips it, reports success, and the tables are simply never
created. That exact failure is on record in this repo — see the `ai` app note in
CLAUDE.md, which is why the assistant app may never be renamed. This deletes the
rows so the name is genuinely free again.

Deliberately irreversible. There is no reverse operation that could bring back
dropped rows, and a no-op reverse would let `migrate children 0018` report
success over a database whose tables are gone.
"""
from django.db import migrations

# Child tables first: every table here is dropped before anything it points at,
# because SQLite cannot DROP ... CASCADE and will refuse an out-of-order drop.
# The names are the models' explicit db_table values (tbl_*), NOT the app_model
# names Django would have generated — dropping the latter would silently do
# nothing at all.
ADOPTION_TABLES = [
    "tbl_adoption_clock",               # -> case
    "tbl_adoption_stage_event",         # -> case, stage
    "tbl_adoption_requirement",         # -> case, stage
    "tbl_adoption_case",                # -> stage, pap
    "tbl_adoption_requirement_template",  # -> stage
    "tbl_adoption_handoff",
    "tbl_adoption_pap",
    "tbl_adoption_stage",
]
SAMD_TABLES = [
    "tbl_samd_response",                # -> assessment
    "tbl_samd_assessment",
]


def drop(apps, schema_editor):
    # IF EXISTS keeps this a no-op on a database built after the removal — CI
    # and any fresh checkout never create these tables in the first place.
    with schema_editor.connection.cursor() as cursor:
        for table in ADOPTION_TABLES + SAMD_TABLES:
            cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
        cursor.execute(
            "DELETE FROM django_migrations WHERE app IN (%s, %s)",
            ["adoption", "samd"],
        )


class Migration(migrations.Migration):

    dependencies = [("children", "0018_remove_child_guardian_delete_guardian")]

    operations = [migrations.RunPython(drop)]
