"""The runtime guard on model text a social worker can still obtain
(assistant/guards.py), and the two places it stands: the census narrative and
the summary of a case referral.

The guard is the same string checks `ai_eval` is scored with, run on every
draft before it is shown. What is held here:

* `checked_draft` returns the text untouched when it states nothing its source
  does not, and refuses a name, a date or a number that is not in the source -
  and applies only the checks the table says for the job type;
* a refused draft is never shown and never stored: the answer is 422 with one
  plain sentence, the job is recorded not ok with what was found, and the
  document keeps whatever summary it had;
* the clean case still works, and the checks do not refuse the ordinary ways a
  model dresses the source (all-capitals forms, key names with underscores,
  a heading on the line below a sentence).

Tests never call a model: the client is patched, as the other assistant tests do.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import guards, prompts, services
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child
from clinical.models import CaseReferral, PsychologicalReport

User = get_user_model()

FIGURES = {"period": "monthly", "completed_pre_assessments": 18, "children_seen": 12,
           "pending_pre_assessments": 3, "case_type_Foster Care": 7,
           "case_type_Adoption": 5}
SOURCE = prompts.census_figures_text(FIGURES)


class CheckedDraftTest(SimpleTestCase):
    def refused(self, job_type, text, source):
        with self.assertRaises(guards.DraftRefused) as caught:
            guards.checked_draft(job_type, text, source)
        return caught.exception.reason

    # -- a clean draft ------------------------------------------------------

    def test_a_draft_that_states_only_the_source_is_returned_as_it_was(self):
        draft = ("In the monthly period 18 pre-assessments were completed and 12 "
                 "children were seen. 3 pre-assessments are still pending.\n\n"
                 "Of the active cases 7 are Foster Care and 5 are Adoption.")
        self.assertIs(draft, guards.checked_draft("census_narrative", draft, SOURCE))

    def test_a_draft_that_states_nothing_is_clean(self):
        self.assertEqual("Quiet month.", guards.checked_draft(
            "census_narrative", "Quiet month.", SOURCE))

    # -- names ----------------------------------------------------------------

    def test_a_name_the_source_never_had_is_refused(self):
        reason = self.refused("census_narrative",
                              "The caseload was reviewed by Villanueva this month.", SOURCE)
        self.assertIn("names", reason)
        self.assertIn("Villanueva", reason)

    def test_key_names_with_underscores_are_words_of_the_source(self):
        # "Foster" is only ever written inside "case_type_Foster Care".
        draft = "The agency holds several Foster Care and Adoption cases."
        self.assertEqual(draft, guards.checked_draft("census_narrative", draft, SOURCE))

    def test_a_name_in_capitals_in_the_source_is_not_invented_when_written_normally(self):
        source = "REASON FOR REFERRAL: JUAN DELA CRUZ was left with an aunt in SAN JUAN."
        draft = "The child, referred by Juan Dela Cruz's aunt, lives in San Juan."
        self.assertEqual(draft, guards.checked_draft("doc_intelligence", draft, source))

    def test_a_line_break_is_the_end_of_a_thought_for_the_name_check(self):
        # Without this a heading under a line that ended in a word is "a name
        # in the middle of a sentence".
        draft = "There were 12 children seen this month\nTotals are shown above."
        self.assertEqual(draft, guards.checked_draft("census_narrative", draft, SOURCE))

    def test_a_name_in_the_middle_of_a_line_is_still_seen_after_a_break(self):
        reason = self.refused(
            "census_narrative", "Seen this month\nThe case was closed by Villanueva.", SOURCE)
        self.assertIn("Villanueva", reason)

    # -- dates -----------------------------------------------------------------

    def test_a_date_the_source_never_had_is_refused(self):
        reason = self.refused("census_narrative",
                              "This was the busiest month since 14 March.", SOURCE)
        self.assertIn("dates", reason)
        self.assertIn("14 March", reason)

    def test_a_weekday_the_source_never_had_is_refused(self):
        self.assertIn("dates", self.refused(
            "census_narrative", "Sessions peaked on Tuesday.", SOURCE))

    def test_a_date_that_is_in_the_source_is_not(self):
        source = "Referral filed: 3 October 2026"
        self.assertEqual("Filed on October 3.", guards.checked_draft(
            "census_narrative", "Filed on October 3.", source))

    # -- numbers ---------------------------------------------------------------

    def test_a_number_the_source_never_had_is_refused(self):
        reason = self.refused(
            "census_narrative", "That is 40 more than last month.", SOURCE)
        self.assertIn("numbers", reason)
        self.assertIn("40", reason)

    def test_a_computed_figure_is_refused(self):
        # 18 + 12 is 30, which is nobody's figure.
        self.assertIn("30", self.refused(
            "census_narrative", "Together that is 30 children and assessments.", SOURCE))

    def test_what_was_found_is_all_named_once(self):
        reason = self.refused(
            "census_narrative", "Reviewed by Villanueva: 99 children on 14 March.", SOURCE)
        for label in ("names", "dates", "numbers"):
            self.assertIn(label, reason)

    # -- which checks, per job type ----------------------------------------------

    def test_a_summary_is_checked_for_names_only(self):
        source = "The child was seen by the barangay in March. Two siblings live with her."
        # A paraphrase of a date or a count is not an invention.
        draft = "Seen in 2026, about 3 weeks after, with a sibling in grade 4."
        self.assertEqual(draft, guards.checked_draft("doc_intelligence", draft, source))
        self.assertIn("Villanueva", self.refused(
            "doc_intelligence", "She was referred by Villanueva.", source))

    def test_the_table_says_what_it_says(self):
        self.assertEqual((guards.NAMES, guards.DATES, guards.NUMBERS),
                         guards.CHECKS["census_narrative"])
        self.assertEqual((guards.NAMES,), guards.CHECKS["doc_intelligence"])
        self.assertEqual((guards.NAMES, guards.DATES, guards.NUMBERS),
                         guards.CHECKS["self_report"])

    def test_a_job_type_nobody_decided_cannot_be_checked(self):
        with self.assertRaises(ValueError) as caught:
            guards.checked_draft("a_new_kind_of_draft", "Text.", "Source.")
        self.assertIn("STAFF_DRAFTING", str(caught.exception))

    def test_the_sentence_a_person_is_shown(self):
        self.assertEqual(
            "The assistant's draft mentioned something that is not in the source, "
            "so it is not shown. Try again, or write it yourself.",
            guards.REFUSAL_SENTENCE)
        # It says nothing of what was found: that is for the audit row.
        self.assertNotIn("Villanueva", guards.REFUSAL_SENTENCE)


class _Base(APITestCase):
    def setUp(self):
        roles = {n: Role.objects.create(role_name=n)
                 for n in (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)}
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=roles[Role.ADMINISTRATOR])
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=roles[Role.PSYCHOLOGIST])
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=roles[Role.STAFF])
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()

    def _model_says(self, text):
        return patch.object(services.OllamaClient, "generate", return_value=text)


class CensusNarrativeGuardTest(_Base):
    URL = "/api/assistant/census-narrative/"

    def _ask(self, user, text, figures=None):
        self.client.force_authenticate(user)
        with self._model_says(text):
            return self.client.post(self.URL, {"figures": figures or FIGURES}, format="json")

    def test_a_narrative_that_restates_the_figures_is_shown(self):
        for user in (self.sw, self.admin):
            with self.subTest(user=user.email):
                res = self._ask(user, "Eighteen were completed; 12 children were seen.")
                self.assertEqual(200, res.status_code, res.data)
                self.assertIn("12 children", res.data["draft"])
        self.assertEqual([True, True], [j.ok for j in AssistantJob.objects.all()])

    def test_an_invented_number_is_refused_with_one_sentence_and_nothing_else(self):
        for user in (self.sw, self.admin):
            with self.subTest(user=user.email):
                res = self._ask(user, "12 children were seen, a rise of 40 percent.")
                self.assertEqual(422, res.status_code)
                self.assertEqual({"detail": guards.REFUSAL_SENTENCE}, res.data)
                self.assertNotIn("40", str(res.data))

    def test_an_invented_name_and_an_invented_date_are_refused_too(self):
        self.assertEqual(422, self._ask(
            self.sw, "The figures were signed off by Villanueva.").status_code)
        self.assertEqual(422, self._ask(
            self.sw, "The busiest day was 14 March.").status_code)

    def test_the_job_is_recorded_not_ok_with_what_was_found(self):
        self._ask(self.sw, "12 children were seen, a rise of 40 percent.")
        job = AssistantJob.objects.get()
        self.assertEqual("census_narrative", job.job_type)
        self.assertFalse(job.ok)
        self.assertTrue(job.error.startswith("refused: "), job.error)
        self.assertIn("40", job.error)
        self.assertEqual(self.sw, job.created_by)

    def test_a_refusal_is_not_a_failure_of_the_runtime(self):
        res = self._ask(self.sw, "A rise of 40 percent.")
        self.assertEqual(422, res.status_code)        # not 503
        self.assertNotEqual(services.SWITCHED_OFF, res.data["detail"])

    def test_the_other_refusals_are_unchanged(self):
        self.client.force_authenticate(self.psy)
        self.assertEqual(403, self.client.post(
            self.URL, {"figures": FIGURES}, format="json").status_code)
        self.client.force_authenticate(self.sw)
        self.assertEqual(400, self.client.post(
            self.URL, {"figures": {}}, format="json").status_code)

    def test_the_figures_it_is_checked_against_are_the_ones_the_model_was_sent(self):
        with self._model_says("Narrative.") as generate:
            self.client.force_authenticate(self.sw)
            self.client.post(self.URL, {"figures": FIGURES}, format="json")
        prompt = generate.call_args.args[0]
        self.assertTrue(prompt.startswith(prompts.CENSUS_INSTRUCTIONS))
        self.assertEqual(SOURCE, prompt[len(prompts.CENSUS_INSTRUCTIONS):])
        # And the instructions' own digits are not among them.
        self.assertNotIn("Two short paragraphs", SOURCE)


class ReferralSummaryGuardTest(_Base):
    REFERRAL_TEXT = ("REASON FOR REFERRAL\nMaria was found at the barangay hall with her "
                     "aunt in San Juan. Her mother has left for work abroad.\n"
                     "RECOMMENDATIONS\nPlacement with the aunt is advised.")

    def setUp(self):
        super().setUp()
        self.child = Child.objects.create(
            fullname="Maria Santos", social_worker=self.sw, assigned_psychologist=self.psy)
        self.referral = CaseReferral.objects.create(
            child=self.child, uploaded_by=self.sw, original_filename="r.pdf",
            extracted_text=self.REFERRAL_TEXT,
            ai_summary="A summary a person confirmed.", ai_summary_confirmed=False)

    def _summarise(self, user, text, kind="case-referral", doc=None):
        self.client.force_authenticate(user)
        with self._model_says(text):
            return self.client.post(
                f"/api/assistant/summarize-{kind}/{(doc or self.referral).id}/")

    def test_a_summary_of_what_the_document_says_is_stored_and_shown(self):
        res = self._summarise(
            self.sw, "Maria was found at the barangay hall with her aunt in San Juan.")
        self.assertEqual(200, res.status_code, res.data)
        self.referral.refresh_from_db()
        self.assertEqual(res.data["draft"], self.referral.ai_summary)
        self.assertFalse(self.referral.ai_summary_confirmed)

    def test_a_summary_naming_someone_the_document_never_mentions_is_not_shown(self):
        res = self._summarise(
            self.sw, "Maria was found with her uncle Villanueva in San Juan.")
        self.assertEqual(422, res.status_code)
        self.assertEqual({"detail": guards.REFUSAL_SENTENCE}, res.data)

    def test_nothing_is_stored_on_the_document(self):
        self._summarise(self.sw, "Maria was found with her uncle Villanueva.")
        self.referral.refresh_from_db()
        self.assertEqual("A summary a person confirmed.", self.referral.ai_summary)
        self.assertFalse(self.referral.ai_summary_confirmed)

    def test_the_job_is_recorded_not_ok_with_the_reason_and_the_child(self):
        self._summarise(self.sw, "Maria was found with her uncle Villanueva.")
        job = AssistantJob.objects.get()
        self.assertFalse(job.ok)
        self.assertIn("Villanueva", job.error)
        self.assertEqual(self.child, job.child)
        self.assertEqual(f"casereferral:{self.referral.id}", job.input_ref)

    def test_a_refused_draft_is_not_the_one_a_confirmation_marks_accepted(self):
        self._summarise(self.sw, "Maria was found with her uncle Villanueva.")
        self.client.force_authenticate(self.sw)
        res = self.client.post(
            f"/api/assistant/confirm-case-referral-summary/{self.referral.id}/",
            {"text": "Maria was found at the barangay hall."}, format="json")
        self.assertEqual(200, res.status_code)
        self.assertEqual("pending", AssistantJob.objects.get().outcome)

    def test_only_names_are_checked_in_a_summary(self):
        # A paraphrase is not an invented date or number.
        res = self._summarise(
            self.sw, "Maria, about 7 years old, was found in 2026 with her aunt.")
        self.assertEqual(200, res.status_code, res.data)

    def test_the_isa_runs_the_same_check_on_a_referral(self):
        res = self._summarise(self.admin, "Maria was found with Villanueva.")
        self.assertEqual(422, res.status_code)

    def test_a_report_summary_is_left_as_it_was(self):
        # The psychologist's own: not a social worker's to reach (403), and not
        # part of this guard.
        report = PsychologicalReport.objects.create(
            child=self.child, author=self.psy, extracted_text="Full report text.")
        res = self._summarise(self.psy, "Seen by Villanueva.", kind="report", doc=report)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(403, self._summarise(
            self.sw, "Anything.", kind="report", doc=report).status_code)

    def test_a_document_too_long_to_read_whole_is_checked_against_what_was_read(self):
        # No headings, so the model is given the beginning and the end and not
        # the middle (prompts.fit_document); the name sits in the middle.
        long = ("Maria lives with her aunt. " * 1000 + "Villanueva signed the form. "
                + "Maria lives with her aunt. " * 1000)
        CaseReferral.objects.filter(pk=self.referral.pk).update(extracted_text=long)
        fitted, cut = prompts.fit_document(long)
        self.assertIsNotNone(cut)
        self.assertNotIn("Villanueva", fitted)
        # A name from the part the model was not shown could only be invented.
        res = self._summarise(self.sw, "Maria lives with her aunt, as Villanueva signed.")
        self.assertEqual(422, res.status_code)
