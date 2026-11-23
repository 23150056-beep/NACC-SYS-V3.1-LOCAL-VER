"""A role the code admits and has put in no other list.

Tests of "an else must not hand a role somebody else's access" use a role row
called "Probe" with `capabilities.EVERY_ROLE` patched, for the one test, to
include it: it can authenticate, and nothing else names it. A door that serves
it anything but a refusal or an empty answer is an else that grants by default.

A test that has to get PAST an earlier gate (a queryset or a permission class)
to reach the branch it is pinning widens that one list as well, with `admit`.
Lists are read through the module (`capabilities.X`), so the patch is seen.
"""
from datetime import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts import capabilities
from accounts.models import Role

PROBE = "Probe"

# Mon 23 Nov 2026, 08:00 in TIME_ZONE. Never the wall clock.
NOW = timezone.make_aware(datetime(2026, 11, 23, 8, 0))


def admit(testcase, *lists):
    """Admit the probe role to the system, and to each named list too
    (`admit(self, "KEEPS_RECORDS")`), until the test ends."""
    for name in ("EVERY_ROLE",) + lists:
        p = patch.object(capabilities, name, getattr(capabilities, name) + (PROBE,))
        p.start()
        testcase.addCleanup(p.stop)


def make_probe(email="probe@racco1.gov.ph"):
    role = Role.objects.get_or_create(role_name=PROBE)[0]
    return get_user_model().objects.create_user(
        email=email, username=email.split("@")[0], password="pass12345", role=role)


def freeze(testcase, moment=NOW):
    p = patch("django.utils.timezone.now", return_value=moment)
    p.start()
    testcase.addCleanup(p.stop)
