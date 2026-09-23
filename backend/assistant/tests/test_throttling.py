"""One account must not be able to spend the day's model allowance.

Every generation takes a process-wide lock, and a hosted allowance is finite —
Cloudflare Workers AI gives 10,000 requests a day across the whole account.
Without a ceiling, one signed-in visitor on a public demo can hold that lock
continuously, make the assistant unusable for everyone else, and exhaust the
quota in minutes.

The rates are deliberately tiny here so the tests are fast; production values
come from the environment.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from accounts.models import Role
from assistant import services
from assistant.models import AssistantSetting

User = get_user_model()
URL = "/api/assistant/ask/"

# DRF binds THROTTLE_RATES as a class attribute when rest_framework.throttling
# is imported, so override_settings(REST_FRAMEWORK=...) cannot reach it. The
# class attribute is what has to be patched.
TINY_RATES = {"assistant_chat": "3/hour", "assistant_draft": "3/hour"}


@patch.object(SimpleRateThrottle, "THROTTLE_RATES", TINY_RATES)
class ChatThrottleTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=self.role)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.psy)

    def tearDown(self):
        cache.clear()

    def _ask(self):
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_statistics", {"status": "active"})):
            return self.client.post(URL, {"question": "how many?"}, format="json")

    def test_allows_questions_up_to_the_ceiling(self):
        for _ in range(3):
            self.assertEqual(200, self._ask().status_code)

    def test_refuses_once_over_the_ceiling(self):
        for _ in range(3):
            self._ask()
        self.assertEqual(429, self._ask().status_code)

    def test_a_second_account_is_unaffected(self):
        # Per account, so one visitor cannot silence everyone else.
        for _ in range(4):
            self._ask()
        other = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234",
            role=self.role)
        self.client.force_authenticate(other)
        self.assertEqual(200, self._ask().status_code)


@patch.object(SimpleRateThrottle, "THROTTLE_RATES", TINY_RATES)
class DraftThrottleTest(APITestCase):
    """Briefs, polish and summaries share their own, separate ceiling — a
    psychologist drafting all afternoon should not lose the chatbot."""

    def setUp(self):
        cache.clear()
        role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=role)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.psy)

    def tearDown(self):
        cache.clear()

    def _polish(self):
        with patch.object(services.OllamaClient, "generate",
                          return_value="A polished note."):
            return self.client.post("/api/assistant/polish-remark/",
                                    {"text": "Settling in well."}, format="json")

    def test_drafting_has_its_own_ceiling(self):
        for _ in range(3):
            self._polish()
        self.assertEqual(429, self._polish().status_code)

    def test_exhausting_drafting_leaves_the_chatbot_working(self):
        for _ in range(4):
            self._polish()
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_statistics", {"status": "active"})):
            res = self.client.post(URL, {"question": "how many?"}, format="json")
        self.assertEqual(200, res.status_code)
