"""The eight stages and their requirement docket, installed by `migrate`.

Reference data, like the PSGC tables — the module has nothing to show without
it. PSGC gets there from entrypoint.sh; this cannot, because entrypoint.sh is a
file this project keeps closed. `migrate` runs on every deploy anyway, so the
schema is a free ride and needs nobody to run anything by hand (Render's Shell
tab is paid-only).

One difference comes with it, and it is the reason this docstring exists: a
data migration runs ONCE per database, where a line in entrypoint.sh runs on
every deploy. If stage_config.py later gains a ninth stage or another
requirement, add a NEW migration that calls install_stages again — editing this
one changes nothing on a database that has already applied it.
"""
from django.db import migrations

from adoption.seeding import install_stages


def seed_stages(apps, schema_editor):
    install_stages(
        apps.get_model("adoption", "AdoptionStage"),
        apps.get_model("adoption", "RequirementTemplate"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("adoption", "0002_pap_adoptioncase_complianceclock_handoff_stageevent_and_more"),
    ]

    operations = [
        # No reverse. A stage carries every case, event and requirement in the
        # module on a cascade, so a reverse that tidied up would take the case
        # data with it. Unapplying this migration leaves the rows alone.
        migrations.RunPython(seed_stages, migrations.RunPython.noop),
    ]
