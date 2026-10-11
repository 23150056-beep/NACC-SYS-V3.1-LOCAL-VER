"""The written part of a social worker's case brief.

Owner's decision, 8 Oct 2026: a social worker gets the case facts (test_case_brief)
and now a short written part drafted from them by the local model. The ISA gets
the facts and never this; the psychologist keeps their own brief. Three things
are held here, none of which needs a model:

* what the model is GIVEN - the prompt is built from the facts dict alone, in a
  fixed order behind a static prefix, with the arithmetic done, and it contains
  none of the texts the brief must never read;
* who may ask, and when nothing is sent - the refusal is for who you are and
  comes before the gate, the child lookup's 404 is another worker's child, and
  a hosted deployment or a switched-off assistant writes no job;
* when a draft is still true - it is served again only for the same user, the
  same child, today, and the same prompt.

Tests never call a model: the client is patched, as the other brief tests do.
"""
import hashlib
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from assistant import prompts, services, views
from assistant.brief_facts import brief_facts
from assistant.models import AssistantJob, AssistantSetting
from assistant.services import CASE_BRIEF_DISCLAIMER
from assistant.tests.test_brief_facts import FactsFixture
from assistant.tests.test_role_access import HOSTED
from case_study.models import CaseStudy, CaseStudySection
from children.models import AssignmentRequest, Child
from clinical.models import (AgencyFormTemplate, CaseReferral, ConsentRecord,
                             OpinionnaireInvite, ProblemEntry, RemarkNote,
                             TreatmentPlan)
from scheduling.models import Appointment

CASE_BRIEF = "/api/assistant/case-brief/child/{}/"
LATEST = "/api/assistant/case-brief/child/{}/latest/"

# Thursday 8 October 2026, 10:00 in Manila. Nothing here reads the wall clock.
NOW = timezone.make_aware(datetime(2026, 10, 8, 10, 0))


class _Request:
    """The one attribute the scope helpers read."""

    def __init__(self, user):
        self.user = user


class CaseBriefFixture(FactsFixture):
    """The facts fixture plus helpers for the things a brief is made from."""

    def setUp(self):
        super().setUp()
        # The clock is pinned for everything here: a draft is "today's" by the
        # date, and the prompt is full of "n days ago".
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        Child.objects.filter(pk=self.child.pk).update(
            first_name="Maria", last_name="Santos",
            birth_date=datetime(2017, 3, 2).date(),
            case_type="Foster Care", case_category="Abandoned")
        self.child.refresh_from_db()

    # -- building -----------------------------------------------------------

    def _facts_for(self, user=None, child=None):
        return brief_facts(_Request(user or self.sw), child or self.child)

    def _prompt(self, user=None, child=None):
        child = child or self.child
        return prompts.build_case_brief_prompt(self._facts_for(user, child), child)

    def _facts_part(self, prompt):
        self.assertTrue(prompt.startswith(prompts.CASE_BRIEF_INSTRUCTIONS))
        return prompt[len(prompts.CASE_BRIEF_INSTRUCTIONS):]

    # -- the record ---------------------------------------------------------

    def _appointment(self, start, **fields):
        return Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=start, **fields)

    def _referral(self, summary=None, confirmed=False, days_ago=5, text="Raw text."):
        referral = CaseReferral.objects.create(
            child=self.child, uploaded_by=self.sw, original_filename="r.pdf",
            extracted_text=text, ai_summary=summary, ai_summary_confirmed=confirmed)
        CaseReferral.objects.filter(pk=referral.pk).update(
            created_at=NOW - timedelta(days=days_ago))
        return referral

    def _draft(self, user=None, child=None, reply="A written brief."):
        self.client.force_authenticate(user or self.sw)
        with patch.object(services.OllamaClient, "generate", return_value=reply):
            return self.client.post(CASE_BRIEF.format((child or self.child).id))

    def _latest(self, user=None, child=None):
        self.client.force_authenticate(user or self.sw)
        return self.client.get(LATEST.format((child or self.child).id))


# --- what the model is given -------------------------------------------------


