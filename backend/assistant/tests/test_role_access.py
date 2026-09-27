"""What each role may get out of the assistant - the drafting half.

The chatbot's tools take scope from request.user and were already held to
the screens by test_tool_resolvers. These cover the three doors the drafting
features left open, each found in the 27 Sep 2026 audit:

* a pre-session brief was cached per CHILD, so whoever opened the child next
  that day read the brief as the first person saw it - an administrator's
  full-history brief reached a psychologist the carry-history control was
  hiding that history from;
* a document summary is a write to the document, and anyone who could read
  the document could overwrite its confirmed summary - a social worker on a
  psychologist's report, a psychologist on a social worker's referral;
* "only the chatbot runs on the hosted model" was written down three times
  and enforced nowhere, so with the hosted flag on, case notes, whole reports
  and a child's own answers were sent to it.
"""
from datetime import datetime, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services, views
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child
from clinical.models import (CaseReferral, OpinionnaireInvite, PsychologicalReport,
                             RemarkNote)
from scheduling.models import Appointment

User = get_user_model()

HOSTED = {
    "ASSISTANT_ALLOW_HOSTED_MODEL": True,
    "ASSISTANT_MODEL_URL": "https://api.example.invalid/v1",
    "ASSISTANT_MODEL_TOKEN": "a-token",
    "ASSISTANT_MODEL_NAME": "@cf/meta/llama-4-scout-17b-16e-instruct",
}


class RoleFixture(APITestCase):
    """One child held by a social worker, assigned to a psychologist who has
    just taken it over from a colleague, with the colleague's history hidden."""

    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff_role = Role.objects.create(role_name=Role.STAFF)
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.previous = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=admin_role)
        self.child = Child.objects.create(
            fullname="Maria Santos", assigned_psychologist=self.psy,
            social_worker=self.sw, assignee_sees_history=False)
        RemarkNote.objects.create(child=self.child, author=self.previous,
                                  text="PREVIOUS PSYCHOLOGIST'S NOTE")
        RemarkNote.objects.create(child=self.child, author=self.psy, text="MY NOTE")
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.addCleanup(self._clear_in_flight)

    def _clear_in_flight(self):
        with views._IN_FLIGHT_LOCK:
            views._IN_FLIGHT.clear()

    @staticmethod
    def _echo_prompt(prompt, system=None):
        """A 'model' that returns what it was given, so a brief carries
        exactly the facts its author was allowed to see."""
        return prompt


