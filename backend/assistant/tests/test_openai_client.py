"""The hosted client speaks /v1/chat/completions.

Every test patches the transport. What matters here is the parsing: a spike
found that @cf/qwen/qwen3-30b-a3b-fp8 accepts the tools array and then returns
the call as raw <tool_call> text rather than in the structured field. A client
that silently treats that as "no tool call" answers every question with a
polite refusal while looking perfectly healthy.
"""
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from assistant.services import AIUnavailable, OpenAICompatibleClient


def _response(payload):
    class _Fake:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return _Fake()


class ChooseToolTest(SimpleTestCase):
    def setUp(self):
        self.client = OpenAICompatibleClient(
            "https://example.invalid/v1", "test-model", "token")

    def _call(self, payload):
        with patch("assistant.services.urllib.request.urlopen",
                   return_value=_response(payload)):
            return self.client.choose_tool("How many children?", [], "system")

    def test_reads_a_structured_tool_call(self):
        name, args = self._call({"choices": [{"message": {"tool_calls": [
            {"function": {"name": "get_statistics",
                          "arguments": '{"status": "active"}'}}]}}]})
        self.assertEqual("get_statistics", name)
        self.assertEqual({"status": "active"}, args)

    def test_accepts_arguments_already_parsed(self):
        name, args = self._call({"choices": [{"message": {"tool_calls": [
            {"function": {"name": "list_care_gaps", "arguments": {}}}]}}]})
        self.assertEqual("list_care_gaps", name)
        self.assertEqual({}, args)

    def test_unparseable_arguments_do_not_raise(self):
        # The validator downstream handles a missing argument; a 500 here would
        # turn a recoverable turn into an error page.
        name, args = self._call({"choices": [{"message": {"tool_calls": [
            {"function": {"name": "get_statistics",
                          "arguments": "not json at all"}}]}}]})
        self.assertEqual("get_statistics", name)
        self.assertEqual({}, args)

    def test_prose_instead_of_a_tool_call_returns_none(self):
        name, args = self._call({"choices": [{"message": {
            "content": "I think you have several children."}}]})
        self.assertIsNone(name)
        self.assertEqual({}, args)

    def test_a_tool_call_left_as_text_returns_none(self):
        # Exactly what qwen3-30b does. Not a tool call, and inventing one from
        # unparsed text would be worse than declining.
        name, _ = self._call({"choices": [{"message": {
            "content": '<tool_call>\n{"name": "get_statistics"}\n</tool_call>'}}]})
        self.assertIsNone(name)

    def test_an_empty_response_returns_none(self):
        name, _ = self._call({"choices": []})
        self.assertIsNone(name)


class GenerateTest(SimpleTestCase):
    def setUp(self):
        self.client = OpenAICompatibleClient(
            "https://example.invalid/v1", "test-model", "token")

    def test_returns_the_message_content(self):
        with patch("assistant.services.urllib.request.urlopen",
                   return_value=_response({"choices": [
                       {"message": {"content": "  a draft  "}}]})):
            self.assertEqual("a draft", self.client.generate("p", system="s"))

    def test_a_transport_failure_becomes_ai_unavailable(self):
        with patch("assistant.services.urllib.request.urlopen",
                   side_effect=OSError("refused")):
            with self.assertRaises(AIUnavailable):
                self.client.generate("p", system="s")

    def test_choose_tool_failure_becomes_ai_unavailable(self):
        with patch("assistant.services.urllib.request.urlopen",
                   side_effect=OSError("refused")):
            with self.assertRaises(AIUnavailable):
                self.client.choose_tool("q", [], "s")

    def test_the_token_is_sent_as_a_bearer_header(self):
        seen = {}

        def _capture(req, *a, **kw):
            seen["auth"] = req.get_header("Authorization")
            return _response({"choices": [{"message": {"content": "x"}}]})

        with patch("assistant.services.urllib.request.urlopen", _capture):
            self.client.generate("p", system="s")
        self.assertEqual("Bearer token", seen["auth"])