class PromptShapeTest(CaseBriefFixture):
    def test_the_prompt_is_the_static_instructions_then_the_facts(self):
        prompt = self._prompt()
        self.assertTrue(prompt.startswith(prompts.CASE_BRIEF_INSTRUCTIONS))
        self.assertTrue(prompts.CASE_BRIEF_INSTRUCTIONS.endswith("FACTS:\n"))
        self.assertTrue(self._facts_part(prompt).startswith("First name: Maria\n"))

    def test_the_instructions_say_what_the_brief_is_and_is_not(self):
        text = prompts.CASE_BRIEF_INSTRUCTIONS
        for phrase in ("social worker", "home visit", "case conference",
                       "No referral summary yet.", "under 150 words",
                       "first name only", "not in the facts"):
            self.assertIn(phrase, text)
        # Written for a social worker: not a clinical brief.
        self.assertNotIn("psychologist", text.lower())
        self.assertNotIn("psychologist", prompts.CASE_BRIEF_SYSTEM.lower())

    def test_every_fact_is_one_labelled_line_in_a_fixed_order(self):
        Child.objects.filter(pk=self.child.pk).update(custodian_name="Lola Cora")
        self._referral(summary="Referred after a fire.", confirmed=True)
        ConsentRecord.objects.create(child=self.child, status=ConsentRecord.SIGNED,
                                     date=datetime(2026, 9, 12).date())
        self._appointment(NOW + timedelta(days=6), status=Appointment.SCHEDULED)
        self._appointment(NOW - timedelta(days=12), status=Appointment.COMPLETED)
        ProblemEntry.objects.create(child=self.child, description="x")
        with patch("django.utils.timezone.now", return_value=NOW):
            facts = self._facts_part(self._prompt())
        labels = ["First name:", "Age:", "Case type:", "Category:", "Case referral:",
                  "Psychologist:", "Consent:", "Custodian texts:", "Next session:",
                  "Last session held:", "Survey:",
                  "Self-report answers waiting to be read:", "Open problems on file:",
                  "Care gaps still open:", "Referral summary:"]
        at = [facts.index("\n" + label) if i else facts.index(label)
              for i, label in enumerate(labels)]
        self.assertEqual(at, sorted(at), facts)

    def test_the_same_facts_give_the_same_prompt(self):
        with patch("django.utils.timezone.now", return_value=NOW):
            self.assertEqual(self._prompt(), self._prompt())

    def test_the_child_is_called_by_first_name_only(self):
        facts = self._facts_part(self._prompt())
        self.assertIn("First name: Maria", facts)
        self.assertNotIn("Santos", facts)

    def test_a_record_with_only_a_full_name_still_gets_a_first_name(self):
        Child.objects.filter(pk=self.child.pk).update(first_name="")
        self.child.refresh_from_db()
        facts = self._facts_part(self._prompt())
        self.assertIn("First name: Maria", facts)
        self.assertNotIn("Santos", facts)

    def test_age_is_told_not_guessed(self):
        with patch("django.utils.timezone.now", return_value=NOW):
            self.assertIn("Age: 9\n", self._prompt())
        Child.objects.filter(pk=self.child.pk).update(birth_date=None)
        self.child.refresh_from_db()
        self.assertIn("Age: unknown\n", self._prompt())

    def test_case_type_and_category_are_stated(self):
        facts = self._facts_part(self._prompt())
        self.assertIn("Case type: Foster Care\n", facts)
        self.assertIn("Category: Abandoned\n", facts)
        Child.objects.filter(pk=self.child.pk).update(case_type="", case_category="")
        self.child.refresh_from_db()
        facts = self._facts_part(self._prompt())
        self.assertIn("Case type: not recorded\n", facts)
        self.assertIn("Category: not recorded\n", facts)


