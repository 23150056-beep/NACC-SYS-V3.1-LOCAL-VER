"""Repair a demo database whose calendar does not work.

Two faults shipped in the seeded caseload:

* the psychologists carrying every child had no availability at all, so no
  session could be booked for anybody, and
* the sessions that did exist sat at whatever time of day the seeder was run -
  in practice nights and weekends.

`seed_demo_data` now avoids both. This exists for the databases that already
have the bad data, because re-seeding to fix a timestamp throws away six
months of invented history along with it.

Idempotent. Availability is filled in rather than overwritten, and an
appointment already inside clinic hours is left exactly where it is, so
running this twice changes nothing the second time.
"""
from django.core.management.base import BaseCommand

from accounts.models import Role, User
from children.models import Child
from clinical import demo_referrals
from config.demo_guard import refuse_if_not_local
from scheduling import demo_schedule


class Command(BaseCommand):
    help = ("Make the demo calendar usable: a working week for every "
            "psychologist, appointments inside clinic hours, and a case "
            "referral for every child so they can be booked at all.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--availability-only", action="store_true",
            help="Create the working weeks but leave existing appointments alone.")
        parser.add_argument(
            "--respread", action="store_true",
            help="Re-place every appointment, not only the out-of-hours ones. "
                 "Use this to thin days an earlier run packed solid.")

    def handle(self, *args, **options):
        refuse_if_not_local()

        psychologists = list(User.objects.filter(
            role__role_name=Role.PSYCHOLOGIST, status=User.ACTIVE).order_by("id"))
        if not psychologists:
            self.stdout.write(self.style.WARNING(
                "No active psychologists, so there is nothing to give a "
                "working week to."))
            return

        pruned = demo_schedule.prune_overlapping_blocks()
        if pruned:
            self.stdout.write(self.style.WARNING(
                f"Removed {pruned} overlapping window(s) left by an earlier run."))
        made = demo_schedule.install_availability(psychologists)
        self.stdout.write(self.style.SUCCESS(
            f"Availability: {made} block(s) added across "
            f"{len(psychologists)} psychologist(s)."))
        for person in psychologists:
            self.stdout.write(f"  {person.fullname or person.email}")

        # Without a referral on file the booking endpoint refuses a child
        # outright, so a demo caseload with none is a calendar that turns
        # everybody away.
        referrals = demo_referrals.install_referrals(
            list(Child.objects.filter(status=Child.ACTIVE)),
            uploaded_by=User.objects.filter(role__role_name=Role.STAFF).first())
        self.stdout.write(self.style.SUCCESS(
            f"Case referrals: {referrals} written for children that had none."))

        if options["availability_only"]:
            return

        moved, kept = demo_schedule.realign_appointments(
            respread=options["respread"])
        self.stdout.write(self.style.SUCCESS(
            f"Appointments: {moved} moved into clinic hours, {kept} already "
            f"there or left alone."))
