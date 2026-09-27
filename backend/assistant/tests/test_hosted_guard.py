"""A hosted model must be impossible to enable by accident.

The codebase says, deliberately: "There is deliberately only one provider. A
hosted API would mean sending clinical free text to an outside processor."
That rule is why the V2 AI layer was removed. Adding a hosted client puts the
capability back, so these tests are the lock that comes with it.
"""
from django.test import TestCase, override_settings

from assistant.models import AssistantSetting
from assistant.services import (NullClient, OllamaClient,
                                OpenAICompatibleClient, get_ai_client)

HOSTED = {
    "ASSISTANT_MODEL_URL": "https://api.example.invalid/v1",
    "ASSISTANT_MODEL_TOKEN": "a-token",
    "ASSISTANT_MODEL_NAME": "@cf/meta/llama-4-scout-17b-16e-instruct",
}


class HostedGuardTest(TestCase):
    def setUp(self):
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()

    def test_local_ollama_by_default(self):
        self.assertIsInstance(get_ai_client(allow_hosted=True), OllamaClient)

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=False, **HOSTED)
    def test_credentials_alone_are_not_consent(self):
        # Someone pasting a key into a dashboard has not decided that clinical
        # free text may leave the building.
        self.assertIsInstance(get_ai_client(allow_hosted=True), OllamaClient)

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=True, **HOSTED)
    def test_only_a_caller_that_asks_may_reach_it(self):
        # Every drafting feature calls without asking, and must be refused
        # rather than handed the hosted model OR the editable local URL.
        from assistant.services import AIUnavailable, HOSTED_DRAFTING_REFUSED
        client = get_ai_client()
        self.assertIsInstance(client, NullClient)
        with self.assertRaisesMessage(AIUnavailable, HOSTED_DRAFTING_REFUSED):
            client.generate("case notes")

    def test_without_a_hosted_model_drafting_is_local_as_before(self):
        self.assertIsInstance(get_ai_client(), OllamaClient)

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=True, **HOSTED)
    def test_the_explicit_flag_enables_it(self):
        client = get_ai_client(allow_hosted=True)
        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual("@cf/meta/llama-4-scout-17b-16e-instruct", client.model)

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=True,
                       ASSISTANT_MODEL_URL="https://api.example.invalid/v1",
                       ASSISTANT_MODEL_TOKEN="", ASSISTANT_MODEL_NAME="m")
    def test_the_flag_without_a_token_falls_back_to_local(self):
        self.assertIsInstance(get_ai_client(allow_hosted=True), OllamaClient)

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=True,
                       ASSISTANT_MODEL_URL="", ASSISTANT_MODEL_TOKEN="t",
                       ASSISTANT_MODEL_NAME="m")
    def test_the_flag_without_a_url_falls_back_to_local(self):
        self.assertIsInstance(get_ai_client(allow_hosted=True), OllamaClient)

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=True, **HOSTED)
    def test_the_administrator_switch_still_wins(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertIsInstance(get_ai_client(allow_hosted=True), NullClient)

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=True, **HOSTED)
    def test_the_database_url_cannot_redirect_a_hosted_model(self):
        # ollama_url is administrator-editable. On a public deployment, anyone
        # holding administrator credentials could otherwise repoint the model
        # at a host they control and capture every prompt.
        cfg = AssistantSetting.load()
        cfg.ollama_url = "http://attacker.invalid:11434"
        cfg.save()
        self.assertEqual("https://api.example.invalid/v1", get_ai_client(allow_hosted=True).base_url)

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=True, **HOSTED)
    def test_it_announces_itself_without_leaking_the_token(self):
        with self.assertLogs("assistant.services", level="INFO") as logs:
            get_ai_client(allow_hosted=True)
        joined = " ".join(logs.output)
        self.assertIn("llama-4-scout", joined)
        self.assertIn("api.example.invalid", joined)
        self.assertNotIn("a-token", joined)
