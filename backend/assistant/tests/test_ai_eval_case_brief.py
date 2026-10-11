"""`manage.py ai_eval --feature case_brief`: the measurement the owner runs on
his PC before the social worker's written brief is relied on.

It needs a live model, so here the client is faked. What is held is everything
around the model: which children are sampled and as whom, that nothing is
written (no AssistantJob), that the prompt is the app's own, how drafts are
scored, when rates are withheld, and what the last line says.
"""
import re
from datetime import datetime
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from assistant import prompts, services
from assistant.brief_facts import brief_facts
from assistant.management.commands import ai_eval
from assistant.management.commands.ai_eval import (
    _EvalRequest, case_brief_sample, case_brief_verdict, score_case_brief)
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child
from clinical.models import CaseReferral

User = get_user_model()

NOW = timezone.make_aware(datetime(2026, 10, 8, 10, 0))

FIRST_NAMES = ["Ana", "Ben", "Cora", "Dino", "Eli", "Fe", "Gil", "Hana", "Ivo", "Joy",
               "Kai", "Lea", "Mon", "Nina"]


def model_that_writes_a_clean_brief(prompt, system=None):
    """What a perfect model would write: the child's first name, nothing else."""
    name = re.search(r"First name: (.+)", prompt).group(1)
    return (f"1. {name} was referred to the agency.\n"
            "2. Check the booking and who is waiting.\n"
            "3. Ask about the consent.")


class EvalFixture(TestCase):
    def setUp(self):
        roles = {n: Role.objects.create(role_name=n)
                 for n in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        self.roles = roles
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=roles[Role.STAFF])
        self.other_sw = User.objects.create_user(
            email="t@racco1.gov.ph", username="t", password="pass1234", role=roles[Role.STAFF])
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=roles[Role.PSYCHOLOGIST])
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=roles[Role.ADMINISTRATOR])
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.n = 0

    def _child(self, summary=False, sw=None, **fields):
        name = FIRST_NAMES[self.n % len(FIRST_NAMES)]
        self.n += 1
        fields.setdefault("case_type", "Foster Care")
        child = Child.objects.create(
            first_name=name, last_name=f"Zzyzx{self.n}", social_worker=sw or self.sw,
            assigned_psychologist=self.psy, **fields)
        if summary:
            CaseReferral.objects.create(
                child=child, uploaded_by=self.sw, original_filename="r.pdf",
                ai_summary="Referred after a fire at home.", ai_summary_confirmed=True)
        return child

    def _children(self, count, **fields):
        return [self._child(**fields) for _ in range(count)]

    def _run(self, *args, model=model_that_writes_a_clean_brief, **options):
        out = StringIO()
        with patch.object(services.OllamaClient, "generate", side_effect=model) as generate:
            call_command("ai_eval", "--feature", "case_brief", *args, stdout=out, **options)
        self.generate = generate
        return out.getvalue()


class WhoIsSampledTest(EvalFixture):
    def test_children_with_a_confirmed_summary_come_first(self):
        plain = self._children(4)
        with_summary = [self._child(summary=True) for _ in range(3)]
        sample = case_brief_sample(5)
        self.assertEqual([c.id for c in with_summary] + [c.id for c in plain[:2]],
                         [c.id for c, _ in sample])
        self.assertEqual([True, True, True, False, False], [has for _, has in sample])

    def test_an_unconfirmed_summary_does_not_count_as_one(self):
        child = self._child()
        CaseReferral.objects.create(child=child, uploaded_by=self.sw, original_filename="r.pdf",
                                    ai_summary="A draft.", ai_summary_confirmed=False)
        self.assertEqual([(child, False)], case_brief_sample(5))

    def test_only_the_latest_referrals_summary_counts(self):
        child = self._child(summary=True)
        CaseReferral.objects.create(child=child, uploaded_by=self.sw, original_filename="new.pdf")
        self.assertEqual([(child, False)], case_brief_sample(5))

    def test_the_limit_is_respected(self):
        self._children(6)
        self.assertEqual(4, len(case_brief_sample(4)))

    def test_only_active_children_held_by_an_active_social_worker(self):
        keep = self._child()
        self._child(status=Child.INACTIVE)
        child = self._child()
        Child.objects.filter(pk=child.pk).update(social_worker=None)
        # A record held by an administrator or a psychologist is nobody's
        # social-worker view, and an archived account is nobody.
        self._child(sw=self.admin)
        self._child(sw=self.psy)
        archived = User.objects.create_user(
            email="x@racco1.gov.ph", username="x", password="pass1234",
            role=self.roles[Role.STAFF], status=User.ARCHIVED)
        self._child(sw=archived)
        self.assertEqual([keep.id], [c.id for c, _ in case_brief_sample(10)])

    def test_nothing_to_sample(self):
        self.assertEqual([], case_brief_sample(10))


