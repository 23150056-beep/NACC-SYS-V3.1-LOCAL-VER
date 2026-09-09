"""Release children whose assessment was completed before this module existed.

The handoff row is written by a post_save signal, so it fires only for
assessments completed after the adoption app was installed. Every child signed
off before that has no row — and the banner built from those rows is the only
thing that tells staff somebody is waiting. Without this the module would go
live blind to its entire existing backlog.

A deploy does not need this either: migration 0004 does the same work once, on
the `migrate` that entrypoint.sh already runs. This command is for a local
database, and shares its implementation with that migration.

Idempotent: it creates a row only where a completed assessment has none, skips
children already in the pipeline, and skips archived records.
"""
from django.core.management.base import BaseCommand

from adoption.models import Handoff
from adoption.seeding import backfill_handoffs
from children.models import Child
from clinical.models import PreAssessment


class Command(BaseCommand):
    help = "Create handoff rows for assessments completed before this app existed."

    def handle(self, *args, **options):
        made = backfill_handoffs(
            Handoff, Child, PreAssessment,
            completed_status=PreAssessment.COMPLETED,
            active_status=Child.ACTIVE,
        )

        if options.get("verbosity", 1):
            self.stdout.write(self.style.SUCCESS(
                f"Adoption handoffs: {made} backfilled, "
                f"{Handoff.objects.count()} total."))
