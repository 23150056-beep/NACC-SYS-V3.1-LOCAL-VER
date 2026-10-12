"""What to do next: a social worker's task list on the case brief.

Plain rules over the brief's own facts (assistant/brief_facts.py next_steps) -
no model. They replaced a written brief the model drafted, which invented
dates and relationships a social worker could act on, so what is held here is
that every sentence is made by a rule and nothing else:

* each rule fires and does not fire at its edge, with a pinned clock;
* the steps come in a fixed order, only to the record's own social worker;
* a step agrees with the care gap that says the same thing (seven days, not
  "about seven");
* and no step ever carries a custodian's name or number, a case note, a
  child's own words or a line of the case study.

Nothing here calls a model, and nothing reads the wall clock.
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from assistant import brief_facts
from assistant.brief_facts import next_steps
from assistant.models import AssistantJob
from assistant.tests.test_brief_facts import FactsFixture
from case_study.completeness import missing_sections
from case_study.models import CaseStudy, CaseStudySection
from children import custodian
from children.models import AssignmentRequest, Child
from clinical import care_gaps
from clinical.models import (AgencyFormTemplate, CaseReferral, ConsentRecord,
                             OpinionnaireInvite, ProblemEntry, RemarkNote)
from scheduling.models import Appointment

# Thursday 8 October 2026, 10:00 in Manila.
NOW = timezone.make_aware(datetime(2026, 10, 8, 10, 0))
CHILD = SimpleNamespace(id=7, status=Child.ACTIVE)

ASSIGNED = {"state": "assigned", "name": "Mila Bulan"}
REFERRAL = {"count": 1, "latest_uploaded_on": "2026-10-03", "summary": None,
            "summary_confirmed": False}
SIGNED = {"status": "signed", "date": "2026-09-12"}
SESSION = {"start": "2026-10-14T09:30:00+08:00", "purpose": "Session"}


def facts(**changes):
    """The facts of a child with nothing left to do, then `changes`."""
    base = {
        "kind": "case", "case_referral": REFERRAL, "psychologist": ASSIGNED,
        "consent": SIGNED, "custodian_texts": None, "survey": None,
        "next_session": SESSION, "unreviewed_self_reports": 0,
        "case_study": {"state": "final"},
    }
    base.update(changes)
    return base


def steps(**changes):
    return next_steps(facts(**changes), CHILD)


def keys(**changes):
    return [s["key"] for s in steps(**changes)]


class EveryRuleTest(SimpleTestCase):
    """next_steps() on hand-made facts: one rule at a time."""

    def test_a_child_with_nothing_left_has_no_steps(self):
        self.assertEqual([], steps())

    def test_a_step_is_a_key_one_sentence_and_a_link_or_none(self):
        every = next_steps(facts(
            case_referral=None, psychologist={"state": "none"}, consent=None,
            next_session=None, custodian_texts="No contact number - texts off",
            survey={"state": "sent", "date": "2026-09-30", "elapsed_days": 8},
            unreviewed_self_reports=2, case_study={"state": "none"}), CHILD)
        self.assertTrue(every)
        for step in every:
            with self.subTest(step["key"]):
                self.assertEqual({"key", "text", "link"}, set(step))
                self.assertTrue(step["text"].endswith("."))
                self.assertTrue(step["link"] is None or step["link"].startswith("/"))

    def test_the_input_is_not_changed_and_the_same_facts_give_the_same_steps(self):
        given = facts(case_referral=None, unreviewed_self_reports=1)
        before = json.dumps(given, sort_keys=True)
        self.assertEqual(next_steps(given, CHILD), next_steps(given, CHILD))
        self.assertEqual(before, json.dumps(given, sort_keys=True))

    def test_a_closed_case_has_nothing_to_do(self):
        closed = SimpleNamespace(id=7, status=Child.INACTIVE)
        self.assertEqual([], next_steps(facts(case_referral=None), closed))

    # -- the case referral ---------------------------------------------------

    def test_no_case_referral(self):
        self.assertEqual(
            [{"key": "case_referral", "link": "/reports?upload=1&child=7",
              "text": "File the case referral. Sessions cannot be booked without it."}],
            steps(case_referral=None))

    def test_a_case_referral_on_file_is_no_step(self):
        self.assertNotIn("case_referral", keys())

    # -- the psychologist ----------------------------------------------------

    def test_nobody_asked(self):
        self.assertEqual(
            [{"key": "psychologist_none", "link": "/children?edit=7",
              "text": "Ask a psychologist to take the case."}],
            steps(psychologist={"state": "none"}))

    def test_an_assigned_psychologist_is_no_step(self):
        self.assertEqual([], steps(psychologist=ASSIGNED))

    def _asked(self, elapsed, days_ago=None, name="Mila Bulan"):
        return {"state": "asked", "name": name, "elapsed_days": elapsed,
                "days_ago": elapsed if days_ago is None else days_ago}

    def test_asked_less_than_the_care_gap_threshold_is_waiting_not_a_task(self):
        threshold = care_gaps.ASSIGNMENT_ANSWER_DAYS
        self.assertEqual(7, threshold)
        for days in (0, 1, threshold - 1):
            with self.subTest(days=days):
                self.assertEqual([], steps(psychologist=self._asked(days)))

    def test_asked_the_threshold_or_more_is_a_step_naming_who_and_how_long(self):
        for days in (care_gaps.ASSIGNMENT_ANSWER_DAYS, 9, 30):
            with self.subTest(days=days):
                self.assertEqual(
                    [{"key": "psychologist_silent", "link": "/children?edit=7",
                      "text": f"No answer from Mila Bulan after {days} days. "
                              "Ask them, or ask someone else."}],
                    steps(psychologist=self._asked(days)))

    def test_the_days_said_are_the_days_the_facts_row_says(self):
        # Asked at 11 PM seven calendar days ago is 6 days 23 hours: not yet a
        # step. Asked 7 days and 1 hour ago on the 8th calendar day is one, and
        # it says 8 days because the row above it says "asked 8 days ago".
        self.assertEqual([], steps(psychologist=self._asked(6, days_ago=7)))
        said = steps(psychologist=self._asked(7, days_ago=8))
        self.assertIn("after 8 days", said[0]["text"])

    def test_a_psychologist_with_no_name_is_still_a_sentence(self):
        said = steps(psychologist=self._asked(8, name=""))
        self.assertTrue(said[0]["text"].startswith("No answer from the psychologist "))

    def test_declined_gives_the_reason(self):
        self.assertEqual(
            [{"key": "psychologist_declined", "link": "/children?edit=7",
              "text": "Mila Bulan declined: Caseload is full this month. "
                      "Ask another psychologist."}],
            steps(psychologist={"state": "declined", "name": "Mila Bulan",
                                "reason": "Caseload is full this month."}))

    def test_a_reason_is_given_one_full_stop_and_one_line(self):
        for reason, said in (("Caseload is full", "declined: Caseload is full. Ask"),
                             ("Caseload is full.", "declined: Caseload is full. Ask"),
                             ("Is it full?", "declined: Is it full? Ask"),
                             ("Too\n  many   cases.", "declined: Too many cases. Ask")):
            with self.subTest(reason=reason):
                got = steps(psychologist={"state": "declined", "name": "M", "reason": reason})
                self.assertIn(said, got[0]["text"])

    def test_a_decline_with_no_reason_does_not_leave_a_colon(self):
        got = steps(psychologist={"state": "declined", "name": "Mila Bulan", "reason": ""})
        self.assertEqual("Mila Bulan declined. Ask another psychologist.", got[0]["text"])

    def test_a_long_reason_is_cut(self):
        got = steps(psychologist={"state": "declined", "name": "M", "reason": "Full. " * 100})
        self.assertLess(len(got[0]["text"]), 300)
        self.assertIn("…", got[0]["text"])

    # -- consent -------------------------------------------------------------

    def test_no_consent_on_file_is_asked_of_the_psychologist_with_no_link(self):
        self.assertEqual(
            [{"key": "consent", "link": None,
              "text": "Ask the psychologist to record the signed consent."}],
            steps(consent=None))

    def test_a_consent_not_signed_is_a_step_and_a_signed_one_is_not(self):
        for status, expected in (("pending", ["consent"]), ("declined", ["consent"]),
                                 ("signed", [])):
            with self.subTest(status=status):
                self.assertEqual(expected, keys(consent={"status": status, "date": "2026-09-12"}))

    # -- booking -------------------------------------------------------------

    def test_book_when_there_is_a_psychologist_a_referral_and_nothing_booked(self):
        self.assertEqual(
            [{"key": "book_session", "link": "/schedule?book=1&child=7",
              "text": "Book the next session."}],
            steps(next_session=None))

    def test_a_session_booked_is_no_step(self):
        self.assertNotIn("book_session", keys(next_session=SESSION))

    def test_booking_waits_for_what_it_needs(self):
        # Without a referral no session can be booked, and without a psychologist
        # there is nobody to book with: the step before it says so instead.
        self.assertNotIn("book_session", keys(next_session=None, case_referral=None))
        for state in ({"state": "none"}, {"state": "asked", "name": "M", "days_ago": 1,
                                          "elapsed_days": 1},
                      {"state": "declined", "name": "M", "reason": ""}):
            with self.subTest(state=state["state"]):
                self.assertNotIn("book_session", keys(next_session=None, psychologist=state))

    # -- the custodian's texts -----------------------------------------------

    def test_texts_off_say_why_once_and_send_the_social_worker_to_the_record(self):
        for sentence, reason in (
                ("No contact number - texts off", "no contact number"),
                ("No consent recorded - texts off", "no consent recorded"),
                ("Number not confirmed - texts off", "number not confirmed")):
            with self.subTest(sentence=sentence):
                self.assertEqual(
                    [{"key": "custodian_texts", "link": "/children?edit=7",
                      "text": f"Texts to the custodian are off: {reason}. "
                              "Fix it on the record."}],
                    steps(custodian_texts=sentence))

    def test_texts_on_or_not_asked_for_this_case_type_is_no_step(self):
        self.assertEqual([], steps(custodian_texts=custodian.TEXTS_ON))
        self.assertEqual([], steps(custodian_texts=None))

    def test_the_sentences_the_record_gives_are_all_understood(self):
        # Whatever custodian.status_of() says when texts are off must make a
        # step, and "on" must not: if a new sentence is added there, this fails.
        record = SimpleNamespace(custodian_contact="", custodian_sms_consent=False,
                                 custodian_contact_verified_at=None)
        off = [custodian.status_of(record)]
        record.custodian_contact = "+639171234567"
        off.append(custodian.status_of(record))
        record.custodian_sms_consent = True
        off.append(custodian.status_of(record))
        record.custodian_contact_verified_at = NOW
        on = custodian.status_of(record)
        for sentence in off:
            with self.subTest(sentence=sentence):
                self.assertTrue(sentence.endswith(custodian.TEXTS_OFF_ENDING))
                self.assertEqual(["custodian_texts"], keys(custodian_texts=sentence))
        self.assertEqual(custodian.TEXTS_ON, on)
        self.assertEqual([], keys(custodian_texts=on))

    # -- the survey ----------------------------------------------------------

    def _survey(self, state, elapsed):
        return {"state": state, "date": "2026-09-30", "elapsed_days": elapsed}

    def test_a_survey_out_less_than_the_care_gap_threshold_is_not_yet_a_step(self):
        threshold = care_gaps.SURVEY_UNANSWERED_DAYS
        self.assertEqual(7, threshold)
        for days in (0, threshold - 1):
            with self.subTest(days=days):
                self.assertEqual([], steps(survey=self._survey("sent", days)))

    def test_a_survey_out_the_threshold_or_more_is_a_step_for_sent_and_expired(self):
        for state in ("sent", "expired"):
            for days in (care_gaps.SURVEY_UNANSWERED_DAYS, 12):
                with self.subTest(state=state, days=days):
                    self.assertEqual(
                        [{"key": "survey_unanswered", "link": "/report/child/7?tab=voice",
                          "text": f"The survey sent {days} days ago has no answer yet. "
                                  "Follow it up or send a new link."}],
                        steps(survey=self._survey(state, days)))

    def test_an_answered_survey_or_none_is_no_step(self):
        self.assertEqual([], steps(survey={"state": "answered", "date": "2026-09-01"}))
        self.assertEqual([], steps(survey=None))

    # -- the child's own answers ---------------------------------------------

    def test_answers_waiting_are_counted_never_quoted(self):
        for n, said in ((1, "Read the 1 survey answer waiting for review."),
                        (3, "Read the 3 survey answers waiting for review.")):
            with self.subTest(n=n):
                self.assertEqual(
                    [{"key": "self_reports", "text": said,
                      "link": "/report/child/7?tab=overview"}],
                    steps(unreviewed_self_reports=n))
        self.assertEqual([], steps(unreviewed_self_reports=0))

    # -- the case study ------------------------------------------------------

    def test_a_case_study_not_started(self):
        self.assertEqual(
            [{"key": "case_study_start", "text": "Start the case study.",
              "link": "/report/child/7?tab=casestudy"}],
            steps(case_study={"state": "none"}))

    def test_a_draft_says_how_many_sections_are_left(self):
        for n, said in ((1, "Case study: 1 section still to complete."),
                        (12, "Case study: 12 sections still to complete.")):
            with self.subTest(n=n):
                self.assertEqual(
                    [{"key": "case_study_complete", "text": said,
                      "link": "/report/child/7?tab=casestudy"}],
                    steps(case_study={"state": "draft", "missing": n}))

    def test_a_finished_or_final_case_study_is_no_step(self):
        self.assertEqual([], steps(case_study={"state": "draft", "missing": 0}))
        self.assertEqual([], steps(case_study={"state": "final"}))

    def test_no_case_study_row_in_the_facts_is_no_step(self):
        given = facts()
        del given["case_study"]
        self.assertEqual([], next_steps(given, CHILD))

    # -- the order -----------------------------------------------------------

    def test_the_order_is_the_order_to_do_them_in(self):
        every = next_steps(facts(
            case_referral=None, psychologist={"state": "none"}, consent=None,
            next_session=None, custodian_texts="No contact number - texts off",
            survey={"state": "sent", "date": "2026-09-30", "elapsed_days": 8},
            unreviewed_self_reports=2, case_study={"state": "none"}), CHILD)
        self.assertEqual(
            ["case_referral", "psychologist_none", "consent", "custodian_texts",
             "survey_unanswered", "self_reports", "case_study_start"],
            [s["key"] for s in every])

    def test_the_order_when_a_psychologist_is_there_and_a_session_is_wanted(self):
        got = keys(case_referral=REFERRAL, consent=None, next_session=None,
                   custodian_texts="Number not confirmed - texts off",
                   survey={"state": "expired", "date": "2026-09-30", "elapsed_days": 9},
                   unreviewed_self_reports=1, case_study={"state": "draft", "missing": 4})
        self.assertEqual(
            ["consent", "book_session", "custodian_texts", "survey_unanswered",
             "self_reports", "case_study_complete"], got)

    def test_the_silent_and_declined_psychologist_steps_come_before_consent(self):
        silent = keys(consent=None, psychologist={
            "state": "asked", "name": "M", "days_ago": 8, "elapsed_days": 8})
        declined = keys(consent=None, psychologist={
            "state": "declined", "name": "M", "reason": "Full."})
        self.assertEqual(["psychologist_silent", "consent"], silent)
        self.assertEqual(["psychologist_declined", "consent"], declined)

    # -- the care-gap table --------------------------------------------------

    def test_every_step_the_rules_can_make_is_in_the_gap_table(self):
        # A step nobody mapped to the gap types it says would leave the same
        # sentence twice on the screen, or a gap dropped by accident.
        every_state = [
            facts(case_referral=None, psychologist={"state": "none"}, consent=None,
                  next_session=None, custodian_texts="No contact number - texts off",
                  survey={"state": "sent", "date": "2026-09-30", "elapsed_days": 8},
                  unreviewed_self_reports=2, case_study={"state": "none"}),
            facts(psychologist={"state": "asked", "name": "M", "days_ago": 8,
                                "elapsed_days": 8}),
            facts(psychologist={"state": "declined", "name": "M", "reason": ""}),
            facts(case_study={"state": "draft", "missing": 3}),
            facts(next_session=None),
        ]
        made = {s["key"] for state in every_state for s in next_steps(state, CHILD)}
        self.assertEqual(set(brief_facts._NEXT_STEP_GAPS), made)


# --- over a real record --------------------------------------------------------


class OverARecordTest(FactsFixture):
    """The same rules through the endpoint, on rows that are really there."""

    def setUp(self):
        super().setUp()
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        Child.objects.filter(pk=self.child.pk).update(case_type="Foster Care")
        self.child.refresh_from_db()

    def _steps(self, user=None, child=None):
        res = self._facts(user or self.sw, child)
        self.assertEqual(200, res.status_code, res.data)
        return res.data["next_steps"]

    def _keys(self, **kw):
        return [s["key"] for s in self._steps(**kw)]

    def _unassign(self):
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=None)
        self.child.refresh_from_db()

    def _referral(self):
        return CaseReferral.objects.create(
            child=self.child, uploaded_by=self.sw, original_filename="r.pdf",
            extracted_text="Text.")

    def _ask(self, ago):
        request = AssignmentRequest.objects.create(
            child=self.child, psychologist=self.previous, requested_by=self.sw)
        AssignmentRequest.objects.filter(pk=request.pk).update(created_at=NOW - ago)
        return request

    def _send_survey(self, ago, **fields):
        template = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="Self-report",
            fields=[{"label": "Q?"}])
        invite = OpinionnaireInvite.objects.create(
            child=self.child, template=template, created_by=self.sw,
            status=fields.pop("status", OpinionnaireInvite.PENDING),
            expires_at=fields.pop("expires_at", NOW + timedelta(days=30)), **fields)
        OpinionnaireInvite.objects.filter(pk=invite.pk).update(created_at=NOW - ago)
        return invite

    def _on_the_dashboard(self):
        self.client.force_authenticate(self.sw)
        return {a["type"] for a in
                self.client.get("/api/reports/dashboard/").data["care_gaps"]
                if a["child_id"] == self.child.id}

    # -- who gets them -------------------------------------------------------

    def test_the_record_holders_steps_are_ordered_and_linked_to_this_child(self):
        self._unassign()
        cid = self.child.id
        self.assertEqual(
            [("case_referral", f"/reports?upload=1&child={cid}"),
             ("psychologist_none", f"/children?edit={cid}"),
             ("consent", None),
             ("custodian_texts", f"/children?edit={cid}"),
             ("case_study_start", f"/report/child/{cid}?tab=casestudy")],
            [(s["key"], s["link"]) for s in self._steps()])

    def test_the_isa_and_a_psychologist_get_no_steps_and_no_case_study_row(self):
        for user in (self.admin, self.psy):
            res = self._facts(user)
            with self.subTest(user=user.email):
                self.assertEqual(200, res.status_code)
                self.assertNotIn("next_steps", res.data)
                self.assertNotIn("case_study", res.data)

    def test_another_social_workers_child_is_404_both_ways(self):
        self.assertEqual(404, self._facts(self.sw, self.other_child).status_code)
        self.assertEqual(404, self._facts(self.other_sw, self.child).status_code)

    def test_a_child_nobody_holds_is_not_found_by_a_social_worker(self):
        # A child nobody holds is the ISA's alone: no social worker reaches it.
        Child.objects.filter(pk=self.child.pk).update(social_worker=None)
        self.assertEqual(404, self._facts(self.sw).status_code)

    def test_no_model_is_involved_and_no_job_is_written(self):
        with patch("assistant.services.OllamaClient.generate") as generate:
            self._steps()
        generate.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())

    def test_a_closed_case_has_no_steps(self):
        Child.objects.filter(pk=self.child.pk).update(status=Child.INACTIVE)
        self.assertEqual([], self._steps())

    # -- each rule on real rows ----------------------------------------------

    def test_booking_follows_the_referral_and_the_psychologist(self):
        self.assertNotIn("book_session", self._keys())          # no referral yet
        self._referral()
        self.assertIn("book_session", self._keys())
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=NOW + timedelta(days=3),
                                   status=Appointment.SCHEDULED)
        self.assertNotIn("book_session", self._keys())
        # Only a SCHEDULED session still to come is a booking.
        Appointment.objects.all().update(status=Appointment.CANCELLED)
        self.assertIn("book_session", self._keys())
        Appointment.objects.all().update(status=Appointment.SCHEDULED,
                                         start=NOW - timedelta(days=1))
        self.assertIn("book_session", self._keys())
        self._unassign()
        self.assertNotIn("book_session", self._keys())

    def test_the_consent_step_goes_when_a_consent_is_signed(self):
        self.assertIn("consent", self._keys())
        ConsentRecord.objects.create(child=self.child, status=ConsentRecord.SIGNED,
                                     date=NOW.date())
        self.assertNotIn("consent", self._keys())

    def test_the_custodian_step_follows_the_texts(self):
        self.assertIn("custodian_texts", self._keys())
        self.assertEqual(
            "Texts to the custodian are off: no contact number. Fix it on the record.",
            next(s["text"] for s in self._steps() if s["key"] == "custodian_texts"))
        Child.objects.filter(pk=self.child.pk).update(
            custodian_contact="+639171234567", custodian_sms_consent=True,
            custodian_contact_verified_at=NOW)
        self.assertNotIn("custodian_texts", self._keys())
        # A case type that asks for no custodian has no such step.
        Child.objects.filter(pk=self.child.pk).update(
            case_type="", custodian_contact="", custodian_sms_consent=False,
            custodian_contact_verified_at=None)
        self.assertNotIn("custodian_texts", self._keys())

    def test_self_report_answers_waiting_are_counted(self):
        self._flag()
        self._flag()
        self.assertEqual(
            "Read the 2 survey answers waiting for review.",
            next(s["text"] for s in self._steps() if s["key"] == "self_reports"))
        self._flag(reviewed=True)
        self.assertEqual(
            "Read the 2 survey answers waiting for review.",
            next(s["text"] for s in self._steps() if s["key"] == "self_reports"))

    # -- the clock: a step agrees with the care gap that says the same -------

    def test_a_request_unanswered_for_seven_days_to_the_second_is_a_step_and_a_gap(self):
        self._unassign()
        self._ask(timedelta(days=7))
        self.assertIn("psychologist_silent", self._keys())
        self.assertIn("no_psychologist", self._on_the_dashboard())

    def test_a_request_six_days_twenty_three_hours_old_is_neither(self):
        self._unassign()
        # 11:00 on Thursday 1 October is seven CALENDAR days before NOW, and the
        # facts row says "asked 7 days ago" - but it is not seven days yet.
        self._ask(timedelta(days=6, hours=23))
        row = self._facts(self.sw).data["psychologist"]
        self.assertEqual(7, row["days_ago"])
        self.assertEqual(6, row["elapsed_days"])
        self.assertNotIn("psychologist_silent", self._keys())
        self.assertNotIn("no_psychologist", self._on_the_dashboard())

    def test_the_step_says_the_days_the_row_says(self):
        self._unassign()
        # 11 PM on Wednesday 30 September: seven days and eleven hours ago, so
        # the care gap counts seven, but the row above reads "asked 8 days ago"
        # and the step must not contradict the line beside it.
        self._ask(timedelta(days=7, hours=11))
        row = self._facts(self.sw).data["psychologist"]
        self.assertEqual((8, 7), (row["days_ago"], row["elapsed_days"]))
        step = next(s for s in self._steps() if s["key"] == "psychologist_silent")
        self.assertIn("after 8 days", step["text"])

    def test_a_survey_out_seven_days_to_the_second_is_a_step_and_a_gap(self):
        self._send_survey(timedelta(days=7))
        self.assertIn("survey_unanswered", self._keys())
        self.assertIn("survey_unanswered", self._on_the_dashboard())

    def test_a_survey_six_days_twenty_three_hours_old_is_neither(self):
        self._send_survey(timedelta(days=6, hours=23))
        self.assertNotIn("survey_unanswered", self._keys())
        self.assertNotIn("survey_unanswered", self._on_the_dashboard())

    def test_only_the_newest_survey_counts(self):
        self._send_survey(timedelta(days=20))
        self._send_survey(timedelta(days=1))
        self.assertNotIn("survey_unanswered", self._keys())

    def test_a_lapsed_survey_is_as_unanswered_as_one_still_out(self):
        self._send_survey(timedelta(days=9), expires_at=NOW - timedelta(days=2))
        step = next(s for s in self._steps() if s["key"] == "survey_unanswered")
        self.assertEqual("The survey sent 9 days ago has no answer yet. "
                         "Follow it up or send a new link.", step["text"])

    def test_an_answered_survey_is_no_step(self):
        self._send_survey(timedelta(days=9), status=OpinionnaireInvite.SUBMITTED,
                          submitted_at=NOW - timedelta(days=8))
        self.assertNotIn("survey_unanswered", self._keys())

    # -- the case study ------------------------------------------------------

    def test_no_case_study_then_a_draft_with_the_sections_the_screen_counts(self):
        self.assertEqual(
            "Start the case study.",
            next(s["text"] for s in self._steps() if s["key"] == "case_study_start"))
        study = CaseStudy.objects.create(child=self.child, created_by=self.sw)
        left = len(missing_sections(study))
        self.assertGreater(left, 1)
        step = next(s for s in self._steps() if s["key"] == "case_study_complete")
        self.assertEqual(f"Case study: {left} sections still to complete.", step["text"])
        # The very number the case study's own screen is sent.
        self.client.force_authenticate(self.sw)
        payload = self.client.get(f"/api/case-studies/child/{self.child.id}/").data
        self.assertEqual(left, len(payload["missing"]))

    def test_a_section_filled_in_is_one_fewer(self):
        study = CaseStudy.objects.create(child=self.child, created_by=self.sw)
        left = len(missing_sections(study))
        CaseStudySection.objects.create(
            case_study=study, key="a3_description", value="A description.")
        step = next(s for s in self._steps() if s["key"] == "case_study_complete")
        self.assertEqual(f"Case study: {left - 1} sections still to complete.",
                         step["text"])

    def test_a_final_case_study_is_no_step(self):
        CaseStudy.objects.create(child=self.child, created_by=self.sw,
                                 status=CaseStudy.FINAL)
        keys = self._keys()
        self.assertNotIn("case_study_start", keys)
        self.assertNotIn("case_study_complete", keys)

    # -- what must not be in a step ------------------------------------------

    SECRETS = {
        "custodian name": "Lola Cora Dizon",
        "custodian surname": "Dizon",
        "custodian number": "+639171234567",
        "custodian number, local form": "09171234567",
        "remark": "REMARK-SECRET-TEXT",
        "problem": "PROBLEM-SECRET-TEXT",
        "self-report answer": "SELFREPORT-SECRET-WORDS",
        "self-report question": "SELFREPORT-SECRET-QUESTION",
        "case study": "CASESTUDY-SECRET-TEXT",
        "referral text": "REFERRAL-RAW-SECRET-TEXT",
        "surname": "Santos",
    }

    def test_no_step_carries_a_name_a_number_or_anyones_words(self):
        # A child who has every one of these, and every step that can fire.
        RemarkNote.objects.create(child=self.child, author=self.psy,
                                  text=self.SECRETS["remark"])
        ProblemEntry.objects.create(child=self.child, logged_by=self.psy,
                                    description=self.SECRETS["problem"])
        self._flag(question=self.SECRETS["self-report question"],
                   answer=self.SECRETS["self-report answer"])
        study = CaseStudy.objects.create(child=self.child, created_by=self.sw)
        CaseStudySection.objects.create(
            case_study=study, key="a3_description", value=self.SECRETS["case study"])
        CaseReferral.objects.create(
            child=self.child, uploaded_by=self.sw, original_filename="r.pdf",
            extracted_text=self.SECRETS["referral text"])
        Child.objects.filter(pk=self.child.pk).update(
            custodian_name=self.SECRETS["custodian name"],
            custodian_contact=self.SECRETS["custodian number"],
            custodian_sms_consent=False)
        self._send_survey(timedelta(days=9))
        self.psy.first_name, self.psy.last_name = "Mila", "Bulan"
        self.psy.save()
        got = self._steps()
        # The control: a rich list, so the absence below is not an empty one.
        self.assertGreaterEqual(len(got), 5, got)
        body = json.dumps(got)
        for label, text in self.SECRETS.items():
            with self.subTest(label):
                self.assertNotIn(text, body)
