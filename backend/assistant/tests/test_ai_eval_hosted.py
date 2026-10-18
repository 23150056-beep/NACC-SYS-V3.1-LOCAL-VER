"""ai_eval sends real records only to a model that stays on the agency's machine."""
from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from assistant import services
from assistant.management.commands import ai_eval
from assistant.management.commands.ai_eval import (
    HOSTED_RECORDS_NEED_DEBUG, HOSTED_RECORDS_REFUSED, HOSTED_RECORDS_SKIPPED, Command)
from assistant.models import AssistantSetting

RECORD = ("brief", "case_brief", "polish", "summary")


def hosted():
    return services.OpenAICompatibleClient("https://api.example.test/v1", "m", "t")


class HostedEvalTest(TestCase):
    def setUp(self):
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()

    def run_eval(self, *args, client=None, **options):
        out = StringIO()
        runners = {n: patch.object(Command, n, return_value=(n, 1, {}, 1))
                   for n in ("_briefs", "_case_briefs", "_polish", "_chat",
                             "_self_report", "_summaries")}
        started = {n: p.start() for n, p in runners.items()}
        for p in runners.values():
            self.addCleanup(p.stop)
        started["_summaries"].return_value = []
        with patch.object(ai_eval, "get_ai_client", return_value=client or hosted()), \
                patch.object(ai_eval, "probe_runtime", return_value=0) as probe:
            try:
                call_command("ai_eval", *args, stdout=out, **options)
            finally:
                self.probe, self.ran = probe, started
        return out.getvalue()

    def test_a_record_feature_asked_for_by_name_is_refused_before_anything_is_sent(self):
        for feature in RECORD:
            with self.subTest(feature):
                with self.assertRaises(CommandError) as ctx:
                    self.run_eval("--feature", feature)
                self.assertEqual(HOSTED_RECORDS_REFUSED, str(ctx.exception))
                self.probe.assert_not_called()

    def test_all_runs_only_the_fixed_chat_and_self_report_cases(self):
        out = self.run_eval("--feature", "all")
        self.assertIn(HOSTED_RECORDS_SKIPPED, out)
        for name in ("_chat", "_self_report"):
            self.ran[name].assert_called_once()
        for name in ("_briefs", "_case_briefs", "_polish", "_summaries"):
            self.ran[name].assert_not_called()

    def test_chat_and_self_report_by_name_run(self):
        for feature, name in (("chat", "_chat"), ("self_report", "_self_report")):
            self.run_eval("--feature", feature)
            self.ran[name].assert_called_once()

    def test_the_override_needs_debug(self):
        for feature in ("brief", "chat"):
            with self.assertRaises(CommandError) as ctx:
                self.run_eval("--feature", feature, "--allow-hosted-records")
            self.assertEqual(HOSTED_RECORDS_NEED_DEBUG, str(ctx.exception))

    @override_settings(DEBUG=True)
    def test_the_override_with_debug_runs_everything(self):
        self.run_eval("--feature", "all", "--allow-hosted-records")
        for name in ("_briefs", "_case_briefs", "_polish", "_chat", "_self_report",
                     "_summaries"):
            self.ran[name].assert_called_once()

    def test_a_local_model_is_not_affected(self):
        local = services.OllamaClient("http://localhost:11434", "m")
        self.run_eval("--feature", "all", client=local)
        for name in self.ran:
            self.ran[name].assert_called_once()
        self.run_eval("--feature", "brief", "--allow-hosted-records", client=local)

    def test_each_refusal_is_one_sentence(self):
        for text in (HOSTED_RECORDS_REFUSED, HOSTED_RECORDS_NEED_DEBUG,
                     HOSTED_RECORDS_SKIPPED):
            self.assertTrue(text.endswith("."))
            self.assertNotIn(". ", text)
