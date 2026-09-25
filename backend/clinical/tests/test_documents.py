
import fitz  # PyMuPDF
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import Child
from clinical.models import (
    InstrumentCatalog, PsychologicalReport, RemarkNote, TreatmentPlan, ResultEntry,
)

User = get_user_model()


class DocumentsBase(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=self.admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=self.psy_role)
        self.other = User.objects.create_user(
            email="o@racco1.gov.ph", username="o", password="pass1234", role=self.psy_role)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=self.staff_role)
        self.child = Child.objects.create(social_worker=self.staff, 
            fullname="Ana", case_type="Foster Care", assigned_psychologist=self.psy)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _pdf(self, text="Initial psychological evaluation. Child shows resilience."):
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), text, fontsize=11)
        return SimpleUploadedFile("report.pdf", doc.tobytes(), content_type="application/pdf")


class PsychologicalReportTest(DocumentsBase):
    def test_upload_extracts_text_and_uuid_filename(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/report-files/", {
            "child": self.child.id, "file": self._pdf(),
            "report_type": "initial", "coverage": "Session 1"}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        rep = PsychologicalReport.objects.get()
        self.assertIn("resilience", rep.extracted_text)
        self.assertEqual(rep.original_filename, "report.pdf")
        self.assertNotIn("report.pdf", rep.file.name)  # UUID name on disk
        self.assertTrue(resp.data["has_text"])

    def test_rejects_wrong_extension(self):
        self._auth("p@racco1.gov.ph")
        bad = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        resp = self.client.post("/api/report-files/", {
            "child": self.child.id, "file": bad}, format="multipart")
        self.assertEqual(resp.status_code, 400)

    def test_download_requires_scope(self):
        self._auth("p@racco1.gov.ph")
        rid = self.client.post("/api/report-files/", {
            "child": self.child.id, "file": self._pdf()}, format="multipart").data["id"]
        # assigned psychologist can download
        dl = self.client.get(f"/api/report-files/{rid}/download/")
        self.assertEqual(dl.status_code, 200)
        self.assertEqual(dl["Content-Disposition"].split("filename=")[-1].strip('"'), "report.pdf")
        b"".join(dl.streaming_content)  # consume to release the file handle (Windows)
        # unassigned psychologist cannot even see it
        self._auth("o@racco1.gov.ph")
        self.assertEqual(self.client.get(f"/api/report-files/{rid}/download/").status_code, 404)

    def test_staff_cannot_upload(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.post("/api/report-files/", {
            "child": self.child.id, "file": self._pdf()}, format="multipart")
        self.assertEqual(resp.status_code, 403)


class ConsentScanDownloadTest(DocumentsBase):
    def test_scan_download_scoped(self):
        self._auth("p@racco1.gov.ph")
        scan = SimpleUploadedFile("consent.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        resp = self.client.post("/api/consents/", {
            "child": self.child.id, "signer_name": "M", "status": "signed",
            "scan": scan}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        cid = resp.data["id"]
        dl = self.client.get(f"/api/consents/{cid}/download/")
        self.assertEqual(dl.status_code, 200)
        b"".join(dl.streaming_content)  # release handle (Windows)
        self._auth("o@racco1.gov.ph")
        self.assertEqual(self.client.get(f"/api/consents/{cid}/download/").status_code, 404)

    def test_download_without_scan_404(self):
        from clinical.models import ConsentRecord
        rec = ConsentRecord.objects.create(child=self.child, status="signed")
        self._auth("p@racco1.gov.ph")
        self.assertEqual(self.client.get(f"/api/consents/{rec.id}/download/").status_code, 404)


class RemarkAndPlanTest(DocumentsBase):
    def test_remark_crud(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/remarks/", {
            "child": self.child.id, "text": "Calmer this week."}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(RemarkNote.objects.get().author, self.psy)

    def test_treatment_plan(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/treatment-plans/", {
            "child": self.child.id, "objectives": "Reduce separation anxiety.",
            "interventions": "Weekly play therapy.", "review_date": "2026-09-01"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(TreatmentPlan.objects.get().status, "active")

    def test_staff_read_only(self):
        RemarkNote.objects.create(child=self.child, author=self.psy, text="x")
        self._auth("s@racco1.gov.ph")
        self.assertEqual(len(self.client.get(f"/api/remarks/?child={self.child.id}").data), 1)
        self.assertEqual(self.client.post("/api/remarks/", {
            "child": self.child.id, "text": "y"}, format="json").status_code, 403)


class ResultEntryTest(DocumentsBase):
    def test_manual_result_entry(self):
        tool = InstrumentCatalog.objects.create(title="CBCL", owner=self.psy)
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/result-entries/", {
            "child": self.child.id, "instrument": tool.id,
            "summary": "Elevated internalizing scale; consistent with adjustment difficulties.",
            "classification": "Adjustment difficulties"}, format="json")
        self.assertEqual(resp.status_code, 201)
        entry = ResultEntry.objects.get()
        self.assertEqual(entry.entered_by, self.psy)
        self.assertEqual(resp.data["instrument_title"], "CBCL")

    def test_result_scoped_to_assigned(self):
        hidden = Child.objects.create(fullname="Ben", assigned_psychologist=self.other)
        ResultEntry.objects.create(child=hidden, summary="x", entered_by=self.other)
        self._auth("p@racco1.gov.ph")
        self.assertEqual(len(self.client.get("/api/result-entries/").data), 0)
