import re

from django.db import migrations

# Frozen copies of accounts/lockout.py's rule as it stood when this was written:
# a migration must keep doing what it did, however that module changes later.
PREFIX = "Login locked for "
SUFFIX = " after repeated failures"
NOT_AN_EMAIL = "an unrecognised sign-in name"
EMAIL_SHAPE = re.compile(r"[\w.%+'-]+@[\w-]+(?:\.[\w-]+)+")


def keep_only_addresses(apps, schema_editor):
    """Rewrite old lockout lines that name something that is not an address.

    The login view used to write whatever was typed into the box, and people
    type their password there. The line is read by the ISA (the console, and the
    audit feed, which shows the label as it was stored), so a typed password sat
    in the log in clear. The view now keeps the name only when it looks like an
    email address; this applies the same test to the lines already written. A
    label that is still an address is left exactly as it is. Nothing here can
    bring a name back, so it has no reverse.
    """
    ActivityLog = apps.get_model("activity", "ActivityLog")
    rows = ActivityLog.objects.filter(
        category="security", action="updated", actor__isnull=True, entity_id__isnull=True,
        entity_type="User", entity_label__startswith=PREFIX, entity_label__endswith=SUFFIX)
    for row in rows.only("id", "entity_label"):
        label = row.entity_label
        typed = label[len(PREFIX):len(label) - len(SUFFIX)].strip()
        keep = len(typed) <= 120 and typed.isprintable() and EMAIL_SHAPE.fullmatch(typed)
        if not keep:
            ActivityLog.objects.filter(pk=row.pk).update(
                entity_label=f"{PREFIX}{NOT_AN_EMAIL}{SUFFIX}")


class Migration(migrations.Migration):

    dependencies = [
        ("activity", "0008_activitylog_kind"),
    ]

    operations = [
        migrations.RunPython(keep_only_addresses, migrations.RunPython.noop),
    ]
