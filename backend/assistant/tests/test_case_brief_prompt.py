"""The prompt for the social worker's written case brief - not offered to anyone.

The written case brief was taken away on 12 Oct 2026: measured on the owner's PC
it invented dates, and drafts the checker passed invented relationships a social
worker could act on. Its two endpoints, the capability and the screen are gone
(the last class here holds that). What stays is the prompt builder and
`manage.py ai_eval --feature case_brief`, so it can be measured again, and so
these tests stay:

* what a model would be GIVEN - the prompt is built from the facts dict alone,
  in a fixed order behind a static prefix, with the arithmetic done, and it
  contains none of the texts the brief must never read.

Tests never call a model.
"""
from datetime import datetime, timedelta
from unittest.mock import patch

from django.utils import timezone

from assistant import prompts
from assistant.brief_facts import brief_facts
from assistant.models import AssistantJob
from assistant.tests.test_brief_facts import FactsFixture
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
        # A session held 40 days ago, none to come: the overdue follow-up is a
        # gap no row and no next step says, so the prompt still lists it.
        self._appointment(NOW - timedelta(days=40), status=Appointment.COMPLETED)
        text = self._facts_text()
        self.assertIn("Self-report answers waiting to be read: 1\n", text)
        self.assertIn("Open problems on file: 2\n", text)
        self.assertIn("Care gaps still open:\n- Last completed session over 30 days "
                      "ago.", text)

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

    def test_a_confirmed_summary_is_the_one_exception_and_only_for_the_latest(self):
        self._referral(summary="Referred after a fire at home.", confirmed=True, days_ago=1)
        prompt = self._prompt()
        self.assertIn("Referred after a fire at home.", prompt)
        self.assertNotIn(self.SECRETS["unconfirmed summary"], prompt)
        # The referral's own document text is still not in it.
        self.assertNotIn(self.SECRETS["referral document text"], prompt)


# --- the endpoints are gone ----------------------------------------------------


class TheWrittenCaseBriefIsGoneTest(CaseBriefFixture):
    """Nobody is offered a written case brief, and nothing a social worker (or
    anyone) sends reaches a model through the routes that used to. A model
    that is asked for nothing cannot invent anything."""

    def test_no_route_serves_it_to_any_role(self):
        users = (self.sw, self.admin, self.psy, self.other_sw)
        with patch("assistant.services.OllamaClient.generate") as generate:
            for user in users:
                self.client.force_authenticate(user)
                for method, url in (("post", CASE_BRIEF), ("get", LATEST)):
                    res = getattr(self.client, method)(url.format(self.child.id))
                    with self.subTest(user=user.email, url=url, method=method):
                        self.assertEqual(404, res.status_code)
        generate.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())

    def test_the_capability_is_not_advertised_to_anyone(self):
        for user in (self.sw, self.admin, self.psy):
            self.client.force_authenticate(user)
            res = self.client.get("/api/assistant/capabilities/")
            with self.subTest(user=user.email):
                self.assertEqual(200, res.status_code)
                self.assertNotIn("case_brief_writing", res.data)

    def test_a_case_brief_job_is_still_a_job_type_so_old_rows_stay_listed(self):
        self.assertIn("case_brief", dict(AssistantJob.TYPE_CHOICES))
