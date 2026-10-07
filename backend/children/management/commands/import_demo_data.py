"""Load the fictional caseload into a deployment database.

Intended for a Neon BRANCH that already carries the real user accounts. The
children come from `export_demo_data`; the accounts are whatever the branch
already holds, which is the reason for branching that database at all.

Every imported child is dealt to a psychologist and a social worker that
exist here, and everything recorded about it moves with it. Every user id in
the fixture belongs to the local machine and means nothing on the branch —
left alone, a child and its sessions and notes would point at the wrong
person or at nobody, and a caseload nobody can see is not a demo.

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

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role
from children import demo_custodians, demo_owners, demo_profiles
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


# A custodian's number and consent were given to someone on the exporting
# machine, and demo custodians carry no number at all
# (children/demo_custodians.py).
_CUSTODIAN_CONTACT = {"custodian_contact": "",
                      "custodian_sms_consent": False,
                      "custodian_sms_consent_at": None,
                      "custodian_sms_consent_by": None,
                      "custodian_contact_verified_at": None}


def forget_custodian_contacts(rows):
    """Blank every custodian number and consent in the fixture's children.
    Returns True if anything changed."""
    changed = False
    for row in rows:
        if row.get("model") != "children.child":
            continue
        fields = row.get("fields", {})
        for field, blank in _CUSTODIAN_CONTACT.items():
            if fields.get(field, blank) != blank:
                fields[field] = blank
                changed = True
    return changed


def rehome_people(rows, psychologists, social_workers):
    """Give the fixture's children, and everything recorded about them, to
    accounts that exist here. Returns (children dealt, links moved).

    Every user id in a fixture is the exporting machine's, and on a branch it
    names somebody else or nobody: a missing one fails the whole load, and one
    that lands on a Staff account put 68 of 198 demo sessions "with" a social
    worker (rehearsed 29 Sep 2026). So nothing local is loaded at all.

    Children are dealt round-robin in pk order across the psychologists and
    social workers here. Each record then follows its child: what the child's
    own psychologist or social worker wrote is written by the child's new one.
    A different psychologist - the one the child was with before a transfer -
    becomes whoever received that psychologist's own caseload, so history kept
    from the next psychologist stays somebody else's. Anyone else falls to the
    child's psychologist, or to nobody where there is no child and the field
    allows it (an instrument or form owned by nobody is the shared one).
    """
    User = get_user_model()
    children = sorted((r for r in rows if r.get("model") == "children.child"),
                      key=lambda r: r["pk"])
    local, new = {}, {}
    for index, row in enumerate(children):
        fields = row["fields"]
        local[row["pk"]] = (fields.get("assigned_psychologist"), fields.get("social_worker"))
        new[row["pk"]] = (
            psychologists[index % len(psychologists)].pk,
            social_workers[index % len(social_workers)].pk if social_workers else None)
        fields["assigned_psychologist"], fields["social_worker"] = new[row["pk"]]

    # Where each local account's own caseload went: with its first child.
    caseload = {}
    for pk in sorted(local):
        for was, now in zip(local[pk], new[pk]):
            if was is not None and now is not None:
                caseload.setdefault(was, now)

    moved = 0
    for row in rows:
        if row.get("model") == "children.child":
            continue
        fields = row.get("fields", {})
        child = fields.get("child")
        own_was, own_now = local.get(child, ()), new.get(child, (None, None))
        for field in apps.get_model(row["model"])._meta.concrete_fields:
            if not (field.is_relation and field.related_model is User):
                continue
            was = fields.get(field.name)
            if was is None:
                continue
            if was in own_was and own_now[own_was.index(was)] is not None:
                now = own_now[own_was.index(was)]
            else:
                now = caseload.get(was, own_now[0])
            if now is None and not field.null:
                now = psychologists[0].pk
            fields[field.name] = now
            moved += 1
    return len(children), moved


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
        if upgrade_rows(rows):
            self.stdout.write("  fixture is an older export; child rows upgraded")
        if forget_custodian_contacts(rows):
            self.stdout.write("  custodian numbers and consent left behind")
        social_workers = demo_owners.active_staff()
        imported, moved = rehome_people(rows, psychologists, social_workers)
        self.stdout.write(
            f"  {imported} children dealt across {len(psychologists)} psychologist(s) "
            f"and {len(social_workers)} social worker(s) here; {moved} record "
            f"link(s) moved with them")
        # Load the corrected copy, never the file as given: every export names
        # the exporting machine's accounts, and an older one a field the model
        # no longer has.
        fd, corrected = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(rows, handle)
            call_command("loaddata", corrected, verbosity=0)
        finally:
            os.remove(corrected)

        # Rows alone are not a working demo. Both of these are what the
        # booking endpoint checks, and the fixture can carry neither: a
        # referral is a file, and availability belongs to the accounts that
        # live on this database rather than the seeder's.
        blocks = demo_schedule.install_availability(psychologists)
        self.stdout.write(f"  availability: {blocks} block(s) added")
        # Each social worker sees only their own records, so a child with none
        # is one no staff account can see (children/demo_owners.py). The
        # fixture's children were dealt one above; this is for any already
        # here without one.
        demo_owners.assign_social_workers(list(Child.objects.order_by("pk")))
        custodians = demo_custodians.fill_custodians(list(Child.objects.order_by("pk")))
        self.stdout.write(f"  custodians: {custodians} filled in")
        profiles = demo_profiles.fill_profiles(list(Child.objects.order_by("pk")))
        self.stdout.write(f"  health condition and whereabouts: {profiles} filled in")
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
            f"import_demo_data: {imported} children across "
            f"{len(psychologists)} psychologists.")
