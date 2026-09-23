"""How a chat turn ended, recorded on its log row.

The row used to hold the question and the tool call, never what the tool
found - so a lookup that routed perfectly and came back empty was
indistinguishable from a good answer in real use. These pin what each kind of
turn records.
"""
import importlib
from unittest.mock import patch

from django.test import SimpleTestCase

from assistant import services, tools
from assistant.models import AssistantJob
from assistant.tests.test_ask import AskTestBase


class ResultSizeTest(SimpleTestCase):
    """One definition of "found nothing", shared by the log and ai_eval."""

    def test_a_reply_that_is_not_a_lookup_has_no_size(self):
        # Counting "Hello" as 0 would file every greeting under empty answers.
        self.assertIsNone(tools.result_size({"kind": "message", "text": "Hello"}))

    def test_a_found_child_is_one_answer_not_zero(self):
        # The summary has no `items` when it finds exactly one child, so an
        # item count read a complete answer as found nothing.
        self.assertEqual(1, tools.result_size(
            {"kind": "summary", "match": "one", "child": {"name": "Maria"}}))

    def test_several_matches_count_the_candidates(self):
        self.assertEqual(2, tools.result_size(
            {"kind": "summary", "match": "several", "items": [{}, {}]}))

    def test_no_matching_child_is_empty(self):
        self.assertEqual(0, tools.result_size({"kind": "summary", "match": "none"}))

    def test_a_count_is_its_own_size_including_zero(self):
        self.assertEqual(0, tools.result_size({"kind": "count", "count": 0}))
        self.assertEqual(40, tools.result_size({"kind": "people_count", "count": 40}))

    def test_a_paged_list_reports_its_real_total(self):
        # 25 rows shown of 40 found is 40, not 25.
        self.assertEqual(40, tools.result_size(
            {"kind": "appointments", "total": 40, "items": [{}] * 25}))

    def test_an_unpaged_list_counts_its_items(self):
        self.assertEqual(3, tools.result_size({"kind": "children", "items": [{}] * 3}))


class AnswerRecordedTest(AskTestBase):
    def _job(self):
        return AssistantJob.objects.get()

    def test_a_lookup_that_found_something(self):
        res = self._ask("how many children do I have?",
                        "get_statistics", {"status": "active"})
        job = self._job()
        self.assertEqual(AssistantJob.DATA, job.answer)
        self.assertEqual(1, job.result_count)
        # The panel sends feedback against this row.
        self.assertEqual(job.id, res.data["job"])

    def test_a_lookup_that_found_nothing_is_recorded_as_zero(self):
        # The case this exists for: routed right, answered with nothing.
        self._ask("any children with nightmares?",
                  "search_children_by_concern", {"concern": "nightmares"})
        job = self._job()
        self.assertEqual(AssistantJob.DATA, job.answer)
        self.assertEqual(0, job.result_count)

    def test_no_tool_fitting_is_recorded_as_declined(self):
        self._ask("what is the weather?", "answer_directly", {"reason": "unsupported"})
        job = self._job()
        self.assertEqual(AssistantJob.DECLINED, job.answer)
        self.assertIsNone(job.result_count)

    def test_a_missing_reason_is_declined_like_the_resolver_treats_it(self):
        # answer_directly's resolver defaults `reason` to unsupported; the log
        # must agree, or it records a greeting the user never saw.
        self._ask("what is the weather?", "answer_directly", {})
        self.assertEqual(AssistantJob.DECLINED, self._job().answer)

    def test_a_greeting_is_recorded_as_a_greeting(self):
        self._ask("Salamat po!", "answer_directly", {"reason": "greeting_or_closing"})
        self.assertEqual(AssistantJob.GREETING, self._job().answer)

    def test_a_request_to_change_something_is_recorded_as_an_action(self):
        # Routed to a data tool by the model, downgraded by the guard; the log
        # records what the user actually got.
        self._ask("book Ana for Friday", "list_my_appointments", {"when": "this_week"})
        self.assertEqual(AssistantJob.ACTION, self._job().answer)

    def test_a_call_the_validator_refused_is_not_understood(self):
        self._ask("appointments on the moon", "list_my_appointments", {"when": "moonday"})
        job = self._job()
        self.assertEqual(AssistantJob.NOT_UNDERSTOOD, job.answer)
        self.assertIsNone(job.result_count)

    def test_a_resolver_that_raised_is_failed(self):
        with patch.dict(tools.REGISTRY["get_statistics"],
                        {"resolve": lambda *a, **k: 1 / 0}):
            self._ask("how many children do I have?",
                      "get_statistics", {"status": "active"})
        job = self._job()
        self.assertEqual(AssistantJob.FAILED, job.answer)
        self.assertIsNone(job.result_count)

    def test_an_unreachable_runtime_is_failed(self):
        err = services.AIUnavailable("Local AI runtime unreachable: refused")
        with patch.object(services.OllamaClient, "choose_tool", side_effect=err):
            self.client.post("/api/assistant/ask/", {"question": "hi"}, format="json")
        self.assertEqual(AssistantJob.FAILED, self._job().answer)


class BackfillClassifierTest(SimpleTestCase):
    """The migration classifies rows logged before `answer` existed, from the
    fixed shape AssistantAskView always wrote them in."""

    classify = staticmethod(importlib.import_module(
        "assistant.migrations.0005_assistantjob_answer_result_count").classify)

    def test_each_shape_of_row(self):
        c = self.classify
        self.assertEqual("data", c(True, "count_my_children({'status': 'active'})", ""))
        self.assertEqual("declined", c(True, "answer_directly({'reason': 'unsupported'})", ""))
        self.assertEqual("declined", c(True, "answer_directly({})", ""))
        self.assertEqual("greeting", c(True, "answer_directly({'reason': 'greeting_or_closing'})", ""))
        self.assertEqual("action", c(True, "answer_directly({'reason': 'action_request'})", ""))
        self.assertEqual("not_understood",
                         c(False, "list_my_appointments({})", "'when' is required."))
        self.assertEqual("failed", c(False, "count_my_children({'status': 'active'})",
                                     "resolver failed: count_my_children"))
        # The runtime was down: nothing was chosen, so nothing was written.
        self.assertEqual("failed", c(False, "", "Local AI runtime unreachable"))
