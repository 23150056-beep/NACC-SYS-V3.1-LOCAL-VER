"""A clinical record stays on a child its editor may write to.

perform_update checked the child a record was on BEFORE the change, and every
serializer in this family leaves `child` writable, so one PATCH could move a
psychologist's report, remark or plan onto a child who is not theirs - accepted
with a 200, and then out of their own sight. The destination is checked too.

Moving a record between two children the editor may write to is unchanged:
whether that should be possible at all is the owner's call, not this fix's.
"""
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role
from children.models import Child
from clinical.models import (ClinicalInterviewRecord, ConsentRecord, PreAssessment,
                             ProblemEntry, PsychologicalReport, RemarkNote,
                             ResultEntry, TreatmentPlan)
from clinical.report_check import OTHER_CHILD
from clinical.urls import router
from clinical.views import _ChildScopedClinicalViewSet


def _report(child, psy):
    report = PsychologicalReport(child=child, author=psy, original_filename="r.docx",
                                 extracted_text="Name: Maria Santos\nReferred for support.")
    report.file.save("r.docx", ContentFile(b"PK stand-in"), save=True)
    return report


# Every endpoint whose viewset shares the rule, and how to make one of its rows.
ENDPOINTS = {
    "pre-assessments": lambda c, p: PreAssessment.objects.create(child=c, psychologist=p, status="in_progress"),
    "consents": lambda c, p: ConsentRecord.objects.create(child=c, status="signed"),
    "interviews": lambda c, p: ClinicalInterviewRecord.objects.create(child=c),
    "problems": lambda c, p: ProblemEntry.objects.create(child=c, description="Sleep disturbance"),
    "report-files": _report,
    "remarks": lambda c, p: RemarkNote.objects.create(child=c, author=p, text="Note."),
    "treatment-plans": lambda c, p: TreatmentPlan.objects.create(child=c, author=p, objectives="Sleep."),
    "result-entries": lambda c, p: ResultEntry.objects.create(child=c, entered_by=p, summary="x"),
}


class _Caseloads(TestCase):
    """Two psychologists, three children, an administrator. No tests here."""

    def setUp(self):
        User = get_user_model()
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(email="a@racco1.gov.ph", username="a",
                                            password="pass1234", role=psy_role)
        self.other = User.objects.create_user(email="b@racco1.gov.ph", username="b",
                                              password="pass1234", role=psy_role)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="pass1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR))
        self.mine = Child.objects.create(fullname="Maria Santos", first_name="Maria",
                                         last_name="Santos", assigned_psychologist=self.psy)
        self.mine_too = Child.objects.create(fullname="Pedro Reyes", first_name="Pedro",
                                             last_name="Reyes", assigned_psychologist=self.psy)
        self.theirs = Child.objects.create(fullname="Juan Cruz", first_name="Juan",
                                           last_name="Cruz", assigned_psychologist=self.other)

    def patch(self, user, url, data, fmt="json"):
        client = APIClient()
        client.force_authenticate(user)
        return client.patch(url, data, format=fmt)


