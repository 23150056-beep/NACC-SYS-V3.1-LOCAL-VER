from django.db import migrations


def add_officer_role(apps, schema_editor):
    # Only the Officer's row. The other three are made by seed_initial_data
    # (and by tests, with create()), and role_name is unique.
    Role = apps.get_model("accounts", "Role")
    Role.objects.get_or_create(role_name="Officer")


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_remove_license_and_head_of_office"),
    ]

    operations = [
        # Reverse does nothing: an account may already hold the role.
        migrations.RunPython(add_officer_role, migrations.RunPython.noop),
    ]
