"""A refusal that sent nothing is not a read of the child; a log line names no child."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services, tools
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child

User = get_user_model()
HOSTED = {"ASSISTANT_ALLOW_HOSTED_MODEL": True,
          "ASSISTANT_MODEL_URL": "https://api.example.invalid/v1",
          "ASSISTANT_MODEL_TOKEN": "t", "ASSISTANT_MODEL_NAME": "m"}


class RunJobAttributionTest(TestCase):
    def setUp(self):
        self.child = Child.objects.create(fullname="Maria Santos")
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()

    def run_it(self):
        with self.assertRaises(services.AIUnavailable):
            services.run_job("brief", "p", input_ref="child:1", child=self.child)
        return AssistantJob.objects.get()

    @override_settings(**HOSTED)
    def test_a_hosted_refusal_is_not_attributed(self):
        job = self.run_it()
        self.assertFalse(job.ok)
        self.assertIsNone(job.child)

    def test_a_switched_off_assistant_is_not_attributed(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertIsNone(self.run_it().child)

    def test_an_attempt_that_failed_is_attributed(self):
        err = services.AIUnavailable("Local AI runtime unreachable: refused")
        with patch.object(services.OllamaClient, "generate", side_effect=err):
            self.assertEqual(self.child, self.run_it().child)

    def test_a_success_is_attributed(self):
        with patch.object(services.OllamaClient, "generate", return_value="Draft."):
            _, job = services.run_job("brief", "p", child=self.child)
        self.assertEqual(self.child, job.child)


class ResolverLogTest(APITestCase):
    def test_the_failure_line_names_the_tool_and_not_the_arguments(self):
        role, _ = Role.objects.get_or_create(role_name=Role.STAFF)
        sw = User.objects.create_user(email="s@racco1.gov.ph", username="s",
                                      password="pass1234", role=role)
        Child.objects.create(first_name="Ana", last_name="Reyes", social_worker=sw)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(sw)
        spec = {**tools.REGISTRY["get_child_summary"], "resolve": lambda *a: 1 / 0}
        with patch.dict(tools.REGISTRY, {"get_child_summary": spec}), \
                patch.object(services.OllamaClient, "choose_tool",
                             return_value=("get_child_summary", {"name": "CHILD"})), \
                self.assertLogs("assistant.views", level="ERROR") as logs:
            res = self.client.post("/api/assistant/ask/",
                                   {"question": "Tell me about Ana Reyes"}, format="json")
        self.assertFalse(res.data["ok"])
        first = logs.records[0].getMessage()
        self.assertEqual("Chat resolver failed: get_child_summary", first)
        self.assertNotIn("Reyes", first)
