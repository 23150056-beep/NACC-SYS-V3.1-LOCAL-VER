"""The model is a second detector. It can only add flags, never clear one."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from assistant.models import AssistantJob, AssistantSetting
from assistant.services import AIUnavailable
from children.models import Child
from clinical.models import AgencyFormTemplate, OpinionnaireInvite, SelfReportFlag
from clinical.self_report_model_check import _parse, run_model_check

User = get_user_model()

FEELING = "How are you feeling this week?"


class ParseReplyTest(TestCase):
    """Never flag on a reply we did not understand. A flag nobody can explain
    is worse than no flag."""

    def test_a_yes_yields_the_reason(self):
        self.assertEqual("the child sounds overwhelmed",
                         _parse("YES - the child sounds overwhelmed"))

    def test_a_no_yields_nothing(self):
        self.assertIsNone(_parse("NO - the child sounds settled"))

    def test_an_unparseable_reply_yields_nothing(self):
        self.assertIsNone(_parse("I think perhaps maybe"))

    def test_blank_yields_nothing(self):
        self.assertIsNone(_parse(""))
        self.assertIsNone(_parse(None))

    def test_a_yes_with_no_reason_still_yields_something_sayable(self):
        self.assertTrue(_parse("YES"))


class ModelCheckBase(TestCase):
    """Fixtures only - no tests, so a subclass does not run them twice."""

    def setUp(self):
        role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        self.child = Child.objects.create(
            fullname="Maria Santos", assigned_psychologist=psy)
        template = AgencyFormTemplate.objects.create(
            title="Self-report", fields=[{"label": FEELING}])
        self.invite = OpinionnaireInvite.objects.create(
            child=self.child, template=template,
            status=OpinionnaireInvite.SUBMITTED, submitted_at=timezone.now(),
            answers={FEELING: "Kasla nagbaliw ti riknak."},
            expires_at=timezone.now() + timedelta(days=7))
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()

    def _run(self, reply):
        with patch("assistant.services.OllamaClient.generate", return_value=reply):
            run_model_check(self.invite.pk)


class ModelCheckTest(ModelCheckBase):
    def test_a_yes_creates_a_model_flag(self):
        self._run("YES - the child sounds overwhelmed")
        flag = SelfReportFlag.objects.get()
        self.assertEqual(SelfReportFlag.MODEL, flag.source)
        self.assertIn("overwhelmed", flag.matched)
        self.assertEqual(self.child, flag.child)

    def test_a_no_creates_nothing(self):
        self._run("NO - the child sounds settled")
        self.assertEqual(0, SelfReportFlag.objects.count())

    def test_an_unparseable_reply_creates_nothing(self):
        self._run("I think perhaps maybe")
        self.assertEqual(0, SelfReportFlag.objects.count())

    def test_it_never_removes_a_lexicon_flag(self):
        SelfReportFlag.objects.create(
            invite=self.invite, child=self.child, question=FEELING,
            answer="Kasla nagbaliw ti riknak.",
            source=SelfReportFlag.LEXICON, matched="riknak")
        self._run("NO - the child sounds settled")
        self.assertEqual(1, SelfReportFlag.objects.count())
        self.assertEqual(SelfReportFlag.LEXICON, SelfReportFlag.objects.get().source)

    def test_the_runtime_being_down_is_not_an_error(self):
        with patch("assistant.services.OllamaClient.generate",
                   side_effect=AIUnavailable("refused")):
            run_model_check(self.invite.pk)          # must not raise
        self.assertEqual(0, SelfReportFlag.objects.count())

    def test_the_runtime_being_down_is_audited(self):
        with patch("assistant.services.OllamaClient.generate",
                   side_effect=AIUnavailable("refused")):
            run_model_check(self.invite.pk)
        job = AssistantJob.objects.get()
        self.assertEqual("self_report", job.job_type)
        self.assertFalse(job.ok)

    def test_it_does_nothing_when_the_assistant_is_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        with patch("assistant.services.OllamaClient.generate") as gen:
            run_model_check(self.invite.pk)
        gen.assert_not_called()

    def test_running_twice_does_not_duplicate(self):
        self._run("YES - sounds distressed")
        self._run("YES - sounds distressed")
        self.assertEqual(1, SelfReportFlag.objects.count())

    def test_a_missing_invite_is_not_an_error(self):
        run_model_check(999999)                      # must not raise

    def test_a_blank_answer_is_skipped(self):
        self.invite.answers = {FEELING: "   "}
        self.invite.save()
        with patch("assistant.services.OllamaClient.generate") as gen:
            run_model_check(self.invite.pk)
        gen.assert_not_called()


class TheReasonIsHeldToTheAnswerTest(ModelCheckBase):
    """The model's few words are stored on the flag and read by whoever can read
    the child's flags, a social worker included. They may say only what the
    question and answer it was shown say: no name, date or number that is not
    in them. A reason that does is replaced with a fixed one - the flag is not."""

    ANSWER = "Wala akong kausap kundi si Lola, minsan 2 gabi akong umiiyak."

    def setUp(self):
        super().setUp()
        OpinionnaireInvite.objects.filter(pk=self.invite.pk).update(
            answers={FEELING: self.ANSWER})

    def _flag(self, reply):
        self._run(reply)
        return SelfReportFlag.objects.get()

    def test_a_reason_drawn_from_the_answer_is_kept_as_written(self):
        flag = self._flag("YES - only talks to Lola, cries for 2 nights")
        self.assertEqual("only talks to Lola, cries for 2 nights", flag.matched)
        self.assertEqual("", AssistantJob.objects.get().error)

    def test_a_name_the_answer_never_had_replaces_the_reason_and_not_the_flag(self):
        flag = self._flag("YES - the child says that Villanueva hurt them")
        self.assertEqual(SelfReportFlag.MODEL, flag.source)
        self.assertEqual("flagged by the local model", flag.matched)
        self.assertEqual(self.ANSWER, flag.answer)
        self.assertEqual(self.child, flag.child)

    def test_a_date_and_a_number_the_answer_never_had_do_the_same(self):
        for reply in ("YES - this began on 14 March", "YES - cries 77 nights a year"):
            with self.subTest(reply):
                SelfReportFlag.objects.all().delete()
                AssistantJob.objects.all().delete()
                flag = self._flag(reply)
                self.assertEqual("flagged by the local model", flag.matched)

    def test_what_was_found_goes_to_the_audit_row_which_stays_ok(self):
        self._flag("YES - the child says that Villanueva hurt them")
        job = AssistantJob.objects.get()
        self.assertTrue(job.ok)
        self.assertTrue(job.error.startswith("reason not shown: "), job.error)
        self.assertIn("Villanueva", job.error)
        # The audit row keeps the model's reply, which only an administrator reads.
        self.assertIn("Villanueva", job.output_text)

    def test_a_yes_with_no_reason_is_the_fixed_one_and_is_not_a_refusal(self):
        flag = self._flag("YES")
        self.assertEqual("flagged by the local model", flag.matched)
        self.assertEqual("", AssistantJob.objects.get().error)

    def test_a_no_is_still_nothing(self):
        self._run("NO - Villanueva said 77 things on 14 March")
        self.assertEqual(0, SelfReportFlag.objects.count())