class PromptArithmeticTest(CaseBriefFixture):
    """The model does no date arithmetic and reads no 24-hour clock: every date
    is in words, every "n days ago" is worked out here."""

    def _facts_text(self):
        with patch("django.utils.timezone.now", return_value=NOW):
            return self._facts_part(self._prompt())

    def test_a_next_session_is_a_date_in_words_a_12_hour_time_and_days_to_go(self):
        self._appointment(
            timezone.make_aware(datetime(2026, 10, 14, 9, 30)),
            status=Appointment.SCHEDULED, purpose=Appointment.FOLLOW_UP)
        self.assertIn(
            "Next session: Wednesday 14 October 2026 (in 6 days) at 9:30 AM, Follow-up.\n",
            self._facts_text())

    def test_an_afternoon_session_reads_pm_and_never_14_05(self):
        self._appointment(
            timezone.make_aware(datetime(2026, 10, 9, 14, 5)),
            status=Appointment.SCHEDULED)
        text = self._facts_text()
        self.assertIn("Friday 9 October 2026 (tomorrow) at 2:05 PM", text)
        self.assertNotIn("14:05", text)
        self.assertNotIn("09:30", text)

    def test_no_session_is_said_plainly(self):
        text = self._facts_text()
        self.assertIn("Next session: none booked.\n", text)
        self.assertIn("Last session held: none yet.\n", text)

    def test_the_last_session_is_days_ago_and_no_date(self):
        self._appointment(timezone.make_aware(datetime(2026, 9, 26, 10, 0)),
                          status=Appointment.COMPLETED)
        text = self._facts_text()
        self.assertIn("Last session held: 12 days ago.\n", text)
        self.assertNotIn("26 September", text)

    def test_a_referral_is_counted_and_dated_in_words(self):
        self._referral(days_ago=30)
        self._referral(days_ago=5)
        self.assertIn(
            "Case referral: 2 on file, the latest filed Saturday 3 October 2026 "
            "(5 days ago).\n", self._facts_text())

    def test_no_referral_is_said_plainly(self):
        self.assertIn("Case referral: none on file.\n", self._facts_text())

    def test_consent_is_its_status_and_date(self):
        ConsentRecord.objects.create(child=self.child, status=ConsentRecord.SIGNED,
                                     date=datetime(2026, 9, 12).date())
        self.assertIn("Consent: signed, dated Saturday 12 September 2026 (26 days ago).\n",
                      self._facts_text())
        ConsentRecord.objects.all().delete()
        self.assertIn("Consent: none on file.\n", self._facts_text())

    def test_the_survey_in_each_state(self):
        template = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="Self-report",
            fields=[{"label": "Q?"}])
        invite = OpinionnaireInvite.objects.create(
            child=self.child, template=template, created_by=self.sw,
            status=OpinionnaireInvite.PENDING, expires_at=NOW + timedelta(days=2))
        OpinionnaireInvite.objects.filter(pk=invite.pk).update(
            created_at=NOW - timedelta(days=6))
        self.assertIn("Survey: sent Friday 2 October 2026 (6 days ago), no answer yet.\n",
                      self._facts_text())
        OpinionnaireInvite.objects.filter(pk=invite.pk).update(
            expires_at=NOW - timedelta(days=1))
        self.assertIn("the link has expired unanswered.\n", self._facts_text())
        OpinionnaireInvite.objects.filter(pk=invite.pk).update(
            status=OpinionnaireInvite.SUBMITTED, submitted_at=NOW - timedelta(days=2),
            answers={"Q?": "Okay lang."})
        self.assertIn("Survey: answered Tuesday 6 October 2026 (2 days ago).\n",
                      self._facts_text())

    def test_the_four_states_of_the_psychologist(self):
        self.psy.first_name, self.psy.last_name = "Mila", "Bulan"
        self.psy.save()
        self.assertIn("Psychologist: assigned.\n", self._facts_text())

        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=None)
        self.child.refresh_from_db()
        self.assertIn("Psychologist: none yet, and nobody has been asked.\n",
                      self._facts_text())

        with patch("django.utils.timezone.now", return_value=NOW - timedelta(days=3)):
            asked = AssignmentRequest.objects.create(
                child=self.child, psychologist=self.previous, requested_by=self.sw)
        self.assertIn("Psychologist: asked 3 days ago, no answer yet.\n",
                      self._facts_text())

        AssignmentRequest.objects.filter(pk=asked.pk).update(
            status=AssignmentRequest.DECLINED, reason="Caseload is full this month.")
        self.assertIn(
            "Psychologist: the one asked declined. Reason given: Caseload is full "
            "this month.\n", self._facts_text())

    def test_a_decline_reason_is_cut_to_200_characters(self):
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=None)
        self.child.refresh_from_db()
        AssignmentRequest.objects.create(
            child=self.child, psychologist=self.previous, requested_by=self.sw,
            status=AssignmentRequest.DECLINED, reason="Full. " * 100)
        line = next(ln for ln in self._facts_text().splitlines()
                    if ln.startswith("Psychologist:"))
        reason = line.split("Reason given: ")[1]
        self.assertLessEqual(len(reason), prompts.DECLINE_REASON_CHARS + len(" [...]."))

    def test_the_psychologists_name_is_not_in_it(self):
        # Data minimisation: "waiting on the psychologist" needs no name, and a
        # name is one more thing a small model can garble.
        self.psy.first_name, self.psy.last_name = "Mila", "Bulan"
        self.psy.save()
        self.assertNotIn("Mila", self._prompt())
        self.assertNotIn("Bulan", self._prompt())

    def test_custodian_texts_is_the_sentence_and_only_where_the_case_asks(self):
        self.assertIn("Custodian texts: No contact number - texts off\n", self._facts_text())
        # A record with no case type yet asks for no custodian.
        Child.objects.filter(pk=self.child.pk).update(case_type="")
        self.child.refresh_from_db()
        self.assertNotIn("Custodian texts", self._facts_text())

    def test_counts_and_care_gaps(self):
        ProblemEntry.objects.create(child=self.child, description="One.")
        ProblemEntry.objects.create(child=self.child, description="Two.")
        ProblemEntry.objects.create(child=self.child, description="Done.", resolved=True)
        self._flag()
        text = self._facts_text()
        self.assertIn("Self-report answers waiting to be read: 1\n", text)
        self.assertIn("Open problems on file: 2\n", text)
        self.assertIn("Care gaps still open:\n- Active case with no upcoming "
                      "appointment.", text)

    def test_no_gaps_is_said_plainly(self):
        self._appointment(NOW + timedelta(days=2), status=Appointment.SCHEDULED)
        self._referral()
        ConsentRecord.objects.create(child=self.child, status=ConsentRecord.SIGNED,
                                     date=NOW.date())
        self.assertTrue(self._facts_text().endswith("Care gaps still open: none."))


