"""The assistant log keeps its rows and sheds its text.

Every rule is held at its edge, on a clock the test pins: the row one second
inside the window keeps its words, the row one second outside loses them, and
in both cases everything the access log, the usage table and the unanswered
list read is still there afterwards.
"""
from datetime import datetime, timedelta, timezone as tz
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import retention, services
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child
from clinical.models import CaseReferral, PsychologicalReport

User = get_user_model()
NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=tz.utc)
SECOND = timedelta(seconds=1)


def days(n):
    return timedelta(days=n)


def job(job_type, age, **fields):
    """A job row created `age` before NOW, whatever the clock says."""
    fields.setdefault("input_ref", "x:1")
    fields.setdefault("output_text", "model text")
    row = AssistantJob.objects.create(job_type=job_type, **fields)
    AssistantJob.objects.filter(pk=row.pk).update(created_at=NOW - age)
    return AssistantJob.objects.get(pk=row.pk)


def fresh(row):
    return AssistantJob.objects.get(pk=row.pk)


class RetentionBase(TestCase):
    def setUp(self):
        role, _ = Role.objects.get_or_create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        self.child = Child.objects.create(fullname="Maria Santos",
                                          assigned_psychologist=self.psy)


class ChatTest(RetentionBase):
    def chat(self, age):
        return job("chat", age, input_ref="Tell me about CHILD",
                   output_text="get_child_summary({'name': 'CHILD'})",
                   answer=AssistantJob.DATA, result_count=1,
                   outcome=AssistantJob.ACCEPTED, created_by=self.psy,
                   model_used="qwen", latency_ms=1234)

    def test_a_chat_just_inside_thirty_days_keeps_its_words(self):
        row = self.chat(days(30))                   # exactly 30 days: not yet
        retention.prune(NOW)
        self.assertEqual("Tell me about CHILD", fresh(row).input_ref)
        self.assertNotEqual("", fresh(row).output_text)

    def test_a_chat_just_outside_loses_both_question_and_call(self):
        row = self.chat(days(30) + SECOND)
        retention.prune(NOW)
        self.assertEqual("", fresh(row).input_ref)
        self.assertEqual("", fresh(row).output_text)

    def test_everything_else_about_the_chat_is_kept(self):
        row = self.chat(days(90))
        retention.prune(NOW)
        kept = fresh(row)
        self.assertEqual("chat", kept.job_type)
        self.assertEqual(AssistantJob.DATA, kept.answer)
        self.assertEqual(1, kept.result_count)
        self.assertEqual(AssistantJob.ACCEPTED, kept.outcome)
        self.assertEqual(self.psy, kept.created_by)
        self.assertEqual("qwen", kept.model_used)
        self.assertEqual(1234, kept.latency_ms)
        self.assertTrue(kept.ok)
        self.assertEqual(NOW - days(90), kept.created_at)

    def test_a_chat_with_only_one_of_the_two_is_still_blanked(self):
        row = job("chat", days(31), input_ref="", output_text="a call")
        other = job("chat", days(31), input_ref="a question", output_text="")
        retention.prune(NOW)
        self.assertEqual(("", ""), (fresh(row).input_ref, fresh(row).output_text))
        self.assertEqual(("", ""), (fresh(other).input_ref, fresh(other).output_text))


class DraftsTest(RetentionBase):
    def test_a_draft_loses_its_text_after_two_days_and_nothing_else(self):
        for job_type in ("brief", "case_brief", "remark_polish"):
            with self.subTest(job_type):
                inside = job(job_type, days(2), input_ref="child:12", child=self.child)
                outside = job(job_type, days(2) + SECOND, input_ref="child:12",
                              child=self.child, created_by=self.psy,
                              outcome=AssistantJob.EDITED)
                retention.prune(NOW)
                self.assertEqual("model text", fresh(inside).output_text)
                kept = fresh(outside)
                self.assertEqual("", kept.output_text)
                # What the access log reads.
                self.assertEqual("child:12", kept.input_ref)
                self.assertEqual(self.child, kept.child)
                self.assertEqual(self.psy, kept.created_by)
                self.assertEqual(AssistantJob.EDITED, kept.outcome)

    def test_a_non_chat_input_ref_is_never_blanked(self):
        rows = [job(t, days(400), input_ref=ref) for t, ref in (
            ("brief", "child:1"), ("case_brief", "child:2"), ("remark_polish", "remark:draft"),
            ("doc_intelligence", "report:3"), ("self_report", "invite:4"),
            ("census_narrative", "census"))]
        retention.prune(NOW)
        self.assertEqual(["child:1", "child:2", "remark:draft", "report:3", "invite:4", "census"],
                         [fresh(r).input_ref for r in rows])