class RecordMoveTest(_Caseloads):
    def test_every_endpoint_sharing_the_rule_is_covered(self):
        # A ninth viewset on the same base is tested the day it is added.
        shared = {prefix for prefix, viewset, _ in router.registry
                  if issubclass(viewset, _ChildScopedClinicalViewSet)}
        self.assertEqual(shared, set(ENDPOINTS))

    def test_a_record_cannot_be_moved_to_another_psychologists_child(self):
        for prefix, make in ENDPOINTS.items():
            with self.subTest(prefix):
                row = make(self.mine, self.psy)
                res = self.patch(self.psy, f"/api/{prefix}/{row.pk}/", {"child": self.theirs.pk})
                self.assertEqual(403, res.status_code, res.data)
                row.refresh_from_db()
                self.assertEqual(self.mine, row.child)

    def test_nor_by_the_other_psychologist_onto_theirs(self):
        # The same hole from the other side: B cannot even see A's record, so
        # it is refused before the destination is looked at.
        for prefix, make in ENDPOINTS.items():
            with self.subTest(prefix):
                row = make(self.mine, self.psy)
                res = self.patch(self.other, f"/api/{prefix}/{row.pk}/", {"child": self.theirs.pk})
                self.assertIn(res.status_code, (403, 404), res.data)
                row.refresh_from_db()
                self.assertEqual(self.mine, row.child)

    def test_between_the_editors_own_children_is_unchanged(self):
        for prefix, make in ENDPOINTS.items():
            with self.subTest(prefix):
                row = make(self.mine, self.psy)
                res = self.patch(self.psy, f"/api/{prefix}/{row.pk}/", {"child": self.mine_too.pk})
                self.assertEqual(200, res.status_code, res.data)
                row.refresh_from_db()
                self.assertEqual(self.mine_too, row.child)

    def test_an_administrator_may_still_move_one(self):
        for prefix, make in ENDPOINTS.items():
            with self.subTest(prefix):
                row = make(self.mine, self.psy)
                res = self.patch(self.admin, f"/api/{prefix}/{row.pk}/", {"child": self.theirs.pk})
                self.assertEqual(200, res.status_code, res.data)
                row.refresh_from_db()
                self.assertEqual(self.theirs, row.child)

    def test_naming_the_same_child_again_is_not_a_move(self):
        # A client that sends the whole row back, child included, still saves.
        row = ENDPOINTS["remarks"](self.mine, self.psy)
        res = self.patch(self.psy, f"/api/remarks/{row.pk}/", {"child": self.mine.pk, "text": "Edited."})
        self.assertEqual(200, res.status_code, res.data)
        row.refresh_from_db()
        self.assertEqual("Edited.", row.text)


class ReportUpdateTest(_Caseloads):
    """The report's text, its check and its summary were all read from one
    file filed against one child. An update cannot swap the file underneath
    them, and a report moved to another child is checked against that child."""

    def test_the_file_cannot_be_replaced(self):
        report = _report(self.mine, self.psy)
        before = (report.file.name, report.extracted_text)
        swap = SimpleUploadedFile("other.docx", b"PK other", content_type="application/octet-stream")
        res = self.patch(self.psy, f"/api/report-files/{report.pk}/", {"file": swap}, fmt="multipart")
        self.assertEqual(400, res.status_code, res.data)
        self.assertIn("file", res.data)
        report.refresh_from_db()
        self.assertEqual(before, (report.file.name, report.extracted_text))

    def test_its_type_and_coverage_can_still_be_edited(self):
        report = _report(self.mine, self.psy)
        res = self.patch(self.psy, f"/api/report-files/{report.pk}/",
                         {"report_type": "final", "coverage": "Sessions 1-6"})
        self.assertEqual(200, res.status_code, res.data)
        report.refresh_from_db()
        self.assertEqual(("final", "Sessions 1-6"), (report.report_type, report.coverage))

    def test_moved_between_own_children_it_is_checked_again(self):
        # Filed against Maria, whose name it carries: nothing to see. Moved to
        # Pedro, it now names another child - and a finding already marked
        # looked at was about the other filing, so that goes too.
        report = _report(self.mine, self.psy)
        report.check_findings = []
        report.check_reviewed = True
        report.save()
        res = self.patch(self.psy, f"/api/report-files/{report.pk}/", {"child": self.mine_too.pk})
        self.assertEqual(200, res.status_code, res.data)
        report.refresh_from_db()
        self.assertIn((OTHER_CHILD, self.mine.pk),
                      [(f["kind"], f.get("child")) for f in report.check_findings])
        self.assertFalse(report.check_reviewed)
        self.assertEqual(report.check_findings, res.data["check_findings"])

    def test_an_edit_that_does_not_move_it_leaves_the_check_alone(self):
        report = _report(self.mine, self.psy)
        report.check_findings = [{"kind": "age", "message": "Age 12 is not this child's."}]
        report.check_reviewed = True
        report.save()
        self.patch(self.psy, f"/api/report-files/{report.pk}/", {"coverage": "Sessions 1-2"})
        report.refresh_from_db()
        self.assertEqual("age", report.check_findings[0]["kind"])
        self.assertTrue(report.check_reviewed)
