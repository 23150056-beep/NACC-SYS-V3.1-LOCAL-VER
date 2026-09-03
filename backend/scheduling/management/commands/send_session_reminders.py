"""Text each psychologist a count of tomorrow's sessions.

The work is in scheduling/reminders.py, shared with the scheduled endpoint —
this is the door for a person at a keyboard.

    manage.py send_session_reminders            # tomorrow's sessions
    manage.py send_session_reminders --today    # what is left today
    manage.py send_session_reminders --dry-run  # print, send nothing

Safe to run twice: whoever has already been told about a given day is skipped.
"""
from django.core.management.base import BaseCommand

from scheduling.reminders import send_session_reminders


class Command(BaseCommand):
    help = "Text each psychologist how many sessions they have tomorrow."

    def add_arguments(self, parser):
        parser.add_argument("--today", action="store_true",
                            help="Remind about today rather than tomorrow.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Print what would be sent and send nothing.")

    def handle(self, *args, **options):
        report = send_session_reminders(today=options["today"],
                                        dry_run=options["dry_run"])
        for line in report["lines"]:
            self.stdout.write("  " + line)

        if report["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — nothing was sent."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"{report['sent']} reminder(s) queued, {report['skipped']} "
                f"skipped, for {report['date']}."))