class ReferralSummaryTest(CaseBriefFixture):
    def test_a_confirmed_summary_is_the_last_thing_in_it(self):
        self._referral(summary="Referred after a fire at home.", confirmed=True)
        prompt = self._prompt()
        self.assertTrue(prompt.endswith("Referral summary:\nReferred after a fire at home."))

    def test_an_unconfirmed_summary_is_never_in_it(self):
        self._referral(summary="UNCONFIRMED MODEL DRAFT", confirmed=False)
        prompt = self._prompt()
        self.assertNotIn("UNCONFIRMED MODEL DRAFT", prompt)
        self.assertNotIn("Referral summary:", prompt)

    def test_no_summary_leaves_no_line_for_the_instruction_to_answer(self):
        self._referral()
        self.assertNotIn("Referral summary:", self._prompt())

    def test_only_the_latest_referrals_summary_counts(self):
        self._referral(summary="OLD CONFIRMED", confirmed=True, days_ago=9)
        self._referral(summary="NEW DRAFT", confirmed=False, days_ago=1)
        prompt = self._prompt()
        self.assertNotIn("OLD CONFIRMED", prompt)
        self.assertNotIn("NEW DRAFT", prompt)

    def test_a_flag_the_facts_do_not_carry_cannot_put_a_draft_back(self):
        # Defence in depth: a facts dict with text but no confirmation.
        facts = self._facts_for()
        facts["case_referral"] = {"count": 1, "latest_uploaded_on": "2026-10-03",
                                  "summary": "SOMEONE ELSE'S DRAFT",
                                  "summary_confirmed": False}
        self.assertNotIn("SOMEONE ELSE'S DRAFT",
                         prompts.build_case_brief_prompt(facts, self.child))

    def test_a_long_summary_is_trimmed_to_about_1500_characters(self):
        long = "\n".join(f"Line {i}: the child was seen at the barangay hall."
                         for i in range(200))
        self._referral(summary=long, confirmed=True)
        tail = self._prompt().split("Referral summary:\n", 1)[1]
        self.assertLessEqual(len(tail), prompts.REFERRAL_SUMMARY_CHARS + len(" [...]"))
        self.assertTrue(tail.endswith("[...]"))
        self.assertTrue(tail.startswith("Line 0:"))
        self.assertEqual(prompts.REFERRAL_SUMMARY_CHARS, 1500)

    def test_a_short_summary_is_not_marked_as_cut(self):
        self._referral(summary="Short.", confirmed=True)
        self.assertNotIn("[...]", self._prompt())


