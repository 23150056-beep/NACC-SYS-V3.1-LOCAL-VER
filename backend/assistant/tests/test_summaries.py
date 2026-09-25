from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child
from clinical.models import CaseReferral, PsychologicalReport

User = get_user_model()


class SummaryTestBase(APITestCase):
    """Shared fixtures only — no tests. Task 14 extends this too, and a
    subclass inheriting these tests would silently run them twice."""

    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.other = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)
        self.child = Child.objects.create(social_worker=self.staff, fullname="Maria", assigned_psychologist=self.psy)
        self.report = PsychologicalReport.objects.create(
            child=self.child, author=self.psy, extracted_text="Full report text.")
        self.referral = CaseReferral.objects.create(
            child=self.child, uploaded_by=self.staff, extracted_text="Referral text.")
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()


class SummaryTest(SummaryTestBase):
    def test_assigned_psychologist_can_summarize_a_report(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Summary."):
            res = self.client.post(f"/api/assistant/summarize-report/{self.report.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["draft"], "Summary.")
        self.report.refresh_from_db()
        self.assertEqual(self.report.ai_summary, "Summary.")
        self.assertFalse(self.report.ai_summary_confirmed)
        self.assertEqual(AssistantJob.objects.get().input_ref,
                         f"report:{self.report.id}")

    def test_staff_can_summarize_a_referral(self):
        self.client.force_authenticate(self.staff)
        with patch.object(services.OllamaClient, "generate", return_value="Summary."):
            res = self.client.post(
                f"/api/assistant/summarize-case-referral/{self.referral.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(AssistantJob.objects.get().input_ref,
                         f"casereferral:{self.referral.id}")

    def test_unassigned_psychologist_gets_404(self):
        self.client.force_authenticate(self.other)
        res = self.client.post(f"/api/assistant/summarize-report/{self.report.id}/")
        self.assertEqual(res.status_code, 404)

    def test_400_when_no_extracted_text(self):
        empty = PsychologicalReport.objects.create(child=self.child, extracted_text="")
        self.client.force_authenticate(self.psy)
        res = self.client.post(f"/api/assistant/summarize-report/{empty.id}/")
        self.assertEqual(res.status_code, 400)

    def test_resummarizing_resets_the_confirmed_flag(self):
        PsychologicalReport.objects.filter(pk=self.report.pk).update(
            ai_summary="Old", ai_summary_confirmed=True)
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="New."):
            self.client.post(f"/api/assistant/summarize-report/{self.report.id}/")
        self.report.refresh_from_db()
        self.assertEqual(self.report.ai_summary, "New.")
        self.assertFalse(self.report.ai_summary_confirmed)

    def test_503_when_the_assistant_is_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.client.force_authenticate(self.psy)
        res = self.client.post(f"/api/assistant/summarize-report/{self.report.id}/")
        self.assertEqual(res.status_code, 503)

    def test_no_history_psychologist_gets_404_for_unauthored_report(self):
        """Carry-history control: assignee_sees_history=False must stop a
        document the caller did not author from ever reaching the model."""
        self.child.assignee_sees_history = False
        self.child.save()
        unauthored = PsychologicalReport.objects.create(
            child=self.child, author=self.other, extracted_text="Someone else's report.")
        self.client.force_authenticate(self.psy)
        res = self.client.post(f"/api/assistant/summarize-report/{unauthored.id}/")
        self.assertEqual(res.status_code, 404)
        self.assertFalse(AssistantJob.objects.exists())

    def test_no_history_psychologist_gets_404_for_unauthored_referral(self):
        self.child.assignee_sees_history = False
        self.child.save()
        # uploaded_by=self.staff, not self.psy -- same mechanism as the report
        # case above, exercised through CaseReferral's `uploaded_by` field.
        other_referral = CaseReferral.objects.create(
            child=self.child, uploaded_by=self.staff, extracted_text="Another referral.")
        self.client.force_authenticate(self.psy)
        res = self.client.post(f"/api/assistant/summarize-case-referral/{other_referral.id}/")
        self.assertEqual(res.status_code, 404)

    def test_no_history_psychologist_can_still_summarize_their_own_report(self):
        self.child.assignee_sees_history = False
        self.child.save()
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Summary."):
            res = self.client.post(f"/api/assistant/summarize-report/{self.report.id}/")
        self.assertEqual(res.status_code, 200)

    def test_history_flag_true_unaffected_for_unauthored_report(self):
        """assignee_sees_history defaults to True and must stay unaffected."""
        assert self.child.assignee_sees_history is True
        unauthored = PsychologicalReport.objects.create(
            child=self.child, author=self.other, extracted_text="Someone else's report.")
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Summary."):
            res = self.client.post(f"/api/assistant/summarize-report/{unauthored.id}/")
        self.assertEqual(res.status_code, 200)


class ConfirmSummaryTest(SummaryTestBase):
    """Extends the fixture base, NOT SummaryTest — inheriting its cases would
    run every summarize test a second time."""

    def _draft(self, text="Draft summary."):
        with patch.object(services.OllamaClient, "generate", return_value=text):
            self.client.post(f"/api/assistant/summarize-report/{self.report.id}/")
        return AssistantJob.objects.filter(input_ref=f"report:{self.report.id}").first()

    def test_confirming_unchanged_text_marks_the_job_accepted(self):
        self.client.force_authenticate(self.psy)
        job = self._draft()
        res = self.client.post(
            f"/api/assistant/confirm-summary/{self.report.id}/",
            {"text": "Draft summary."}, format="json")
        self.assertEqual(res.status_code, 200)
        self.report.refresh_from_db()
        job.refresh_from_db()
        self.assertTrue(self.report.ai_summary_confirmed)
        self.assertEqual(job.outcome, AssistantJob.ACCEPTED)

    def test_confirming_changed_text_marks_the_job_edited(self):
        self.client.force_authenticate(self.psy)
        job = self._draft()
        self.client.post(f"/api/assistant/confirm-summary/{self.report.id}/",
                         {"text": "My own words."}, format="json")
        self.report.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.report.ai_summary, "My own words.")
        self.assertEqual(job.outcome, AssistantJob.EDITED)

    def test_whitespace_only_difference_still_counts_as_accepted(self):
        self.client.force_authenticate(self.psy)
        job = self._draft()
        self.client.post(f"/api/assistant/confirm-summary/{self.report.id}/",
                         {"text": "  Draft summary.  "}, format="json")
        job.refresh_from_db()
        self.assertEqual(job.outcome, AssistantJob.ACCEPTED)

    def test_blank_confirmation_is_rejected(self):
        self.client.force_authenticate(self.psy)
        self._draft()
        res = self.client.post(f"/api/assistant/confirm-summary/{self.report.id}/",
                               {"text": "   "}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_unassigned_psychologist_cannot_confirm(self):
        self.client.force_authenticate(self.psy)
        self._draft()
        self.client.force_authenticate(self.other)
        res = self.client.post(f"/api/assistant/confirm-summary/{self.report.id}/",
                               {"text": "Anything."}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_confirming_works_with_the_assistant_switched_off(self):
        """Confirming is the human's own act — it must not need the model."""
        self.client.force_authenticate(self.psy)
        self._draft()
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        res = self.client.post(f"/api/assistant/confirm-summary/{self.report.id}/",
                               {"text": "Mine."}, format="json")
        self.assertEqual(res.status_code, 200)

    def test_non_string_text_is_rejected(self):
        """A non-string JSON value (e.g. {"text": 5}) must 400, not 500."""
        self.client.force_authenticate(self.psy)
        self._draft()
        res = self.client.post(f"/api/assistant/confirm-summary/{self.report.id}/",
                               {"text": 5}, format="json")
        self.assertEqual(res.status_code, 400)
