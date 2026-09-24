"""Tests for the blueprint additions: case tracker, baseline category,
case-referral sharing, and the QR opinionnaire flow."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import Child
from clinical.models import (
    AgencyFormTemplate, CaseReferral, OpinionnaireInvite, ResultEntry,
)

User = get_user_model()


class BlueprintBase(APITestCase):
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
            fullname="Ana Lopez", case_type="Adoption", assigned_psychologist=self.psy)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)


class CaseTrackerTest(BlueprintBase):
    def test_default_stage_is_pre_assessment(self):
        self.assertEqual(self.child.case_status, Child.STAGE_PRE_ASSESSMENT)

    def test_psychologist_advances_to_counseling(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/advance-status/",
                                {"case_status": "counseling"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.child.refresh_from_db()
        self.assertEqual(self.child.case_status, Child.STAGE_COUNSELING)

    def test_staff_cannot_advance(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/advance-status/",
                                {"case_status": "counseling"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_terminated_not_settable_directly(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/advance-status/",
                                {"case_status": "terminated"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_terminate_sets_stage_terminated(self):
        self._auth("p@racco1.gov.ph")
        self.client.post(f"/api/children/{self.child.id}/terminate/", {
            "reason_category": "Adoption finalized", "note": "Done."}, format="json")
        self.child.refresh_from_db()
        self.assertEqual(self.child.case_status, Child.STAGE_TERMINATED)

    def test_baseline_category_on_result_entry(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/result-entries/", {
            "child": self.child.id, "summary": "Findings.",
            "baseline_category": "Needs Counseling"}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(ResultEntry.objects.get().baseline_category, "Needs Counseling")

    def test_invalid_baseline_category_rejected(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/result-entries/", {
            "child": self.child.id, "summary": "x",
            "baseline_category": "Maybe"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_dashboard_counts_counseling_per_psychologist(self):
        self.child.case_status = Child.STAGE_COUNSELING
        self.child.save()
        self._auth("a@racco1.gov.ph")
        resp = self.client.get("/api/reports/dashboard/")
        self.assertEqual(resp.data["census"]["by_case_status"]["counseling"], 1)
        self.assertEqual(resp.data["counseling_per_psychologist"][0]["count"], 1)


class CaseReferralTest(BlueprintBase):
    def _pdf(self):
        import fitz
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "Case referral: family background and history.", fontsize=11)
        return SimpleUploadedFile("case-referral.pdf", doc.tobytes(), content_type="application/pdf")

    def test_staff_uploads_case_referral(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.post("/api/case-referrals/", {
            "child": self.child.id, "file": self._pdf(),
            "description": "Intake case referral"}, format="multipart")
        self.assertEqual(resp.status_code, 201)
        cs = CaseReferral.objects.get()
        self.assertEqual(cs.uploaded_by, self.staff)
        self.assertIn("family background", cs.extracted_text)

    def test_psychologist_cannot_upload_but_can_view_and_download(self):
        self._auth("s@racco1.gov.ph")
        rid = self.client.post("/api/case-referrals/", {
            "child": self.child.id, "file": self._pdf()}, format="multipart").data["id"]
        self._auth("p@racco1.gov.ph")
        self.assertEqual(self.client.post("/api/case-referrals/", {
            "child": self.child.id, "file": self._pdf()}, format="multipart").status_code, 403)
        listed = self.client.get(f"/api/case-referrals/?child={self.child.id}").data
        self.assertEqual(len(listed), 1)
        dl = self.client.get(f"/api/case-referrals/{rid}/download/")
        self.assertEqual(dl.status_code, 200)
        b"".join(dl.streaming_content)

    def test_unassigned_psychologist_sees_nothing(self):
        self._auth("s@racco1.gov.ph")
        self.client.post("/api/case-referrals/", {
            "child": self.child.id, "file": self._pdf()}, format="multipart")
        self._auth("o@racco1.gov.ph")
        self.assertEqual(len(self.client.get("/api/case-referrals/").data), 0)


class OpinionnaireTest(BlueprintBase):
    def setUp(self):
        super().setUp()
        self.survey_tpl = AgencyFormTemplate.objects.create(
            form_type="self_report_gov", title="NACC Self-Report Questionnaire",
            owner=self.psy, attestation=True,
            fields=[{"label": "How do you feel today?", "field_type": "long_text"},
                    {"label": "Do you sleep well?", "field_type": "yes_no"}])
        self.consent_tpl = AgencyFormTemplate.objects.create(
            form_type="consent", title="Consent", owner=self.psy, attestation=True)

    def _invite(self):
        return self.client.post("/api/opinionnaire-invites/", {
            "child": self.child.id, "template": self.survey_tpl.id}, format="json")

    def test_staff_creates_invite_with_token_and_expiry(self):
        self._auth("s@racco1.gov.ph")
        resp = self._invite()
        self.assertEqual(resp.status_code, 201)
        inv = OpinionnaireInvite.objects.get()
        self.assertEqual(len(inv.token), 32)
        self.assertTrue(inv.is_open)

    def test_only_self_report_templates_allowed(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.post("/api/opinionnaire-invites/", {
            "child": self.child.id, "template": self.consent_tpl.id}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("template", resp.data)

    def test_public_fetch_returns_fields_and_first_name_only(self):
        self._auth("s@racco1.gov.ph")
        token = self._invite().data["token"]
        self.client.credentials()  # anonymous
        resp = self.client.get(f"/api/opinionnaire/{token}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["first_name"], "Ana")
        self.assertNotIn("Lopez", str(resp.data))
        self.assertEqual(len(resp.data["fields"]), 2)

    def test_public_submit_saves_answers_single_use(self):
        self._auth("s@racco1.gov.ph")
        token = self._invite().data["token"]
        self.client.credentials()
        resp = self.client.post(f"/api/opinionnaire/{token}/submit/", {
            "answers": {"How do you feel today?": "I feel sad when it is dark.",
                        "Do you sleep well?": "No",
                        "Injected": "x"}}, format="json")
        self.assertEqual(resp.status_code, 200)
        inv = OpinionnaireInvite.objects.get()
        self.assertEqual(inv.status, "submitted")
        self.assertNotIn("Injected", inv.answers)  # unknown labels dropped
        # second submit blocked
        again = self.client.post(f"/api/opinionnaire/{token}/submit/", {
            "answers": {"Do you sleep well?": "Yes"}}, format="json")
        self.assertEqual(again.status_code, 410)

    def test_expired_invite_gone(self):
        self._auth("s@racco1.gov.ph")
        token = self._invite().data["token"]
        OpinionnaireInvite.objects.update(expires_at=timezone.now() - timezone.timedelta(days=1))
        self.client.credentials()
        self.assertEqual(self.client.get(f"/api/opinionnaire/{token}/").status_code, 410)

    def test_bad_token_404(self):
        self.assertEqual(self.client.get("/api/opinionnaire/deadbeef/").status_code, 404)

    def test_answers_visible_in_child_report_bundle(self):
        self._auth("s@racco1.gov.ph")
        token = self._invite().data["token"]
        self.client.credentials()
        self.client.post(f"/api/opinionnaire/{token}/submit/", {
            "answers": {"Do you sleep well?": "No"}}, format="json")
        self._auth("p@racco1.gov.ph")
        bundle = self.client.get(f"/api/reports/child/{self.child.id}/").data
        self.assertEqual(len(bundle["opinionnaires"]), 1)
        self.assertEqual(bundle["opinionnaires"][0]["answers"]["Do you sleep well?"], "No")
