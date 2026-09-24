from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import Child
from clinical.models import InstrumentCatalog, ConsentRecord, PreAssessment

User = get_user_model()


class PreAssessmentFlowTest(APITestCase):
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
        self.tool = InstrumentCatalog.objects.create(title="CBCL", owner=self.psy)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _start(self):
        return self.client.post("/api/pre-assessments/", {"child": self.child.id}, format="json")

    def test_start_sets_in_progress_and_psychologist(self):
        self._auth("p@racco1.gov.ph")
        resp = self._start()
        self.assertEqual(resp.status_code, 201)
        pa = PreAssessment.objects.get()
        self.assertEqual(pa.status, "in_progress")
        self.assertEqual(pa.psychologist, self.psy)

    def test_unassigned_psychologist_cannot_start(self):
        self._auth("o@racco1.gov.ph")
        self.assertEqual(self._start().status_code, 403)

    def test_staff_cannot_start(self):
        self._auth("s@racco1.gov.ph")
        self.assertEqual(self._start().status_code, 403)

    def test_complete_requires_signed_consent(self):
        self._auth("p@racco1.gov.ph")
        pid = self._start().data["id"]
        self.client.patch(f"/api/pre-assessments/{pid}/", {"instruments": [self.tool.id]}, format="json")
        resp = self.client.post(f"/api/pre-assessments/{pid}/complete/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("consent", resp.data)

    def test_complete_requires_instruments(self):
        self._auth("p@racco1.gov.ph")
        pid = self._start().data["id"]
        consent = ConsentRecord.objects.create(child=self.child, status="signed", signer_name="M")
        self.client.patch(f"/api/pre-assessments/{pid}/", {"consent": consent.id}, format="json")
        resp = self.client.post(f"/api/pre-assessments/{pid}/complete/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("instruments", resp.data)

    def test_full_flow_completes(self):
        self._auth("p@racco1.gov.ph")
        pid = self._start().data["id"]
        consent = ConsentRecord.objects.create(child=self.child, status="signed", signer_name="M")
        patch = self.client.patch(f"/api/pre-assessments/{pid}/", {
            "consent": consent.id, "instruments": [self.tool.id],
            "notes": "Administered on paper."}, format="json")
        self.assertEqual(patch.status_code, 200)
        resp = self.client.post(f"/api/pre-assessments/{pid}/complete/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "completed")
        self.assertEqual(resp.data["instrument_titles"], ["CBCL"])
        self.assertIsNotNone(PreAssessment.objects.get().completed_at)

    def test_consent_of_other_child_rejected(self):
        stranger = Child.objects.create(fullname="Ben", assigned_psychologist=self.psy)
        consent = ConsentRecord.objects.create(child=stranger, status="signed")
        self._auth("p@racco1.gov.ph")
        pid = self._start().data["id"]
        resp = self.client.patch(f"/api/pre-assessments/{pid}/", {"consent": consent.id}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("consent", resp.data)

    def test_child_profile_surfaces_answered_and_titles(self):
        self._auth("p@racco1.gov.ph")
        before = self.client.get(f"/api/children/{self.child.id}/").data
        self.assertEqual(before["pre_assessment_status"], "No Consent Yet")
        self.assertEqual(before["instruments_used"], [])
        pid = self._start().data["id"]
        consent = ConsentRecord.objects.create(child=self.child, status="signed")
        self.client.patch(f"/api/pre-assessments/{pid}/", {
            "consent": consent.id, "instruments": [self.tool.id]}, format="json")
        self.client.post(f"/api/pre-assessments/{pid}/complete/")
        after = self.client.get(f"/api/children/{self.child.id}/").data
        self.assertEqual(after["pre_assessment_status"], "Answered")
        self.assertEqual(after["instruments_used"], ["CBCL"])

    def test_starting_again_resumes_in_progress_instead_of_duplicating(self):
        self._auth("p@racco1.gov.ph")
        first = self._start()
        self.assertEqual(first.status_code, 201)
        pid = first.data["id"]
        self.client.patch(f"/api/pre-assessments/{pid}/", {"notes": "so far so good"}, format="json")

        second = self._start()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["id"], pid)
        self.assertEqual(second.data["notes"], "so far so good")
        self.assertEqual(PreAssessment.objects.filter(child=self.child).count(), 1)

    def test_starting_after_completion_creates_a_new_one(self):
        self._auth("p@racco1.gov.ph")
        pid = self._start().data["id"]
        consent = ConsentRecord.objects.create(child=self.child, status="signed", signer_name="M")
        self.client.patch(f"/api/pre-assessments/{pid}/", {
            "consent": consent.id, "instruments": [self.tool.id]}, format="json")
        self.client.post(f"/api/pre-assessments/{pid}/complete/")

        again = self._start()
        self.assertEqual(again.status_code, 201)
        self.assertNotEqual(again.data["id"], pid)
        self.assertEqual(PreAssessment.objects.filter(child=self.child).count(), 2)

    def test_staff_can_read_pre_assessments(self):
        PreAssessment.objects.create(child=self.child, psychologist=self.psy, status="completed")
        self._auth("s@racco1.gov.ph")
        resp = self.client.get(f"/api/pre-assessments/?child={self.child.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
