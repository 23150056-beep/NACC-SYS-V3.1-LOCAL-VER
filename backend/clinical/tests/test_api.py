
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from accounts.models import Role
from children.models import Child
from clinical.models import (
    InstrumentCatalog, AgencyFormTemplate, ConsentRecord,
    ClinicalInterviewRecord, ProblemEntry,
)


User = get_user_model()


class ClinicalBase(APITestCase):
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
            fullname="Ana", case_type="Adoption", assigned_psychologist=self.psy)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)


class InstrumentCatalogTest(ClinicalBase):
    def test_psychologist_creates_own_entry(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/instruments/", {
            "title": "Child Behavior Checklist", "publisher": "ASEBA",
            "category": "behavioral", "age_range": "6-18"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(InstrumentCatalog.objects.get().owner, self.psy)

    def test_admin_create_without_owner_is_shared(self):
        self._auth("a@racco1.gov.ph")
        resp = self.client.post("/api/instruments/", {"title": "WISC-V"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(InstrumentCatalog.objects.get(title="WISC-V").owner)

    def test_psychologist_sees_own_and_shared_entries(self):
        InstrumentCatalog.objects.create(title="Mine", owner=self.psy)
        InstrumentCatalog.objects.create(title="Theirs", owner=self.other)
        InstrumentCatalog.objects.create(title="Shared", owner=None)
        self._auth("p@racco1.gov.ph")
        titles = [i["title"] for i in self.client.get("/api/instruments/").data]
        self.assertEqual(sorted(titles), ["Mine", "Shared"])

    def test_admin_sees_all_entries(self):
        InstrumentCatalog.objects.create(title="Mine", owner=self.psy)
        InstrumentCatalog.objects.create(title="Theirs", owner=self.other)
        self._auth("a@racco1.gov.ph")
        self.assertEqual(len(self.client.get("/api/instruments/").data), 2)

    def test_staff_forbidden(self):
        self._auth("s@racco1.gov.ph")
        self.assertEqual(self.client.get("/api/instruments/").status_code, 403)

    def test_deactivate_hides_from_default_list(self):
        obj = InstrumentCatalog.objects.create(title="Old Tool", owner=self.psy)
        self._auth("p@racco1.gov.ph")
        self.client.post(f"/api/instruments/{obj.id}/deactivate/")
        self.assertEqual(len(self.client.get("/api/instruments/").data), 0)
        self.assertEqual(len(self.client.get("/api/instruments/?include_inactive=true").data), 1)


class AgencyFormTemplateTest(ClinicalBase):
    def _payload(self, **over):
        return {
            "form_type": "consent", "title": "RACCO I Consent to Psychological Services",
            "fields": [{"label": "Guardian name", "field_type": "text"}],
            "attestation": True, **over,
        }

    def test_create_requires_attestation(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/form-templates/", self._payload(attestation=False), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("attestation", resp.data)
        self.assertEqual(AgencyFormTemplate.objects.count(), 0)

    def test_create_with_attestation_stamps_time(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/form-templates/", self._payload(), format="json")
        self.assertEqual(resp.status_code, 201)
        tpl = AgencyFormTemplate.objects.get()
        self.assertIsNotNone(tpl.attested_at)
        self.assertEqual(tpl.owner, self.psy)
        self.assertEqual(tpl.version, 1)

    def test_editing_fields_bumps_version(self):
        self._auth("p@racco1.gov.ph")
        tid = self.client.post("/api/form-templates/", self._payload(), format="json").data["id"]
        resp = self.client.patch(f"/api/form-templates/{tid}/", {
            "fields": [{"label": "Guardian name", "field_type": "text"},
                       {"label": "Date", "field_type": "date"}],
            "attestation": True}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(AgencyFormTemplate.objects.get().version, 2)

    def test_type_filter(self):
        AgencyFormTemplate.objects.create(form_type="consent", title="C", owner=self.psy, attestation=True)
        AgencyFormTemplate.objects.create(form_type="clinical_interview", title="I", owner=self.psy, attestation=True)
        self._auth("p@racco1.gov.ph")
        data = self.client.get("/api/form-templates/?type=consent").data
        self.assertEqual([t["title"] for t in data], ["C"])

    def test_staff_forbidden(self):
        self._auth("s@racco1.gov.ph")
        self.assertEqual(self.client.get("/api/form-templates/").status_code, 403)


class ConsentRecordTest(ClinicalBase):
    def test_assigned_psychologist_records_consent(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/consents/", {
            "child": self.child.id, "signer_name": "Maria Lopez",
            "signer_relationship": "Foster mother", "status": "signed"}, format="json")
        self.assertEqual(resp.status_code, 201)
        rec = ConsentRecord.objects.get()
        self.assertEqual(rec.recorded_by, self.psy)
        self.assertEqual(rec.status, "signed")

    def test_unassigned_psychologist_cannot_record(self):
        self._auth("o@racco1.gov.ph")
        resp = self.client.post("/api/consents/", {
            "child": self.child.id, "signer_name": "X", "status": "signed"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_staff_reads_but_cannot_write(self):
        ConsentRecord.objects.create(child=self.child, signer_name="M", status="signed")
        self._auth("s@racco1.gov.ph")
        self.assertEqual(len(self.client.get(f"/api/consents/?child={self.child.id}").data), 1)
        resp = self.client.post("/api/consents/", {"child": self.child.id, "status": "signed"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_psychologist_scoped_to_assigned_children(self):
        hidden = Child.objects.create(fullname="Ben", assigned_psychologist=self.other)
        ConsentRecord.objects.create(child=hidden, signer_name="X", status="signed")
        self._auth("p@racco1.gov.ph")
        self.assertEqual(len(self.client.get("/api/consents/").data), 0)

    def test_consent_scan_upload_and_download(self):
        self._auth("p@racco1.gov.ph")
        scan = SimpleUploadedFile("consent.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        r = self.client.post("/api/consents/", {
            "child": self.child.id, "signer_name": "Guardian G",
            "status": "signed", "scan": scan,
        }, format="multipart")
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data["has_scan"])
        self.assertTrue(r.data["scan_filename"].endswith(".pdf"))
        self.assertNotIn("scan", r.data)  # write-only — never echoed back
        d = self.client.get(f"/api/consents/{r.data['id']}/download/")
        self.assertEqual(d.status_code, 200)
        b"".join(d.streaming_content)  # release handle (Windows)


class ClinicalInterviewTest(ClinicalBase):
    def test_record_interview_with_answers(self):
        tpl = AgencyFormTemplate.objects.create(
            form_type="clinical_interview", title="Intake Interview",
            owner=self.psy, attestation=True,
            fields=[{"label": "Presenting concern", "field_type": "long_text"}])
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/interviews/", {
            "child": self.child.id, "template": tpl.id,
            "answers": {"Presenting concern": "Difficulty sleeping since placement."}}, format="json")
        self.assertEqual(resp.status_code, 201)
        rec = ClinicalInterviewRecord.objects.get()
        self.assertEqual(rec.interviewer, self.psy)
        self.assertIn("Presenting concern", rec.answers)


class ProblemEntryTest(ClinicalBase):
    def test_log_and_resolve_problem(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/problems/", {
            "child": self.child.id, "description": "Nightmares after visitation",
            "category": "Sleep"}, format="json")
        self.assertEqual(resp.status_code, 201)
        pid = resp.data["id"]
        upd = self.client.patch(f"/api/problems/{pid}/", {"resolved": True}, format="json")
        self.assertEqual(upd.status_code, 200)
        self.assertTrue(ProblemEntry.objects.get().resolved)

    def test_staff_cannot_log(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.post("/api/problems/", {
            "child": self.child.id, "description": "X"}, format="json")
        self.assertEqual(resp.status_code, 403)
