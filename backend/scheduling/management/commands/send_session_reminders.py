"""Text each psychologist a count of tomorrow's sessions.

One message per person per day, not one per appointment: five texts about five
sessions is how a sender gets muted, and a per-appointment message would have
to name the child to be worth reading, which it may not do.

Run it once a day from whatever schedules jobs — Render's cron, Task Scheduler,
or a colleague at 5pm. It is safe to run twice: the second run in a day sends
nothing, because it records who it has already told.

    manage.py send_session_reminders            # tomorrow's sessions
    manage.py send_session_reminders --today    # what is left today
    manage.py send_session_reminders --dry-run  # print, send nothing
"""
from datetime import timedelta

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Role, User
from accounts.sms_notifications import notify_session_reminder
from scheduling.models import Appointment


def _already_told_key(user_id, day):
    return f"session-reminder:{user_id}:{day.isoformat()}"


class Command(BaseCommand):
    help = "Text each psychologist how many sessions they have tomorrow."

    def add_arguments(self, parser):
        parser.add_argument("--today", action="store_true",
                            help="Remind about today rather than tomorrow.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would be sent and send nothing.")

    def handle(self, *args, **options):
        target = timezone.localdate() + (
            timedelta(days=0) if options["today"] else timedelta(days=1))
        word = "today" if options["today"] else "tomorrow"

        appointments = (Appointment.objects
                        .filter(start__date=target, status=Appointment.SCHEDULED)
                        .select_related("psychologist"))

        counts = {}
        for appointment in appointments:
            psychologist = appointment.psychologist
            if psychologist is None:
                continue
            counts[psychologist] = counts.get(psychologist, 0) + 1

        if not counts:
            self.stdout.write(f"No scheduled sessions {word} ({target}). Nothing to send.")
            return

        sent = skipped = 0
        for psychologist, count in sorted(counts.items(), key=lambda kv: kv[0].pk):
            label = psychologist.fullname or psychologist.email
            if not psychologist.phone or not psychologist.phone_verified:
                self.stdout.write(
                    f"  skip  {label}: {count} session(s), no verified number")
                skipped += 1
                continue

            key = _already_told_key(psychologist.pk, target)
            if cache.get(key):
                self.stdout.write(f"  skip  {label}: already told about {target}")
                skipped += 1
                continue

            if options["dry_run"]:
                self.stdout.write(
                    f"  would text {label} ({psychologist.phone}): "
                    f"{count} session(s) {word}")
                continue

            if notify_session_reminder(psychologist, count, when=word):
                # Remembered for a day and a bit, so a second run cannot
                # double-text and a run just after midnight still counts.
                cache.set(key, True, 36 * 3600)
                self.stdout.write(f"  sent  {label}: {count} session(s) {word}")
                sent += 1
            else:
                self.stdout.write(f"  skip  {label}: sending was refused")
                skipped += 1

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — nothing was sent."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"{sent} reminder(s) queued, {skipped} skipped, for {target}."))