class WhatItMustNeverReadTest(CaseBriefFixture):
    """A child who HAS every kind of text the brief must not read, and the
    prompt for them carries none of it. Checked on the prompt itself and on
    what the endpoint actually hands the client."""

    SECRETS = {
        "remark": "REMARK-SECRET-TEXT",
        "self-report answer": "SELFREPORT-SECRET-WORDS",
        "self-report question": "SELFREPORT-SECRET-QUESTION",
        "case study": "CASESTUDY-SECRET-TEXT",
        "unconfirmed summary": "UNCONFIRMED-SECRET-SUMMARY",
        "referral document text": "REFERRAL-RAW-SECRET-TEXT",
        "custodian name": "Lola Cora Dizon",
        "custodian surname": "Dizon",
        "custodian number": "+639171234567",
        "custodian number, local form": "09171234567",
        "problem description": "PROBLEM-SECRET-TEXT",
        "treatment plan": "PLAN-SECRET-OBJECTIVES",
        "surname": "Santos",
    }

    def setUp(self):
        super().setUp()
        RemarkNote.objects.create(child=self.child, author=self.psy,
                                  text=self.SECRETS["remark"])
        self._flag(question=self.SECRETS["self-report question"],
                   answer=self.SECRETS["self-report answer"])
        study = CaseStudy.objects.create(child=self.child, created_by=self.sw)
        CaseStudySection.objects.create(
            case_study=study, key="a3_description", value=self.SECRETS["case study"])
        self._referral(summary=self.SECRETS["unconfirmed summary"], confirmed=False,
                       text=self.SECRETS["referral document text"])
        Child.objects.filter(pk=self.child.pk).update(
            case_type="Foster Care", custodian_name=self.SECRETS["custodian name"],
            custodian_contact=self.SECRETS["custodian number"],
            custodian_sms_consent=True, custodian_contact_verified_at=NOW)
        self.child.refresh_from_db()
        ProblemEntry.objects.create(child=self.child, logged_by=self.psy,
                                    description=self.SECRETS["problem description"])
        TreatmentPlan.objects.create(child=self.child, author=self.psy,
                                     objectives=self.SECRETS["treatment plan"])

    def test_the_fixture_really_has_all_of_it(self):
        # Without this the test below passes for a child who has none of it.
        self.assertTrue(self.child.remarks.exists())
        self.assertEqual(1, self.child.self_report_flags.count())
        self.assertTrue(CaseStudySection.objects.filter(case_study__child=self.child).exists())
        self.assertEqual(1, self.child.case_referrals.count())
        self.assertEqual(1, self.child.problems.count())
        self.assertEqual(1, self.child.treatment_plans.count())
        self.assertEqual(self.SECRETS["custodian name"], self.child.custodian_name)
        # And that the facts panel, which is made from the same record, sees it.
        facts = self._facts_for()
        self.assertEqual(1, facts["unreviewed_self_reports"])
        self.assertEqual(1, len(facts["open_problems"]))
        self.assertIsNotNone(facts["treatment_plan"])

    def test_none_of_it_is_in_the_prompt(self):
        prompt = self._prompt()
        for label, text in self.SECRETS.items():
            with self.subTest(label):
                self.assertNotIn(text, prompt)
        # What IS there, so the absence above is not an empty prompt.
        self.assertIn("Self-report answers waiting to be read: 1\n", prompt)
        self.assertIn("Open problems on file: 1\n", prompt)
        self.assertIn("Custodian texts: Texts on\n", prompt)

    def test_none_of_it_reaches_the_client(self):
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "generate",
                          return_value="Draft.") as generate:
            res = self.client.post(CASE_BRIEF.format(self.child.id))
        self.assertEqual(200, res.status_code)
        sent = json.dumps([generate.call_args.args, generate.call_args.kwargs])
        for label, text in self.SECRETS.items():
            with self.subTest(label):
                self.assertNotIn(text, sent)

    def test_none_of_it_is_in_the_job_row_either(self):
        self._draft()
        job = AssistantJob.objects.get(job_type="case_brief")
        stored = json.dumps([job.input_ref, job.output_text, job.error])
        for text in self.SECRETS.values():
            self.assertNotIn(text, stored)

    def test_a_confirmed_summary_is_the_one_exception_and_only_for_the_latest(self):
        self._referral(summary="Referred after a fire at home.", confirmed=True, days_ago=1)
        prompt = self._prompt()
        self.assertIn("Referred after a fire at home.", prompt)
        self.assertNotIn(self.SECRETS["unconfirmed summary"], prompt)
        # The referral's own document text is still not in it.
        self.assertNotIn(self.SECRETS["referral document text"], prompt)


# --- the door ----------------------------------------------------------------