class SelfReportTest(RetentionBase):
    def test_the_models_reply_goes_after_thirty_days(self):
        inside = job("self_report", days(30), input_ref="invite:4", output_text="YES - alone")
        outside = job("self_report", days(30) + SECOND, input_ref="invite:4",
                      output_text="YES - alone", child=self.child, error="note")
        retention.prune(NOW)
        self.assertEqual("YES - alone", fresh(inside).output_text)
        kept = fresh(outside)
        self.assertEqual("", kept.output_text)
        self.assertEqual(("invite:4", self.child, "note"),
                         (kept.input_ref, kept.child, kept.error))


class SummariesTest(RetentionBase):
    def setUp(self):
        super().setUp()
        self.report = PsychologicalReport.objects.create(
            child=self.child, author=self.psy, extracted_text="Text.",
            ai_summary="A draft.", ai_summary_confirmed=False)
        self.referral = CaseReferral.objects.create(
            child=self.child, uploaded_by=self.psy, extracted_text="Text.",
            ai_summary="A draft.", ai_summary_confirmed=False)

    def summary(self, doc, prefix, age=timedelta(0)):
        return job("doc_intelligence", age, input_ref=f"{prefix}:{doc.pk}",
                   child=self.child, output_text="The model's summary.")

    def test_a_summary_waiting_to_be_confirmed_is_kept(self):
        rows = [self.summary(self.report, "report"),
                self.summary(self.referral, "casereferral", days(29))]
        retention.prune(NOW)
        self.assertEqual(["The model's summary."] * 2, [fresh(r).output_text for r in rows])

    def test_confirming_it_makes_the_text_unnecessary(self):
        rows = [self.summary(self.report, "report"),
                self.summary(self.referral, "casereferral")]
        PsychologicalReport.objects.filter(pk=self.report.pk).update(ai_summary_confirmed=True)
        retention.prune(NOW)
        self.assertEqual("", fresh(rows[0]).output_text)
        self.assertEqual("The model's summary.", fresh(rows[1]).output_text)
        CaseReferral.objects.filter(pk=self.referral.pk).update(ai_summary_confirmed=True)
        retention.prune(NOW)
        self.assertEqual("", fresh(rows[1]).output_text)

    def test_a_summary_whose_document_is_gone_is_blanked_at_once(self):
        rows = [self.summary(self.report, "report"),
                self.summary(self.referral, "casereferral")]
        self.report.delete()
        retention.prune(NOW)
        self.assertEqual("", fresh(rows[0]).output_text)
        self.assertEqual("The model's summary.", fresh(rows[1]).output_text)
        self.referral.delete()
        retention.prune(NOW)
        self.assertEqual("", fresh(rows[1]).output_text)

    def test_the_access_log_still_knows_the_document_was_read_and_is_gone(self):
        row = self.summary(self.report, "report")
        reference = row.input_ref
        self.report.delete()
        retention.prune(NOW)
        kept = fresh(row)
        self.assertEqual(reference, kept.input_ref)
        self.assertEqual(self.child, kept.child)

    def test_an_unconfirmed_summary_goes_after_thirty_days_anyway(self):
        inside = self.summary(self.report, "report", days(30))
        outside = self.summary(self.report, "report", days(30) + SECOND)
        retention.prune(NOW)
        self.assertEqual("The model's summary.", fresh(inside).output_text)
        self.assertEqual("", fresh(outside).output_text)

    def test_a_reference_it_cannot_read_is_left_to_the_age_rule(self):
        odd = [job("doc_intelligence", days(1), input_ref=ref)
               for ref in ("", "report", "report:abc", "elsewhere:1", "report:99999999")]
        retention.prune(NOW)
        # "report:99999999" names no document: gone.
        self.assertEqual(["model text"] * 4 + [""], [fresh(r).output_text for r in odd])


