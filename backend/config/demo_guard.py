"""One answer to "is it safe to write invented data here?".

Two independent checks, because one of them will eventually be wrong: the host
looks hosted, or DEBUG is off. Either is enough to refuse.

It lives here rather than on a command class because more than one command
invents data now, and a guard that exists in two copies is a guard that
protects whichever copy somebody remembered to update.

Callers must run this BEFORE anything opens a database connection. Inside a
transaction.atomic-decorated handler Django connects first, so pointing a
seeder at a hosted database would hang on the connection rather than refuse -
and the guard could not do the one thing it exists for.
"""
from django.conf import settings
from django.core.management.base import CommandError

HOSTED_MARKERS = ("neon.tech", "render.com", "amazonaws.com", "supabase.co")


def refuse_if_not_local():
    db = settings.DATABASES["default"]
    host = str(db.get("HOST", "")).lower()
    if any(marker in host for marker in HOSTED_MARKERS):
        raise CommandError(
            f"Refusing to run: the database host ({host}) looks like a hosted "
            "one. This command is for local databases only.")
    if not settings.DEBUG:
        raise CommandError(
            "Refusing to run with DJANGO_DEBUG=False. Demo data belongs in "
            "development, and a production system should never contain it.")