class BriefBelongsToWhoeverDraftedItTest(RoleFixture):
    def _latest(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/assistant/brief/child/{self.child.id}/latest/")

    def _draft_as(self, user):
        self.client.force_authenticate(user)
        with patch.object(services.OllamaClient, "generate", side_effect=self._echo_prompt):
            res = self.client.post(f"/api/assistant/brief/child/{self.child.id}/")
        self.assertEqual(res.status_code, 200)
        return res

    def test_an_administrators_brief_is_not_served_to_the_psychologist(self):
        drafted = self._draft_as(self.admin)
        self.assertIn("PREVIOUS PSYCHOLOGIST'S NOTE", drafted.data["draft"])

        res = self._latest(self.psy)
        self.assertEqual(res.status_code, 404)

    def test_the_previous_psychologists_brief_is_not_served_after_reassignment(self):
        self.child.assigned_psychologist = self.previous
        self.child.save()
        self._draft_as(self.previous)
        self.child.assigned_psychologist = self.psy
        self.child.save()

        self.assertEqual(self._latest(self.psy).status_code, 404)

    def test_a_psychologist_still_gets_their_own_brief_back(self):
        self._draft_as(self.psy)
        res = self._latest(self.psy)
        self.assertEqual(res.status_code, 200)
        self.assertIn("MY NOTE", res.data["draft"])
        self.assertNotIn("PREVIOUS PSYCHOLOGIST'S NOTE", res.data["draft"])

    def test_prefetch_drafts_the_psychologists_own_even_if_someone_else_briefed_today(self):
        self._draft_as(self.admin)
        start = timezone.make_aware(datetime.combine(timezone.localdate(), time(12, 0)))
        Appointment.objects.create(child=self.child, psychologist=self.psy, start=start,
                                   status=Appointment.SCHEDULED)
        self.client.force_authenticate(self.psy)
        with patch.object(views, "_start_prefetch_thread") as spawn:
            res = self.client.post("/api/assistant/prefetch-briefs/")
        self.assertEqual(res.data["queued"], [self.child.id])
        spawn.assert_called_once()


    def test_a_brief_drafted_before_history_was_hidden_is_not_served_after(self):
        # Review of the first version: keyed by user, a psychologist's own
        # brief drafted while history was carried was still served after the
        # ISA hid it - the previous psychologist's note included.
        self.child.assignee_sees_history = True
        self.child.save()
        self.assertIn("PREVIOUS PSYCHOLOGIST'S NOTE", self._draft_as(self.psy).data["draft"])
        self.child.assignee_sees_history = False
        self.child.save()

        self.assertEqual(self._latest(self.psy).status_code, 404)
        # Nor does prefetch count the stale one as done.
        start = timezone.make_aware(datetime.combine(timezone.localdate(), time(12, 0)))
        Appointment.objects.create(child=self.child, psychologist=self.psy, start=start,
                                   status=Appointment.SCHEDULED)
        with patch.object(views, "_start_prefetch_thread"):
            res = self.client.post("/api/assistant/prefetch-briefs/")
        self.assertEqual(res.data["queued"], [self.child.id])
        # Drafted again, it is the psychologist's own notes only, and served.
        self.assertNotIn("PREVIOUS PSYCHOLOGIST'S NOTE", self._draft_as(self.psy).data["draft"])
        self.assertEqual(self._latest(self.psy).status_code, 200)


class SummaryIsAWriteToTheDocumentTest(RoleFixture):
    """Drafting a summary replaces what is in `ai_summary`, and the screen
    warns that a confirmed one "cannot be recovered". Confirming one saves it
    as the psychologist's own clinical text. Both are writes, so both follow
    the document's own write rule - not the read rule."""

    CONFIRMED = "The psychologist's confirmed summary."

    def setUp(self):
        super().setUp()
        self.child.assignee_sees_history = True
        self.child.save()
        self.report = PsychologicalReport.objects.create(
            child=self.child, author=self.psy, extracted_text="Report text.",
            ai_summary=self.CONFIRMED, ai_summary_confirmed=True)
        self.referral = CaseReferral.objects.create(
            child=self.child, uploaded_by=self.sw, extracted_text="Referral text.",
            ai_summary="The social worker's confirmed summary.",
            ai_summary_confirmed=True)

    def _summarize(self, user, kind, doc):
        self.client.force_authenticate(user)
        with patch.object(services.OllamaClient, "generate", return_value="New draft."):
            return self.client.post(f"/api/assistant/summarize-{kind}/{doc.id}/")

    def _confirm(self, user, path, doc, text="Words put in their mouth."):
        self.client.force_authenticate(user)
        return self.client.post(f"/api/assistant/{path}/{doc.id}/", {"text": text},
                                format="json")

    def test_a_social_worker_cannot_replace_a_psychologists_confirmed_summary(self):
        res = self._summarize(self.sw, "report", self.report)
        self.assertEqual(res.status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.ai_summary, self.CONFIRMED)
        self.assertTrue(self.report.ai_summary_confirmed)
        self.assertFalse(AssistantJob.objects.exists())

    def test_a_social_worker_cannot_confirm_text_onto_a_psychologists_report(self):
        res = self._confirm(self.sw, "confirm-summary", self.report)
        self.assertEqual(res.status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.ai_summary, self.CONFIRMED)

    def test_a_psychologist_cannot_replace_a_referrals_confirmed_summary(self):
        res = self._summarize(self.psy, "case-referral", self.referral)
        self.assertEqual(res.status_code, 403)
        self.referral.refresh_from_db()
        self.assertTrue(self.referral.ai_summary_confirmed)

    def test_a_psychologist_cannot_confirm_text_onto_a_referral(self):
        res = self._confirm(self.psy, "confirm-case-referral-summary", self.referral)
        self.assertEqual(res.status_code, 403)

    def test_the_documents_own_writers_still_can(self):
        self.assertEqual(self._summarize(self.psy, "report", self.report).status_code, 200)
        self.assertEqual(self._summarize(self.sw, "case-referral", self.referral).status_code, 200)
        self.assertEqual(self._summarize(self.admin, "report", self.report).status_code, 200)
        self.assertEqual(self._summarize(self.admin, "case-referral", self.referral).status_code, 200)
        self.assertEqual(self._confirm(self.psy, "confirm-summary", self.report).status_code, 200)
        self.assertEqual(self._confirm(
            self.sw, "confirm-case-referral-summary", self.referral).status_code, 200)

    def test_confirming_honours_the_carry_history_control_too(self):
        """Summarising already refused a report the psychologist did not
        author when history is hidden; confirming did not, so they could
        overwrite a summary on a report they are not allowed to open."""
        self.child.assignee_sees_history = False
        self.child.save()
        theirs = PsychologicalReport.objects.create(
            child=self.child, author=self.previous, extracted_text="Earlier report.",
            ai_summary="Earlier confirmed summary.", ai_summary_confirmed=True)
        res = self._confirm(self.psy, "confirm-summary", theirs)
        self.assertEqual(res.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.ai_summary, "Earlier confirmed summary.")


    def _summaries_seen_by(self, user):
        self.client.force_authenticate(user)
        chart = self.client.get(f"/api/reports/child/{self.child.id}/").data
        return {
            "report": self.client.get(
                f"/api/report-files/?child={self.child.id}").data[0]["ai_summary"],
            "referral": self.client.get(
                f"/api/case-referrals/?child={self.child.id}").data[0]["ai_summary"],
            "chart report": chart["reports"][0]["ai_summary"],
            "chart referral": chart["case_referrals"][0]["ai_summary"],
        }

    def test_an_unconfirmed_draft_goes_only_to_those_who_may_confirm_it(self):
        # Review of the first version: the screen showed a draft only to its
        # writers, but every reader's API response carried it.
        PsychologicalReport.objects.filter(pk=self.report.pk).update(
            ai_summary="REPORT DRAFT", ai_summary_confirmed=False)
        CaseReferral.objects.filter(pk=self.referral.pk).update(
            ai_summary="REFERRAL DRAFT", ai_summary_confirmed=False)
        expected = {
            self.sw: ("REFERRAL DRAFT", None),
            self.psy: (None, "REPORT DRAFT"),
            self.admin: ("REFERRAL DRAFT", "REPORT DRAFT"),
        }
        for user, (referral, report) in expected.items():
            seen = self._summaries_seen_by(user)
            with self.subTest(user=user.email):
                self.assertEqual(seen["report"], report)
                self.assertEqual(seen["chart report"], report)
                self.assertEqual(seen["referral"], referral)
                self.assertEqual(seen["chart referral"], referral)

    def test_a_confirmed_summary_is_for_every_reader(self):
        for user in (self.sw, self.psy, self.admin):
            seen = self._summaries_seen_by(user)
            with self.subTest(user=user.email):
                self.assertEqual(seen["report"], self.CONFIRMED)
                self.assertEqual(seen["chart referral"],
                                 "The social worker's confirmed summary.")


@override_settings(**HOSTED)
class OnlyTheChatbotIsHostedTest(RoleFixture):
    """With the hosted flag on, nothing but the chatbot's question may reach
    the hosted model. The drafting features refuse instead - they are prose
    over case text, and none of them was measured on that model."""

    def setUp(self):
        super().setUp()
        self.child.assignee_sees_history = True
        self.child.save()
        self.hosted = patch.object(services.OpenAICompatibleClient, "generate",
                                   return_value="sent off the machine")
        self.sent = self.hosted.start()
        self.addCleanup(self.hosted.stop)

    def test_a_brief_is_refused_rather_than_sent(self):
        self.client.force_authenticate(self.psy)
        res = self.client.post(f"/api/assistant/brief/child/{self.child.id}/")
        self.assertEqual(res.status_code, 503)
        self.assertIn("chatbot", res.data["detail"])
        self.sent.assert_not_called()

    def test_a_report_summary_is_refused_rather_than_sent(self):
        report = PsychologicalReport.objects.create(
            child=self.child, author=self.psy, extracted_text="Whole report.")
        self.client.force_authenticate(self.psy)
        res = self.client.post(f"/api/assistant/summarize-report/{report.id}/")
        self.assertEqual(res.status_code, 503)
        self.sent.assert_not_called()

    def test_remark_polish_is_refused_rather_than_sent(self):
        self.client.force_authenticate(self.psy)
        res = self.client.post("/api/assistant/polish-remark/",
                               {"text": "Umiiyak si bata."}, format="json")
        self.assertEqual(res.status_code, 503)
        self.sent.assert_not_called()

    def test_a_childs_own_answers_are_not_sent(self):
        from clinical.self_report_model_check import run_model_check
        from clinical.models import AgencyFormTemplate
        template = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="Self-report")
        invite = OpinionnaireInvite.objects.create(
            child=self.child, template=template, created_by=self.sw,
            answers={"Who do you talk to when you are sad?": "Nobody"},
            expires_at=timezone.now())
        run_model_check(invite.pk)
        self.sent.assert_not_called()

    def test_the_chatbot_still_uses_the_hosted_model(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OpenAICompatibleClient, "choose_tool",
                          return_value=("list_care_gaps", {})) as chose:
            res = self.client.post("/api/assistant/ask/", {"question": "Who needs follow-up?"},
                                   format="json")
        self.assertEqual(res.status_code, 200)
        chose.assert_called_once()
