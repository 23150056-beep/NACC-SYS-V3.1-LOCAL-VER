"""Install the eight stages and their requirement templates.

Idempotent, and deliberately NOT an overwrite: `get_or_create` means a target
an office has corrected survives. Re-running only fills gaps — a stage that has
been deleted comes back, a stage that has been edited does not get reverted.

A deploy does not need this: migration 0003 installs the same rows through the
`migrate` that entrypoint.sh already runs. This command is for a local database
and for repairing a stage somebody deleted, and it shares one implementation
with the migration so the two can never disagree.
"""
from django.core.management.base import BaseCommand

from adoption.models import AdoptionStage, RequirementTemplate
from adoption.seeding import install_stages


class Command(BaseCommand):
    help = "Seed the adoption process stages and their requirement templates."

    def handle(self, *args, **options):
        created_stages, created_reqs = install_stages(AdoptionStage, RequirementTemplate)

        if options.get("verbosity", 1):
            self.stdout.write(self.style.SUCCESS(
                f"Adoption stages: {created_stages} added, "
                f"{AdoptionStage.objects.count()} total. "
                f"Requirements: {created_reqs} added, "
                f"{RequirementTemplate.objects.count()} total."))