class WhoMayAskTest(CaseBriefFixture):
    def test_a_social_workers_own_child_is_drafted(self):
        res = self._draft(reply="Why referred. What is booked. What to ask.")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual({"draft", "job_id", "generated_at", "disclaimer"}, set(res.data))
        self.assertEqual("Why referred. What is booked. What to ask.", res.data["draft"])

    def test_the_disclaimer_is_for_a_social_worker_not_a_psychologist(self):
        res = self._draft()
        self.assertEqual(CASE_BRIEF_DISCLAIMER, res.data["disclaimer"])
        self.assertEqual(
            "Drafted by the assistant from the facts above. Check it against them "
            "before relying on it.", res.data["disclaimer"])
        self.assertNotIn("psychologist", res.data["disclaimer"].lower())
        self.assertNotEqual(services.DISCLAIMER, res.data["disclaimer"])

    def test_another_social_workers_child_is_404_and_nothing_is_sent(self):
        with patch.object(services.OllamaClient, "generate") as generate:
            self.client.force_authenticate(self.sw)
            self.assertEqual(404, self.client.post(
                CASE_BRIEF.format(self.other_child.id)).status_code)
            self.assertEqual(404, self.client.get(
                LATEST.format(self.other_child.id)).status_code)
            self.client.force_authenticate(self.other_sw)
            self.assertEqual(404, self.client.post(
                CASE_BRIEF.format(self.child.id)).status_code)
        generate.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())

    def test_a_child_that_does_not_exist_is_404(self):
        self.client.force_authenticate(self.sw)
        self.assertEqual(404, self.client.post(CASE_BRIEF.format(99999)).status_code)

    def test_the_isa_and_the_psychologist_are_refused_with_one_sentence_and_no_job(self):
        with patch.object(services.OllamaClient, "generate") as generate:
            for user in (self.admin, self.psy):
                self.client.force_authenticate(user)
                for res in (self.client.post(CASE_BRIEF.format(self.child.id)),
                            self.client.get(LATEST.format(self.child.id))):
                    with self.subTest(user=user.email, path=res.request["PATH_INFO"]):
                        self.assertEqual(403, res.status_code)
                        self.assertEqual(views.CASE_BRIEF_REFUSED, res.data["detail"])
        generate.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())
        self.assertEqual("The written case brief is for the child's social worker.",
                         views.CASE_BRIEF_REFUSED)

    def test_the_refusal_comes_before_the_child_and_before_the_gate(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.client.force_authenticate(self.admin)
        # Switched off, a child that does not exist, another worker's child: the
        # answer is the same 403, so it says nothing about the record.
        for child_id in (self.child.id, self.other_child.id, 99999):
            self.assertEqual(403, self.client.post(CASE_BRIEF.format(child_id)).status_code)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(401, self.client.post(CASE_BRIEF.format(self.child.id)).status_code)
        self.assertEqual(401, self.client.get(LATEST.format(self.child.id)).status_code)

    def test_the_psychologists_written_brief_is_unchanged(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Brief."):
            res = self.client.post(f"/api/assistant/brief/child/{self.child.id}/")
        self.assertEqual(200, res.status_code)
        self.assertEqual(services.DISCLAIMER, res.data["disclaimer"])
        self.assertEqual(["brief"], list(AssistantJob.objects.values_list("job_type", flat=True)))
        # And the social worker's door to it is still shut.
        self.client.force_authenticate(self.sw)
        self.assertEqual(403, self.client.post(
            f"/api/assistant/brief/child/{self.child.id}/").status_code)


class WhenNothingIsSentTest(CaseBriefFixture):
    def test_the_switch_off_answers_503_and_writes_no_job(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "generate") as generate:
            res = self.client.post(CASE_BRIEF.format(self.child.id))
        self.assertEqual(503, res.status_code)
        self.assertEqual(services.SWITCHED_OFF, res.data["detail"])
        generate.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())

    @override_settings(**HOSTED)
    def test_a_hosted_deployment_answers_503_and_writes_no_job(self):
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "generate") as local, \
                patch.object(services.OpenAICompatibleClient, "generate") as hosted:
            res = self.client.post(CASE_BRIEF.format(self.child.id))
        self.assertEqual(503, res.status_code)
        self.assertEqual(services.HOSTED_DRAFTING_REFUSED, res.data["detail"])
        local.assert_not_called()
        hosted.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())

    @override_settings(**HOSTED)
    def test_a_hosted_deployment_still_refuses_the_wrong_role_first(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(403, self.client.post(CASE_BRIEF.format(self.child.id)).status_code)

    def test_a_runtime_that_is_down_is_503_and_audited_against_the_child(self):
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "generate",
                          side_effect=services.AIUnavailable("down")):
            res = self.client.post(CASE_BRIEF.format(self.child.id))
        self.assertEqual(503, res.status_code)
        job = AssistantJob.objects.get()
        self.assertFalse(job.ok)
        self.assertEqual(self.child.id, job.child_id)
        self.assertEqual(self.sw, job.created_by)
        # A failure is never served as today's brief.
        self.assertEqual(404, self._latest().status_code)

    def test_an_empty_draft_is_not_a_brief(self):
        res = self._draft(reply="   ")
        self.assertEqual(422, res.status_code)
        job = AssistantJob.objects.get()
        self.assertFalse(job.ok)
        self.assertEqual(404, self._latest().status_code)

    def test_the_draft_budget_is_the_other_drafting_features(self):
        self.assertEqual("assistant_draft", views.CaseBriefView.throttle_scope)