class WhatIsAskedTest(EvalFixture):
    def test_the_prompt_is_the_apps_own_built_from_that_social_workers_facts(self):
        mine = self._child(summary=True)
        theirs = self._child(sw=self.other_sw)
        self._run("--reps", "1")
        sent = {call.args[0] for call in self.generate.call_args_list}
        expected = {
            prompts.build_case_brief_prompt(
                brief_facts(_EvalRequest(child.social_worker), child), child)
            for child in (mine, theirs)}
        self.assertEqual(expected, sent)
        for prompt in sent:
            self.assertTrue(prompt.startswith(prompts.CASE_BRIEF_INSTRUCTIONS))
            # The case kind: the rows a social worker's facts panel has.
            self.assertIn("Case referral:", prompt)

    def test_the_client_gets_the_prompt_and_the_system_text_only(self):
        self._child()
        self._run("--reps", "2")
        self.assertEqual(2, self.generate.call_count)
        for call in self.generate.call_args_list:
            self.assertEqual(1, len(call.args))
            self.assertEqual({"system": prompts.CASE_BRIEF_SYSTEM}, call.kwargs)

    def test_it_writes_no_job(self):
        self._children(3, summary=True)
        self._run("--reps", "2")
        self.assertFalse(AssistantJob.objects.exists())

    def test_the_defaults_are_ten_children_and_three_drafts_each(self):
        self._children(12)
        out = self._run()
        self.assertIn("CASE BRIEF - 10 children x 3 reps", out)
        self.assertEqual(30, self.generate.call_count)

    def test_the_options_override_the_defaults(self):
        self._children(12)
        out = self._run("--limit", "2", "--reps", "1")
        self.assertIn("CASE BRIEF - 2 children x 1 reps", out)
        self.assertEqual(2, self.generate.call_count)

    def test_the_other_features_keep_their_own_defaults(self):
        # Two reps and three children, as before: a run of `brief` is unchanged.
        self.assertEqual(
            (None, None),
            (ai_eval.Command().create_parser("m", "ai_eval").parse_args([]).reps,
             ai_eval.Command().create_parser("m", "ai_eval").parse_args([]).limit))


class WhatIsPrintedTest(EvalFixture):
    def test_below_ten_children_only_counts_are_printed_and_it_says_why(self):
        self._children(4, summary=True)
        out = self._run("--reps", "2")
        self.assertRegex(out, r"invented names\s+0 of 8 drafts")
        self.assertIn("Rates are not printed: only 4 distinct children were evaluated", out)
        self.assertIn("fewer than 10 children", out)
        self.assertNotIn("%", out)

    def test_one_child_is_worded_in_the_singular(self):
        self._child()
        out = self._run("--reps", "1")
        self.assertIn("only 1 distinct child was evaluated", out)

    def test_from_ten_children_the_rates_are_printed(self):
        self._children(10)
        out = self._run("--reps", "1")
        self.assertRegex(out, r"invented names\s+0/10  \(0%\)")
        self.assertNotIn("Rates are not printed", out)

    def test_the_summary_says_how_many_children_had_a_confirmed_summary(self):
        self._children(3)
        for _ in range(2):
            self._child(summary=True)
        out = self._run("--reps", "1")
        self.assertIn("children evaluated     5  (2 with a confirmed referral summary)", out)

    def test_the_median_latency_is_printed(self):
        self._children(2)
        out = self._run("--reps", "3")
        self.assertRegex(out, r"median latency         \d+ ms")

    def test_every_flag_is_counted_in_the_table(self):
        self._child()
        out = self._run("--reps", "1")
        for label in ("invented names", "invented dates", "invented numbers",
                      "over 150 words", "repeated lines", "language drift"):
            self.assertIn(label, out)

    def test_five_drafts_are_printed_in_full_with_first_names_only(self):
        self._children(8, summary=True)
        out = self._run("--reps", "1")
        self.assertIn("SAMPLE DRAFTS - 5 of 8, in full (first names only)", out)
        samples = out.split("SAMPLE DRAFTS")[1]
        self.assertEqual(5, samples.count("1. "), samples)
        self.assertEqual(5, samples.count("3. Ask about the consent."))
        # A child is named by first name wherever it is named at all.
        self.assertNotIn("Zzyzx", out)
        for child in Child.objects.all():
            self.assertNotIn(child.fullname, out)

    def test_samples_are_spread_across_children_before_a_second_draft_of_one(self):
        self._children(5)
        out = self._run("--reps", "3")
        samples = out.split("SAMPLE DRAFTS")[1]
        heads = re.findall(r"\[\d\] (\w+) \(id=(\d+)\), rep(\d)", samples)
        self.assertEqual(5, len({cid for _, cid, _ in heads}))
        self.assertEqual({"0"}, {rep for _, _, rep in heads})

    def test_fewer_than_five_drafts_are_all_printed(self):
        self._child()
        out = self._run("--reps", "2")
        self.assertIn("SAMPLE DRAFTS - 2 of 2, in full", out)

    def test_a_flagged_draft_shows_where_it_fired(self):
        self._child()
        out = self._run("--reps", "1", model=lambda p, system=None: (
            "1. Booked for 20 October.\n2. Check.\n3. Ask."))
        self.assertIn("invented dates=['20 October']", out)
        self.assertRegex(out, r"invented dates: .*20 October")


