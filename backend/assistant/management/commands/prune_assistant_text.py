from django.core.management.base import BaseCommand

from assistant import retention

LABELS = {
    "chat": "chat questions and tool calls",
    "drafts": "drafted briefs and polished remarks",
    "summaries_aged": "document summaries older than %d days" % retention.SUMMARY_DAYS,
    "summaries_finished": "document summaries confirmed, or whose document is gone",
    "self_report": "self-report check replies",
}


class Command(BaseCommand):
    help = ("Blank the text kept on assistant log rows once it has outlived its use "
            "(see assistant/retention.py). The rows themselves, and who/when/which "
            "child, are kept. The server does the same by itself at most once an "
            "hour; this is for doing it by hand.")

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Only count what would be blanked.")

    def handle(self, *args, dry_run=False, **options):
        counts = retention.prune(dry_run=dry_run)
        verb = "Would blank" if dry_run else "Blanked"
        for key, label in LABELS.items():
            self.stdout.write(f"{verb} {counts[key]:>5}  {label}")
        self.stdout.write("Rows, types, people, children and dates are kept.")