class WhatItDoesNotTouchTest(RetentionBase):
    def test_a_census_narrative_is_agency_figures_and_is_left_alone(self):
        row = job("census_narrative", days(400))
        retention.prune(NOW)
        self.assertEqual("model text", fresh(row).output_text)

    def test_a_row_with_nothing_to_blank_is_not_counted(self):
        job("chat", days(60), input_ref="", output_text="")
        job("brief", days(60), output_text="")
        self.assertEqual({"chat": 0, "drafts": 0, "summaries_aged": 0,
                          "summaries_finished": 0, "self_report": 0}, retention.prune(NOW))

    def test_it_is_safe_to_run_twice(self):
        job("chat", days(60))
        job("brief", days(5))
        first = retention.prune(NOW)
        second = retention.prune(NOW)
        self.assertEqual((1, 1), (first["chat"], first["drafts"]))
        self.assertEqual(0, sum(second.values()))
        self.assertEqual(2, AssistantJob.objects.count())

    def test_no_row_is_ever_deleted(self):
        for t in ("chat", "brief", "doc_intelligence", "self_report", "remark_polish"):
            job(t, days(500))
        before = AssistantJob.objects.count()
        retention.prune(NOW)
        self.assertEqual(before, AssistantJob.objects.count())

    def test_a_dry_run_counts_and_changes_nothing(self):
        old = job("chat", days(60))
        counts = retention.prune(NOW, dry_run=True)
        self.assertEqual(1, counts["chat"])
        self.assertEqual("x:1", fresh(old).input_ref)
        self.assertEqual("model text", fresh(old).output_text)


class TheReadersStillWorkTest(APITestCase):
    """The ISA's access log, the usage table and the unanswered list say the
    same thing after pruning as before it."""

    def setUp(self):
        roles = {n: Role.objects.get_or_create(role_name=n)[0]
                 for n in (Role.ADMINISTRATOR, Role.PSYCHOLOGIST)}
        self.isa = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=roles[Role.ADMINISTRATOR], first_name="Ada", last_name="Lim")
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=roles[Role.PSYCHOLOGIST], first_name="Pia", last_name="Reyes")
        self.child = Child.objects.create(fullname="Maria Santos",
                                          assigned_psychologist=self.psy)
        self.report = PsychologicalReport.objects.create(
            child=self.child, author=self.psy, original_filename="r.pdf",
            extracted_text="Text.", ai_summary="Confirmed.", ai_summary_confirmed=True)
        self.client.force_authenticate(self.isa)

    def make_history(self):
        # Rows written "now" and then aged, so created_at is what a real
        # deployment would hold: the readers use the real clock for their window.
        from django.utils import timezone
        real = timezone.now()

        def aged(row, age):
            AssistantJob.objects.filter(pk=row.pk).update(created_at=real - age)

        aged(AssistantJob.objects.create(
            job_type="brief", input_ref=f"child:{self.child.pk}", child=self.child,
            output_text="A brief.", created_by=self.psy), days(5))
        aged(AssistantJob.objects.create(
            job_type="doc_intelligence", input_ref=f"report:{self.report.pk}",
            child=self.child, output_text="A summary.", created_by=self.psy,
            outcome=AssistantJob.ACCEPTED), days(40))
        aged(AssistantJob.objects.create(
            job_type="self_report", input_ref="invite:77", child=self.child,
            output_text="YES - alone"), days(40))
        for age, count, answer in ((days(3), 0, AssistantJob.DATA),
                                   (days(10), None, AssistantJob.DECLINED),
                                   (days(50), 0, AssistantJob.DATA)):
            aged(AssistantJob.objects.create(
                job_type="chat", input_ref="How many in CHILD's group?",
                output_text="get_statistics({})", answer=answer, result_count=count,
                created_by=self.psy), age)
        return real

    def read(self):
        return [self.client.get(f"/api/assistant/access-log/child/{self.child.pk}/").json(),
                self.client.get("/api/assistant/metrics/").json(),
                self.client.get("/api/assistant/unanswered/").json()]

    def test_the_access_log_the_usage_table_and_the_unanswered_list_do_not_change(self):
        real = self.make_history()
        before = self.read()
        counts = retention.prune(real)
        self.assertGreater(sum(counts.values()), 0)
        self.assertEqual(before, self.read())

    def test_the_text_really_went(self):
        real = self.make_history()
        retention.prune(real)
        self.assertEqual(
            {"brief": "", "doc_intelligence": "", "self_report": ""},
            {j.job_type: j.output_text for j in AssistantJob.objects.exclude(job_type="chat")})
        # Of the chats, only the one older than thirty days lost its words.
        chats = AssistantJob.objects.filter(job_type="chat").order_by("created_at")
        self.assertEqual(["", "get_statistics({})", "get_statistics({})"],
                         [c.output_text for c in chats])
        self.assertEqual(["", "How many in CHILD's group?", "How many in CHILD's group?"],
                         [c.input_ref for c in chats])