class TheVerdictTest(EvalFixture):
    def _last(self, out):
        return [line for line in out.splitlines() if line.strip()][-1]

    def test_thirty_clean_drafts_from_ten_children_pass(self):
        self._children(10)
        out = self._run()
        self.assertEqual("PASS: 0 invented names, 0 invented dates over 30 drafts",
                         self._last(out))

    def test_it_is_the_last_line_of_the_output(self):
        self._children(2)
        out = self._run("--reps", "1")
        self.assertTrue(self._last(out).startswith("NOT YET: "))
        # And a run of this feature alone prints no general summary table after it.
        self.assertNotIn("\nSUMMARY", out)

    def test_twenty_drafts_are_not_enough(self):
        self._children(10)
        out = self._run("--reps", "2")
        self.assertEqual(
            "NOT YET: only 20 drafts from 10 children (PASS needs at least 30 from "
            "at least 10)", self._last(out))

    def test_thirty_drafts_from_five_children_are_not_enough(self):
        self._children(5)
        out = self._run("--reps", "6")
        self.assertEqual(
            "NOT YET: only 30 drafts from 5 children (PASS needs at least 30 from "
            "at least 10)", self._last(out))

    def test_an_invented_name_is_not_yet(self):
        children = self._children(10)

        def model(prompt, system=None):
            text = model_that_writes_a_clean_brief(prompt)
            if f"First name: {children[0].first_name}\n" in prompt:
                return text + "\nSpeak to Villanueva first."
            return text
        out = self._run(model=model)
        self.assertEqual(
            "NOT YET: 3 drafts with invented names", self._last(out))

    def test_an_invented_date_is_not_yet(self):
        self._children(10)
        out = self._run(model=lambda p, system=None: (
            "1. Booked for 20 October.\n2. Check.\n3. Ask."))
        self.assertEqual("NOT YET: 30 drafts with invented dates", self._last(out))

    def test_both_are_named(self):
        self._children(10)
        out = self._run(model=lambda p, system=None: (
            "1. Booked for 20 October with Villanueva.\n2. Check.\n3. Ask."))
        self.assertEqual(
            "NOT YET: 30 drafts with invented names, 30 drafts with invented dates",
            self._last(out))

    def test_invented_numbers_and_length_are_reported_but_do_not_decide_it(self):
        self._children(10)
        words = " ".join(["word"] * 160)
        out = self._run(model=lambda p, system=None: (
            f"1. There are 77 open problems. {words}\n2. Check.\n3. Ask."))
        self.assertEqual("PASS: 0 invented names, 0 invented dates over 30 drafts",
                         self._last(out))
        self.assertRegex(out, r"invented numbers\s+30/30  \(100%\)")
        self.assertRegex(out, r"over 150 words\s+30/30  \(100%\)")

    def test_tagalog_drift_is_reported_and_not_failed(self):
        self._children(10)
        out = self._run(model=lambda p, system=None: (
            "1. Hindi masyado nagsasalita ngayon.\n2. Check.\n3. Ask."))
        self.assertEqual("PASS: 0 invented names, 0 invented dates over 30 drafts",
                         self._last(out))
        self.assertRegex(
            out, r"language drift\s+30/30  \(100%\)  \(reported, not failed\)")

    def test_a_model_that_is_down_measures_nothing(self):
        self._children(2)
        out = self._run(model=services.AIUnavailable("down"))
        self.assertIn("UNAVAILABLE - down", out)
        self.assertEqual("NOT YET: no draft was produced, so nothing was measured.",
                         self._last(out))
        self.assertFalse(AssistantJob.objects.exists())

    def test_no_child_to_evaluate_as_is_not_yet(self):
        out = self._run()
        self.assertIn("nothing to evaluate as", out)
        self.assertEqual("NOT YET: no draft was produced, so nothing was measured.",
                         self._last(out))

    def test_switched_off_there_is_nothing_to_evaluate(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        out = StringIO()
        with self.assertRaises(SystemExit):
            call_command("ai_eval", "--feature", "case_brief", stdout=out)
        self.assertIn("switched off", out.getvalue())


class OtherFeaturesAreUnchangedTest(EvalFixture):
    def test_polish_still_prints_its_own_summary_and_no_case_brief(self):
        out = StringIO()
        with patch.object(services.OllamaClient, "generate", return_value="Settling in well."):
            call_command("ai_eval", "--feature", "polish", stdout=out)
        text = out.getvalue()
        self.assertIn("REMARK POLISH", text)
        self.assertIn("SUMMARY", text)
        self.assertIn("REMARK POLISH  (6 runs", text)
        self.assertNotIn("CASE BRIEF", text)
        self.assertNotIn("PASS", text)
        self.assertNotIn("NOT YET", text)

    def test_brief_does_not_run_the_case_brief(self):
        out = StringIO()
        with patch.object(services.OllamaClient, "generate", return_value="A brief."):
            call_command("ai_eval", "--feature", "brief", stdout=out)
        self.assertNotIn("CASE BRIEF", out.getvalue())


class ScoringTest(EvalFixture):
    """score_case_brief on strings alone."""

    def _prompt(self):
        child = self._child(summary=True)
        return prompts.build_case_brief_prompt(
            brief_facts(_EvalRequest(self.sw), child), child), child

    def test_a_clean_draft_has_no_flags(self):
        prompt, child = self._prompt()
        self.assertEqual({}, score_case_brief(prompt, model_that_writes_a_clean_brief(prompt)))

    def test_each_kind_of_fault_is_named(self):
        prompt, child = self._prompt()
        draft = ("1. Speak to Villanueva about the visit on 20 October.\n"
                 "1. Speak to Villanueva about the visit on 20 October.\n"
                 "2. There are 77 open problems. " + "word " * 150 + "\n"
                 "3. Hindi masyado.")
        flags = score_case_brief(prompt, draft)
        self.assertEqual({"invented names", "invented dates", "invented numbers",
                          "over 150 words", "repeated lines", "language drift"},
                         set(flags))
        self.assertIn("Villanueva", flags["invented names"])
        self.assertEqual(["20 October"], flags["invented dates"])
        self.assertIn("77", flags["invented numbers"])

    def test_the_childs_own_first_name_and_the_facts_dates_are_not_inventions(self):
        prompt, child = self._prompt()
        name = prompts.first_name(child)
        # The referral was filed "today" (the clock is pinned to Thursday 8 October).
        draft = (f"1. {name} was referred, filed Thursday 8 October 2026.\n"
                 "2. Nothing is booked.\n3. Ask.")
        self.assertEqual({}, score_case_brief(prompt, draft))

    def test_the_instructions_digits_are_not_facts(self):
        prompt, child = self._prompt()
        self.assertEqual({"invented numbers": ["150"]},
                         score_case_brief(prompt, "1. Within 150 words.\n2. Check.\n3. Ask."))


class VerdictLineTest(EvalFixture):
    def test_the_two_lines_the_owner_will_read(self):
        self.assertEqual("PASS: 0 invented names, 0 invented dates over 30 drafts",
                         case_brief_verdict(30, 10, 0, 0))
        self.assertEqual("PASS: 0 invented names, 0 invented dates over 90 drafts",
                         case_brief_verdict(90, 30, 0, 0))

    def test_one_of_each_is_singular(self):
        self.assertEqual(
            "NOT YET: 1 draft with invented names, 1 draft with invented dates",
            case_brief_verdict(30, 10, 1, 1))

    def test_a_short_run_names_what_it_lacked_alongside_what_it_found(self):
        self.assertEqual(
            "NOT YET: 2 drafts with invented names; only 1 draft from 1 child "
            "(PASS needs at least 30 from at least 10)",
            case_brief_verdict(1, 1, 2, 0))

    def test_no_drafts(self):
        self.assertEqual("NOT YET: no draft was produced, so nothing was measured.",
                         case_brief_verdict(0, 0, 0, 0))
