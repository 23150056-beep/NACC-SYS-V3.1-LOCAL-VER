"""Everything the container does before it serves, in ONE process.

entrypoint.sh used to start four separate Django processes (migrate,
seed_initial_data, seed_psgc, backfill_psgc), each importing Django again on a
0.1 CPU instance. This runs the same steps, in the same order, with the same
meaning, through call_command.

Any exception propagates, so the process exits non-zero and (entrypoint has
`set -e`) the container dies rather than serving a half-migrated database.
The "==>" lines are how the deploy log shows which step failed; each one is
flushed as it is printed, and the step's elapsed seconds follow it.
"""
import os
import sys
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand


def _say(text):
    print(text, flush=True)
    sys.stdout.flush()


class Command(BaseCommand):
    help = ("Run the boot steps in one process: migrate, seed_initial_data "
            "(only when RUN_SEED=true), seed_psgc, backfill_psgc --apply.")

    def _step(self, title, name, **kwargs):
        _say(f"==> {title}")
        started = time.monotonic()
        call_command(name, **kwargs)
        _say(f"==> {title} ({time.monotonic() - started:.1f} s)")

    def handle(self, *args, **options):
        self._step("Applying database migrations", "migrate", interactive=False)

        # First boot only; idempotent, so leaving RUN_SEED=true is harmless.
        if os.environ.get("RUN_SEED", "false") == "true":
            self._step("Seeding roles and initial admin", "seed_initial_data")

        # Reference data: matches on the PSGC code and updates in place.
        self._step("Loading PSGC addresses", "seed_psgc")

        # Only touches records with no codes yet; a no-op once none are left.
        self._step("Backfilling PSGC codes on existing addresses",
                   "backfill_psgc", apply=True)
