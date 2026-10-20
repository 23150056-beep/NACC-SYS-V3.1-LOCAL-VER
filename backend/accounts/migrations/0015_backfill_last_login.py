from django.db import migrations
from django.db.models import Max, Q


def stamp_from_the_sign_in_lines(apps, schema_editor):
    """Give every account the sign-in the audit trail already knows about.

    Only the Google door used to set `last_login`, so a colleague who signs in
    with a password showed "Last sign-in: Never" in Users while the ISA's console
    (which reads the sign-in lines) listed them as signed in today. The login
    view now stamps it on every sign-in; this brings the accounts that signed in
    before that up to date. It only ever moves a date forward.
    """
    ActivityLog = apps.get_model("activity", "ActivityLog")
    User = apps.get_model("accounts", "User")
    newest = (ActivityLog.objects.filter(action="login", actor__isnull=False)
              .values("actor_id").annotate(at=Max("created_at")))
    for row in newest:
        (User.objects.filter(pk=row["actor_id"])
         .filter(Q(last_login__isnull=True) | Q(last_login__lt=row["at"]))
         .update(last_login=row["at"]))


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_profile_license"),
        ("activity", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(stamp_from_the_sign_in_lines, migrations.RunPython.noop),
    ]
