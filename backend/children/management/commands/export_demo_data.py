"""Dump the fictional caseload so it can be loaded into a hosted branch.

`seed_demo_data` refuses to run against a hosted database — deliberately, and
that guard stays. Its comment gives the reason: mixing fictional records into
real case files is "not a data loss, something worse: a file that cannot be
trusted." So the demo children travel as a fixture instead.

Users are excluded. The target branch already holds the real accounts — which
are the reason for branching that database at all — and importing the seeder's
four would collide on the unique email.

AssistantJob is excluded too: it stores the questions people typed, and those
routinely name a child.

A final copy of a case study carries its preparer's PRC license number, and the
adoptive parents' table may carry phone numbers and e-mail addresses typed on
this machine. Neither belongs in a file that leaves it, so `scrub_rows` blanks
them before anything is written; the import blanks them again, so a fixture
exported before this existed is still safe to load.
"""
import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand

from children.management.commands.import_demo_data import strip_pap_contacts

# Everything seed_demo_data creates, minus anything identifying a real person.
DEMO_MODELS = [
    "children.Child",
    "clinical.AgencyFormTemplate",
    "clinical.InstrumentCatalog",
    "clinical.ConsentRecord",
    "clinical.PreAssessment",
    "clinical.ProblemEntry",
    "clinical.ResultEntry",
    "clinical.TreatmentPlan",
    "clinical.RemarkNote",
    "clinical.OpinionnaireInvite",
    "clinical.SelfReportFlag",
    "scheduling.Appointment",
    # Case studies travel with their child. The user links in them are the
    # exporting machine's and are re-homed on import (import_demo_data.py).
    "case_study.CaseStudy",
    "case_study.CaseStudySection",
    "case_study.CaseStudyFinal",
]


def scrub_rows(rows):
    """Take what a real person typed out of the exported rows, in place:
    every final copy's preparer license, and every phone number, e-mail address
    and employer address in the adoptive parents' table (the live box and each
    final copy of it). Returns True if anything changed."""
    changed = strip_pap_contacts(rows)
    for row in rows:
        if row.get("model") != "case_study.casestudyfinal":
            continue
        snapshot = row.get("fields", {}).get("snapshot")
        preparer = snapshot.get("preparer") if isinstance(snapshot, dict) else None
        if not isinstance(preparer, dict):
            continue
        for field, blank in (("license_number", ""), ("license_valid_until", None)):
            if preparer.get(field) != blank:
                preparer[field] = blank
                changed = True
    return changed


class Command(BaseCommand):
    help = "Dump the fictional caseload to a fixture for a demo deployment."

    def add_arguments(self, parser):
        parser.add_argument("--output", default="demo_fixture.json",
                            help="Where to write the fixture.")

    def handle(self, *args, **options):
        from children.models import Child

        path = options["output"]
        dump = StringIO()
        call_command("dumpdata", *DEMO_MODELS, indent=2, stdout=dump)
        rows = json.loads(dump.getvalue())
        scrub_rows(rows)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=2)

        self.stdout.write(
            f"export_demo_data: {Child.objects.count()} children written to "
            f"{path}. Users are excluded on purpose.")
