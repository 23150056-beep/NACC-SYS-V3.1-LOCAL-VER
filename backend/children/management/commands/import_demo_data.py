"""Load the fictional caseload into a deployment database.

Intended for a Neon BRANCH that already carries the real user accounts. The
children come from `export_demo_data`; the accounts are whatever the branch
already holds, which is the reason for branching that database at all.

Every imported child is reassigned to a psychologist that exists here. The
fixture's assignee ids belong to the local machine and mean nothing on the
branch — left alone, every child would point at the wrong person or at nobody,
and a caseload nobody can see is not a demo.

It also finishes the job, because loading rows is not the same as loading a
working system. Invented psychological reports are installed here too, for
the same reason referrals are: they are files, and the fixture holds rows. Booking refuses a child with no case referral and a
psychologist with no posted hours, and the fixture carries neither: referrals
are files rather than rows, and availability belongs to the accounts on the
branch, not to the seeder's. `fix_demo_schedule` repairs both and refuses to
run against a hosted database — so without this, a deployed demo had no
supported way to become bookable at all.
"""
import json
import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role
from children import demo_custodians, demo_owners
from children.models import Child
from clinical import demo_referrals, demo_reports
from scheduling import demo_schedule


# A fixture is a file somebody exported on a given day, and it keeps that
# day's field names. These are the changes a child row has been through since;
# the migrations apply the same ones to a database (children 0020 and 0021).
# "Previous Custodian" became the Custodian on 29 Sep 2026 (children 0028):
# the field is renamed, and the old placeholder values - who surrendered the
# child, never who they live with - are cleared, then filled with a demo
# custodian after loading (children/demo_custodians.py).
_RENAMED_FIELDS = {"middle_initial": "middle_name", "surrendered_by": "custodian_name"}
_RENAMED_VALUES = {"case_category": {"Orphan": "Orphaned"},
                   "birth_status": {"N/A": "Unknown"},
                   "type_of_adoption": {"Stepparent": "Step-parent"},
                   "custodian_name": {"Social Worker": "", "Police": "", "Relatives": ""}}


def upgrade_rows(rows):
    """Bring child rows exported before 24 Sep 2026 up to today's model.
    Returns True if anything changed."""
    changed = False
    for row in rows:
        if row.get("model") != "children.child":
            continue
        fields = row.get("fields", {})
        for old, new in _RENAMED_FIELDS.items():
            if old in fields:
                fields[new] = fields.pop(old)
                changed = True
        for field, renames in _RENAMED_VALUES.items():
            if fields.get(field) in renames:
                fields[field] = renames[fields[field]]
                changed = True
    return changed


# What a child row says about the people on the machine it was exported from.
# The social worker is a local account id, exactly like the psychologist: on
# the branch it names somebody else or nobody - a missing id fails the load,
# and a psychologist's id leaves the child with no SW who can see it - so it
# is dealt again below across the staff who are really here. A custodian's
# number and consent were given to someone on that machine, and demo
# custodians carry no number at all (children/demo_custodians.py).
_LOCAL_ONLY = {"social_worker": None,
               "custodian_contact": "",
               "custodian_sms_consent": False,
               "custodian_sms_consent_at": None,
               "custodian_sms_consent_by": None,
               "custodian_contact_verified_at": None}


def forget_local_people(rows):
    """Blank the child fields that only mean something on the exporting
    machine. Returns True if anything changed."""
    changed = False
    for row in rows:
        if row.get("model") != "children.child":
            continue
        fields = row.get("fields", {})
        for field, blank in _LOCAL_ONLY.items():
            if fields.get(field, blank) != blank:
                fields[field] = blank
                changed = True
    return changed


class Command(BaseCommand):
    help = "Load the fictional caseload and assign it to accounts that exist here."

    def add_arguments(self, parser):
        parser.add_argument("--fixture", required=True,
                            help="Path to the file written by export_demo_data.")
        parser.add_argument("--clear", action="store_true",
                            help="Delete existing children first.")
        parser.add_argument("--set-password", default="",
                            help="EMAIL:PASSWORD — give one account a known "
                                 "password, for demonstrating with.")

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        psychologists = list(
            User.objects.filter(role__role_name=Role.PSYCHOLOGIST).order_by("pk"))
        if not psychologists:
            raise CommandError(
                "No psychologist accounts here. Importing would leave every "
                "child unassigned and invisible to everyone.")

        # Checked before loading anything, so a typo does not leave a
        # half-imported database behind.
        email = password = ""
        if options["set_password"]:
            email, _, password = options["set_password"].partition(":")
            if not User.objects.filter(email=email).exists():
                raise CommandError(f"No account here with the email {email}.")

        if options["clear"]:
            removed = Child.objects.count()
            Child.objects.all().delete()
            self.stdout.write(f"  cleared {removed} existing children")

        with open(options["fixture"], encoding="utf-8") as handle:
            rows = json.load(handle)
        self.stdout.write(f"  fixture holds {len(rows)} rows")
        upgraded = upgrade_rows(rows)
        forgotten = forget_local_people(rows)
        if upgraded or forgotten:
            # Load the corrected copy, never the file as given: an older export
            # names a field the model no longer has, and a newer one names the
            # exporting machine's social workers.
            fd, corrected = tempfile.mkstemp(suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(rows, handle)
                call_command("loaddata", corrected, verbosity=0)
            finally:
                os.remove(corrected)
            if upgraded:
                self.stdout.write("  fixture is an older export; child rows upgraded")
            if forgotten:
                self.stdout.write("  local social workers and custodian numbers left behind")
        else:
            call_command("loaddata", options["fixture"], verbosity=0)

        # Round-robin across whoever is really here. The fixture's assignee ids
        # are local and meaningless on this database.
        imported = list(Child.objects.order_by("pk"))
        for index, child in enumerate(imported):
            child.assigned_psychologist = psychologists[index % len(psychologists)]
        Child.objects.bulk_update(imported, ["assigned_psychologist"])

        # Rows alone are not a working demo. Both of these are what the
        # booking endpoint checks, and the fixture can carry neither: a
        # referral is a file, and availability belongs to the accounts that
        # live on this database rather than the seeder's.
        blocks = demo_schedule.install_availability(psychologists)
        self.stdout.write(f"  availability: {blocks} block(s) added")
        # Each social worker sees only their own records, so a child with none
        # is one no staff account can see (children/demo_owners.py).
        demo_owners.assign_social_workers(list(Child.objects.order_by("pk")))
        custodians = demo_custodians.fill_custodians(list(Child.objects.order_by("pk")))
        self.stdout.write(f"  custodians: {custodians} filled in")
        referrals = demo_referrals.install_referrals(
            list(Child.objects.filter(status=Child.ACTIVE).select_related("social_worker")),
            uploaded_by=User.objects.filter(role__role_name=Role.STAFF).first())
        self.stdout.write(f"  case referrals: {referrals} written")
        # After the reassignment above: a report's author is the psychologist
        # the child now belongs to, and its check runs from their viewpoint.
        reports = demo_reports.install_reports(
            Child.objects.filter(status=Child.ACTIVE)
            .select_related("assigned_psychologist").order_by("pk"))
        self.stdout.write(f"  psychological reports: {reports} written")

        if email:
            user = User.objects.get(email=email)
            user.set_password(password)
            user.must_change_password = False
            user.save()
            self.stdout.write(f"  password set for {email}")

        self.stdout.write(
            f"import_demo_data: {len(imported)} children across "
            f"{len(psychologists)} psychologists.")