class TheCommandTest(RetentionBase):
    def run_command(self, *args):
        out = StringIO()
        call_command("prune_assistant_text", *args, stdout=out)
        return out.getvalue()

    def test_it_prunes_by_hand_and_says_what_it_did(self):
        row = job("chat", days(400))
        # The real clock: 2026 rows aged from NOW are far older than any window.
        with patch("django.utils.timezone.now", return_value=NOW):
            text = self.run_command()
        self.assertIn("Blanked     1  chat questions and tool calls", text)
        self.assertEqual("", fresh(row).output_text)
        self.assertIn("Rows, types, people, children and dates are kept.", text)

    def test_a_dry_run_only_counts(self):
        row = job("chat", days(400))
        with patch("django.utils.timezone.now", return_value=NOW):
            text = self.run_command("--dry-run")
        self.assertIn("Would blank     1  chat questions", text)
        self.assertEqual("model text", fresh(row).output_text)


@override_settings(ASSISTANT_PRUNE_ON_REQUEST=True)
class TheLazyHookTest(RetentionBase):
    def setUp(self):
        super().setUp()
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.addCleanup(setattr, retention, "_last_run", None)
        retention._last_run = None

    def test_the_first_call_prunes_and_the_next_within_the_hour_does_not(self):
        with patch.object(retention, "prune") as prune:
            self.assertTrue(retention.prune_if_due(NOW))
            self.assertFalse(retention.prune_if_due(NOW + timedelta(minutes=59, seconds=59)))
            self.assertTrue(retention.prune_if_due(NOW + timedelta(hours=1)))
            self.assertFalse(retention.prune_if_due(NOW + timedelta(hours=1, minutes=5)))
        self.assertEqual(2, prune.call_count)

    def test_a_clock_that_went_backwards_is_not_a_reason_to_wait(self):
        with patch.object(retention, "prune") as prune:
            retention.prune_if_due(NOW)
            self.assertTrue(retention.prune_if_due(NOW - days(1)))
        self.assertEqual(2, prune.call_count)

    def test_a_failure_never_reaches_the_request(self):
        with patch.object(retention, "prune", side_effect=RuntimeError("locked")):
            with self.assertLogs("assistant.retention", level="ERROR"):
                self.assertTrue(retention.prune_if_due(NOW))
            # ...and is not retried on every request that follows.
            self.assertFalse(retention.prune_if_due(NOW + timedelta(minutes=1)))

    def test_it_is_off_while_the_test_runner_is(self):
        with override_settings(ASSISTANT_PRUNE_ON_REQUEST=False), \
                patch.object(retention, "prune") as prune:
            self.assertFalse(retention.prune_if_due(NOW))
        prune.assert_not_called()

    def test_generating_something_prunes(self):
        old = job("brief", days(10))
        with patch.object(services.OllamaClient, "generate", return_value="Draft."), \
                patch("django.utils.timezone.now", return_value=NOW):
            services.run_job("brief", "prompt", input_ref="child:1")
        self.assertEqual("", fresh(old).output_text)

    def test_generating_twice_in_the_hour_prunes_once(self):
        with patch.object(services.OllamaClient, "generate", return_value="Draft."), \
                patch.object(retention, "prune") as prune, \
                patch("django.utils.timezone.now", return_value=NOW):
            services.run_job("brief", "prompt", input_ref="child:1")
            services.run_job("brief", "prompt", input_ref="child:1")
        self.assertEqual(1, prune.call_count)

    def test_a_pruning_failure_does_not_stop_a_draft(self):
        with patch.object(services.OllamaClient, "generate", return_value="Draft."), \
                patch.object(retention, "prune", side_effect=RuntimeError("locked")), \
                self.assertLogs("assistant.retention", level="ERROR"):
            text, row = services.run_job("brief", "prompt", input_ref="child:1")
        self.assertEqual("Draft.", text)
        self.assertEqual("Draft.", fresh(row).output_text)


