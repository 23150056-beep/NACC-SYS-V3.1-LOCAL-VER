"""Tear down the case study (SCSR) feature.

This lives in `children` rather than in the departing `case_study` app on
purpose. A migration inside `case_study` would be deleted along with it, so a
database that already has the tables would never be told to drop them - the
code would go and the tables would stay, forever, on every deployment that was
already running. A surviving app is the only place a teardown can be reached
from.

The `django_migrations` rows go too, and that is the point rather than
tidiness. Django records applied migrations by app label and never verifies
that the tables exist. Leave `case_study.0001_initial` behind and an app called
`case_study` created later is read as already migrated: skipped, reported as
success, tables never created (the same trap as the `ai` and `adoption` apps,
see 0019_drop_adoption_samd). Deleting the rows frees the name.

Deliberately irreversible: no reverse can bring back dropped rows.
"""
from django.db import migrations

# Child tables first: SQLite cannot DROP ... CASCADE. Names are the models'
# explicit db_table values, not the app_model names Django would generate.
CASE_STUDY_TABLES = [
    "tbl_case_study_final",    # -> case study
    "tbl_case_study_section",  # -> case study
    "tbl_case_study",
]


def drop(apps, schema_editor):
    # IF EXISTS keeps this a no-op on a database built after the removal.
    with schema_editor.connection.cursor() as cursor:
        for table in CASE_STUDY_TABLES:
            cursor.execute(f'DROP TABLE IF EXISTS "{table}"')
        cursor.execute("DELETE FROM django_migrations WHERE app = %s", ["case_study"])


class Migration(migrations.Migration):

    dependencies = [("children", "0034_merge_counseling_retirement_and_answer_mailed_at")]

    operations = [migrations.RunPython(drop)]