class WhatTheJobRecordsTest(CaseBriefFixture):
    def test_the_job_carries_the_child_the_user_the_type_and_the_prompt_sha(self):
        res = self._draft(reply="Draft.")
        job = AssistantJob.objects.get()
        self.assertEqual(res.data["job_id"], job.id)
        self.assertEqual("case_brief", job.job_type)
        self.assertEqual(self.child, job.child)
        self.assertEqual(self.sw, job.created_by)
        self.assertEqual(f"child:{self.child.id}", job.input_ref)
        self.assertTrue(job.ok)
        self.assertEqual("Draft.", job.output_text)

    def test_the_sha_is_the_hash_of_the_system_text_and_the_prompt_the_model_got(self):
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "generate",
                          return_value="Draft.") as generate:
            self.client.post(CASE_BRIEF.format(self.child.id))
        prompt = generate.call_args.args[0]
        system = generate.call_args.kwargs["system"]
        self.assertEqual(prompts.CASE_BRIEF_SYSTEM, system)
        job = AssistantJob.objects.get()
        self.assertEqual(64, len(job.prompt_sha))
        self.assertEqual(hashlib.sha256((system + prompt).encode("utf-8")).hexdigest(),
                         job.prompt_sha)

    def test_the_other_jobs_have_no_sha(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", return_value="Brief."):
            self.client.post(f"/api/assistant/brief/child/{self.child.id}/")
        self.assertEqual("", AssistantJob.objects.get().prompt_sha)

    def test_the_social_worker_can_say_whether_it_helped(self):
        res = self._draft()
        self.client.force_authenticate(self.sw)
        fb = self.client.post(f"/api/assistant/jobs/{res.data['job_id']}/feedback/",
                              {"outcome": "accepted"}, format="json")
        self.assertEqual(200, fb.status_code)
        self.assertEqual("accepted", AssistantJob.objects.get().outcome)
        # Not somebody else's.
        self.client.force_authenticate(self.other_sw)
        self.assertEqual(404, self.client.post(
            f"/api/assistant/jobs/{res.data['job_id']}/feedback/",
            {"outcome": "discarded"}, format="json").status_code)


class WhatTheClientIsAskedTest(CaseBriefFixture):
    def test_generate_gets_the_prompt_and_the_system_text_and_nothing_else(self):
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "generate",
                          return_value="Draft.") as generate:
            self.client.post(CASE_BRIEF.format(self.child.id))
        generate.assert_called_once()
        self.assertEqual(1, len(generate.call_args.args))
        self.assertEqual({"system"}, set(generate.call_args.kwargs))
        self.assertEqual(prompts.CASE_BRIEF_SYSTEM, generate.call_args.kwargs["system"])

    def test_the_request_to_the_runtime_has_no_options_block(self):
        # Each distinct option set makes Ollama evict and reload the model; an
        # explicit num_ctx measured 6x slower. Checked on the wire, not on a mock
        # of generate().
        sent = []

        class _Reply:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"response": "Draft."}'

        def fake_urlopen(req, timeout=None):
            sent.append(json.loads(req.data.decode()))
            return _Reply()

        self.client.force_authenticate(self.sw)
        with patch.object(services.urllib.request, "urlopen", fake_urlopen):
            res = self.client.post(CASE_BRIEF.format(self.child.id))
        self.assertEqual(200, res.status_code)
        self.assertEqual(1, len(sent))
        self.assertEqual({"model", "prompt", "stream", "system"}, set(sent[0]))
        self.assertIs(False, sent[0]["stream"])

    def test_every_call_starts_with_the_same_static_prefix(self):
        other = Child.objects.create(
            fullname="Ana Reyes", first_name="Ana", last_name="Reyes",
            social_worker=self.sw, case_type="Adoption")
        seen = []
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "generate",
                          return_value="Draft.") as generate:
            for child in (self.child, other, self.child):
                # An earlier next session each time, so the facts all differ.
                self._appointment(NOW + timedelta(days=5 - len(seen)),
                                  status=Appointment.SCHEDULED)
                self.client.post(CASE_BRIEF.format(child.id))
                seen.append((generate.call_args.args[0],
                             generate.call_args.kwargs["system"]))
        self.assertEqual(3, len(seen))
        for prompt, system in seen:
            self.assertTrue(prompt.startswith(prompts.CASE_BRIEF_INSTRUCTIONS))
            self.assertEqual(prompts.CASE_BRIEF_SYSTEM, system)
        # The prefix is literally the same string, and only what follows moves.
        facts = [prompt[len(prompts.CASE_BRIEF_INSTRUCTIONS):] for prompt, _ in seen]
        self.assertEqual(3, len(set(facts)))
        self.assertIn("First name: Ana\n", facts[1])


# --- when a draft is still true ----------------------------------------------


