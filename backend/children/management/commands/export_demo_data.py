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
"""
import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand


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
]


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
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=2)

        self.stdout.write(
            f"export_demo_data: {Child.objects.count()} children written to "
            f"{path}. Users are excluded on purpose.")
