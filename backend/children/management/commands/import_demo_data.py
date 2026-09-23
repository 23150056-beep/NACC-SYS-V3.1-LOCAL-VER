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

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role
from children.models import Child
from clinical import demo_referrals, demo_reports
from scheduling import demo_schedule


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
        referrals = demo_referrals.install_referrals(
            list(Child.objects.filter(status=Child.ACTIVE)),
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