class LatestTest(CaseBriefFixture):
    def test_nothing_drafted_is_404_no_current_brief(self):
        res = self._latest()
        self.assertEqual(404, res.status_code)
        self.assertEqual("No current brief.", res.data["detail"])

    def test_todays_draft_is_served_again_as_it_was(self):
        drafted = self._draft(reply="The brief.")
        res = self._latest()
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(drafted.data["draft"], res.data["draft"])
        self.assertEqual(drafted.data["job_id"], res.data["job_id"])
        self.assertEqual(CASE_BRIEF_DISCLAIMER, res.data["disclaimer"])
        self.assertIn("generated_at", res.data)
        self.assertEqual(1, AssistantJob.objects.count())

    def test_the_newest_current_draft_wins(self):
        self._draft(reply="First.")
        second = self._draft(reply="Second.")
        self.assertEqual(second.data["job_id"], self._latest().data["job_id"])

    def test_reading_it_does_not_ask_the_model_or_write_a_job(self):
        self._draft()
        with patch.object(services.OllamaClient, "generate") as generate:
            self._latest()
            self._latest()
        generate.assert_not_called()
        self.assertEqual(1, AssistantJob.objects.count())

    def test_it_is_not_gated_so_this_mornings_stays_readable(self):
        self._draft()
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertEqual(200, self._latest().status_code)

    def test_it_is_this_users_only(self):
        # The same child, the same prompt, drafted by somebody else.
        self._draft()
        AssistantJob.objects.update(created_by=self.other_sw)
        self.assertEqual(404, self._latest().status_code)

    def test_it_is_this_childs_only(self):
        second = Child.objects.create(fullname="Ana Reyes", first_name="Ana",
                                      last_name="Reyes", social_worker=self.sw)
        self._draft(child=second)
        self.assertEqual(404, self._latest().status_code)
        self.assertEqual(200, self._latest(child=second).status_code)

    def test_yesterdays_is_not_todays(self):
        self._draft()
        AssistantJob.objects.update(created_at=timezone.now() - timedelta(days=1))
        self.assertEqual(404, self._latest().status_code)

    def test_a_failed_job_with_the_same_prompt_is_never_served(self):
        self._draft()
        AssistantJob.objects.update(ok=False)
        self.assertEqual(404, self._latest().status_code)

    def test_a_job_of_another_type_with_the_same_sha_is_not_a_case_brief(self):
        self._draft()
        AssistantJob.objects.update(job_type="brief")
        self.assertEqual(404, self._latest().status_code)

    def test_a_draft_with_no_sha_is_never_current(self):
        # Rows written before the column existed, or by anything else.
        self._draft()
        AssistantJob.objects.update(prompt_sha="")
        self.assertEqual(404, self._latest().status_code)

    def test_a_booking_since_makes_it_stale_and_a_new_draft_is_current(self):
        first = self._draft(reply="Before the booking.")
        self.assertEqual(200, self._latest().status_code)

        self._appointment(timezone.now() + timedelta(days=3),
                          status=Appointment.SCHEDULED)
        stale = self._latest()
        self.assertEqual(404, stale.status_code)
        self.assertEqual("No current brief.", stale.data["detail"])

        again = self._draft(reply="After the booking.")
        self.assertNotEqual(first.data["job_id"], again.data["job_id"])
        res = self._latest()
        self.assertEqual(200, res.status_code)
        self.assertEqual("After the booking.", res.data["draft"])

    def test_each_kind_of_change_to_the_facts_makes_it_stale(self):
        changes = {
            "a consent recorded": lambda: ConsentRecord.objects.create(
                child=self.child, status=ConsentRecord.SIGNED, date=timezone.localdate()),
            "a referral filed": lambda: CaseReferral.objects.create(
                child=self.child, uploaded_by=self.sw, original_filename="r.pdf"),
            "a problem logged": lambda: ProblemEntry.objects.create(
                child=self.child, description="New."),
            "a self-report flagged": self._flag,
            "the psychologist changing": lambda: Child.objects.filter(
                pk=self.child.pk).update(assigned_psychologist=None),
            "a request for a psychologist": lambda: AssignmentRequest.objects.create(
                child=self.child, psychologist=self.previous, requested_by=self.sw),
        }
        for label, change in changes.items():
            with self.subTest(label):
                self._draft()
                self.assertEqual(200, self._latest().status_code)
                change()
                self.assertEqual(404, self._latest().status_code)
                AssistantJob.objects.all().delete()

    def test_confirming_the_referral_summary_makes_it_stale(self):
        referral = self._referral(summary="Referred after a fire.", confirmed=False)
        self._draft()
        self.assertEqual(200, self._latest().status_code)
        CaseReferral.objects.filter(pk=referral.pk).update(ai_summary_confirmed=True)
        self.assertEqual(404, self._latest().status_code)

    def test_a_change_that_does_not_reach_the_prompt_leaves_it_current(self):
        self._draft()
        # Remarks, case study text and the custodian's name are not in the
        # prompt, so changing them cannot make a draft stale - or leak.
        RemarkNote.objects.create(child=self.child, author=self.psy, text="A new note.")
        Child.objects.filter(pk=self.child.pk).update(custodian_name="Somebody Else")
        self.assertEqual(200, self._latest().status_code)


# --- capabilities --------------------------------------------------------------


class CapabilitiesTest(CaseBriefFixture):
    def _caps(self, user):
        self.client.force_authenticate(user)
        res = self.client.get("/api/assistant/capabilities/")
        self.assertEqual(200, res.status_code)
        return res.data

    def test_only_a_social_worker_is_offered_the_written_brief(self):
        self.assertIs(True, self._caps(self.sw)["case_brief_writing"])
        self.assertIs(False, self._caps(self.admin)["case_brief_writing"])
        self.assertIs(False, self._caps(self.psy)["case_brief_writing"])

    @override_settings(**HOSTED)
    def test_nobody_is_offered_it_on_a_hosted_deployment(self):
        for user in (self.sw, self.admin, self.psy):
            with self.subTest(user=user.email):
                caps = self._caps(user)
                self.assertIs(False, caps["case_brief_writing"])
                self.assertIs(False, caps["drafting"])

    def test_it_follows_the_deployment_and_not_the_switch(self):
        # Like `drafting`: the switch is runtime state the endpoint answers 503
        # for, and a draft already written stays readable while it is off.
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertIs(True, self._caps(self.sw)["case_brief_writing"])

    def test_the_other_capabilities_are_unchanged(self):
        caps = self._caps(self.sw)
        self.assertEqual("case", caps["brief"])
        self.assertIs(True, caps["drafting"])
        self.assertIn("can_ask", caps)
        self.assertIn("examples", caps)
