"""Record what Google already verified.

`email_verified` arrived defaulting to False, which is right for a typed
address and wrong for every account that came through the Google door before
it existed. Google checks the address before the credential ever reaches this
system - that is the whole difference between the two doors, and the sign-up
queue has recorded which one each request used since the day it was built.

Without this, approving any Google request already sitting in the queue is
refused for a reason that is not true of it, and the applicant is asked to
confirm an address nobody doubted.

Deliberately narrow. A form sign-up already waiting keeps `email_verified`
False, because nothing verified it - marking those would be inventing the
fact the new check exists to establish. Those applicants confirm with a code
like everybody else.
"""
from django.db import migrations


def trust_googles_check(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(google_sub__isnull=False).exclude(
        google_sub="").update(email_verified=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_email_verified"),
    ]

    operations = [
        # No reverse. Un-verifying these would not restore a previous state -
        # there was no previous state, the column did not exist - it would
        # just lock the same people out again.
        migrations.RunPython(trust_googles_check, migrations.RunPython.noop),
    ]
