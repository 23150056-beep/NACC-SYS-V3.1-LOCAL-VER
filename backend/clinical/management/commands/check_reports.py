"""Read and check the reports already on file.

    manage.py check_reports

Two things a report uploaded before today may be missing. Its text, if it is a
Word file - only PDFs were read until now - and the check for another child's
name, case number, age or birthday left in it. This does both, for reports and
case referrals alike (referrals get their text; the check is for reports).

Idempotent. The check runs again on every report each time, because the
caseload it compares against changes; a report keeps its "looked at" mark
unless what the check finds has changed since.

The check is made from the viewpoint of the report's author - the children
THEY can see - exactly as it would have been at upload. A report whose author
is gone is checked as its child's assigned psychologist would see it.
"""
from types import SimpleNamespace

from django.core.management.base import BaseCommand

from clinical.models import CaseReferral, PsychologicalReport
from clinical.report_check import check_for
from clinical.services import ensure_text, readable


class Command(BaseCommand):
    help = "Read Word reports uploaded before they could be read, and check every report."

    def handle(self, *args, **options):
        read = unreadable = 0
        for model in (PsychologicalReport, CaseReferral):
            for doc in model.objects.filter(extracted_text="").select_related("child"):
                if not readable(doc.original_filename or doc.file.name):
                    unreadable += 1
                elif ensure_text(doc):
                    read += 1
                else:
                    unreadable += 1

        checked = flagged = changed = 0
        reports = PsychologicalReport.objects.select_related(
            "child", "author__role", "child__assigned_psychologist__role")
        for report in reports:
            viewer = report.author or report.child.assigned_psychologist
            findings = check_for(SimpleNamespace(user=viewer), report.extracted_text,
                                 report.child)
            checked += 1
            flagged += bool(findings)
            if findings != report.check_findings:
                changed += 1
                report.check_findings = findings
                report.check_reviewed = False
                report.save(update_fields=["check_findings", "check_reviewed"])

        self.stdout.write(f"Text read from {read} file(s); {unreadable} could not be read "
                          "(.doc, or a scan with no text).")
        self.stdout.write(f"Checked {checked} report(s): {flagged} with something to look "
                          f"at, {changed} changed since the last check.")