@override_settings(ASSISTANT_PRUNE_ON_REQUEST=True)
class TheChatPathPrunesTooTest(APITestCase):
    def setUp(self):
        role, _ = Role.objects.get_or_create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.addCleanup(setattr, retention, "_last_run", None)
        retention._last_run = None
        self.client.force_authenticate(self.psy)

    def ask(self):
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_statistics", {})):
            return self.client.post("/api/assistant/ask/", {"question": "ilan?"},
                                    format="json")

    def test_answering_a_question_prunes_what_is_old(self):
        old = job("chat", days(40), input_ref="An old question")
        with patch("django.utils.timezone.now", return_value=NOW):
            self.assertEqual(200, self.ask().status_code)
        self.assertEqual("", fresh(old).input_ref)

    def test_and_a_follow_up_does_too(self):
        old = job("chat", days(40), input_ref="An old question")
        with patch("django.utils.timezone.now", return_value=NOW):
            res = self.client.post("/api/assistant/followup/", {
                "tool": "get_statistics", "args": {"by": "sex"}, "label": "By sex?"},
                format="json")
        self.assertEqual(200, res.status_code)
        self.assertEqual("", fresh(old).input_ref)

    def test_the_answer_itself_is_not_pruned_away(self):
        with patch("django.utils.timezone.now", return_value=NOW):
            self.ask()
        self.assertEqual("ilan?", AssistantJob.objects.get(
            created_at=NOW).input_ref)


class ConfirmingAnOldSummaryTest(APITestCase):
    def test_a_draft_whose_text_has_gone_is_not_recorded_as_an_edit(self):
        roles = {n: Role.objects.get_or_create(role_name=n)[0]
                 for n in (Role.PSYCHOLOGIST,)}
        psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=roles[Role.PSYCHOLOGIST])
        child = Child.objects.create(fullname="Maria Santos", assigned_psychologist=psy)
        report = PsychologicalReport.objects.create(
            child=child, author=psy, extracted_text="Text.", ai_summary="A draft.")
        row = job("doc_intelligence", days(60), input_ref=f"report:{report.pk}",
                  output_text="", created_by=psy)
        self.client.force_authenticate(psy)
        res = self.client.post(f"/api/assistant/confirm-summary/{report.pk}/",
                               {"text": "A draft."}, format="json")
        self.assertEqual(200, res.status_code)
        self.assertEqual(AssistantJob.PENDING, fresh(row).outcome)

    def test_a_draft_that_still_has_its_text_is_judged_as_before(self):
        role, _ = Role.objects.get_or_create(role_name=Role.PSYCHOLOGIST)
        psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        child = Child.objects.create(fullname="Maria Santos", assigned_psychologist=psy)
        report = PsychologicalReport.objects.create(
            child=child, author=psy, extracted_text="Text.", ai_summary="A draft.")
        row = job("doc_intelligence", timedelta(0), input_ref=f"report:{report.pk}",
                  output_text="A draft.", created_by=psy)
        self.client.force_authenticate(psy)
        self.client.post(f"/api/assistant/confirm-summary/{report.pk}/",
                         {"text": "A draft."}, format="json")
        self.assertEqual(AssistantJob.ACCEPTED, fresh(row).outcome)
