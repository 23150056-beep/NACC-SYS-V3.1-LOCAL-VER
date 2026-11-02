from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import Child
from clinical.models import ConsentRecord, PreAssessment

User = get_user_model()


class PreAssessmentPipelineStatusTest(APITestCase):
    """Derived 4-state pre-assessment pipeline status on the child profile:
    No Consent Yet → Not Yet Pre-Assessed → In Progress → Completed. There is
    no counseling stage any more (removed 2 Nov 2026), so a completed
    pre-assessment is Completed whatever the case tracker says."""

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=self.admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=self.psy_role)
        self.child = Child.objects.create(fullname="Ana", assigned_psychologist=self.psy)
        token = self.client.post("/api/auth/login/", {
            "email": "a@racco1.gov.ph", "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _status(self):
        return self.client.get(f"/api/children/{self.child.id}/").data["pre_assessment_status"]

    def test_no_consent_yet(self):
        self.assertEqual(self._status(), "No Consent Yet")

    def test_unsigned_consent_still_counts_as_no_consent(self):
        ConsentRecord.objects.create(child=self.child, status="pending")
        self.assertEqual(self._status(), "No Consent Yet")

    def test_signed_consent_without_pre_assessment_is_not_yet_pre_assessed(self):
        ConsentRecord.objects.create(child=self.child, status="signed", signer_name="M")
        self.assertEqual(self._status(), "Not Yet Pre-Assessed")

    def test_started_wizard_is_in_progress(self):
        ConsentRecord.objects.create(child=self.child, status="signed", signer_name="M")
        PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status="in_progress")
        self.assertEqual(self._status(), "In Progress")

    def test_completed_pre_assessment_is_completed(self):
        PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status="completed")
        self.assertEqual(self._status(), "Completed")

    def test_completed_outranks_a_newer_in_progress_re_assessment(self):
        # A re-assessment in progress doesn't erase the fact the child answered.
        PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status="completed")
        PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status="in_progress")
        self.assertEqual(self._status(), "Completed")
