"""What the chat panel is built on, server side (Oct 2026).

* "Tell me about <child>" returns the brief's own facts, through the same scope
  and carry-history rules, and the record's social worker also gets what to do
  next.
* A chip may name a child by number; the model never can, and a number outside
  the asker's scope is "no such child".
* Every starter question the panel offers is a call the server accepts and
  answers for that role, without the model.
* The follow-up door runs the care-gap, flag, unassigned and concern tools under
  the same scope as a typed question.
* Every 503 carries a fixed reason code, never worked out from the sentence.
"""
from unittest.mock import patch

from django.test import override_settings
from rest_framework.test import APIRequestFactory

from assistant import services, tools
from assistant.models import AssistantJob, AssistantSetting
from assistant.tests.test_brief_facts import FactsFixture
from assistant.tests.test_role_access import HOSTED
from children.models import Child
from clinical.models import ProblemEntry, RemarkNote

ASK = "/api/assistant/ask/"
FOLLOWUP = "/api/assistant/followup/"
CAPS = "/api/assistant/capabilities/"


class PanelFixture(FactsFixture):
    def _follow(self, user, tool, args, label="A question"):
        self.client.force_authenticate(user)
        return self.client.post(FOLLOWUP, {"tool": tool, "args": args, "label": label},
                                format="json")

    def _summary(self, user, child=None):
        res = self._follow(user, "get_child_summary",
                           {"child_id": (child or self.child).id}, "Tell me about this child")
        self.assertEqual(200, res.status_code, res.data)
        return res.data["result"]


class SummaryFactsTest(PanelFixture):
    def test_the_summary_is_the_briefs_facts_per_role(self):
        for user, kind in ((self.psy, "clinical"), (self.sw, "case"), (self.admin, "case")):
            with self.subTest(user=user.email):
                result = self._summary(user)
                self.assertEqual("one", result["match"])
                self.assertEqual(kind, result["facts"]["kind"])
                # The very dict the brief panel is given (BriefFactsView).
                self.client.force_authenticate(user)
                facts = self.client.get(
                    f"/api/assistant/brief/child/{self.child.id}/facts/").json()
                self.assertEqual(facts, result["facts"])
                self.assertEqual(f"/report/child/{self.child.id}", result["link"]["path"])

    def test_what_to_do_next_is_only_the_record_s_own_social_workers(self):
        self.assertIn("next_steps", self._summary(self.sw)["facts"])
        self.assertNotIn("next_steps", self._summary(self.admin)["facts"])
        self.assertNotIn("next_steps", self._summary(self.psy)["facts"])

    def test_the_carry_history_control_still_hides_a_previous_psychologists_notes(self):
        RemarkNote.objects.create(child=self.child, author=self.previous, text="Earlier note")
        RemarkNote.objects.create(child=self.child, author=self.psy, text="My own note")
        texts = [r["text"] for r in self._summary(self.psy)["remarks"]]
        self.assertIn("My own note", texts)
        self.assertNotIn("Earlier note", texts)

    def test_a_treatment_plan_from_before_is_hidden_the_same_way(self):
        from clinical.models import TreatmentPlan
        TreatmentPlan.objects.create(child=self.child, author=self.previous,
                                     objectives="Earlier plan", status=TreatmentPlan.ACTIVE)
        self.assertIsNone(self._summary(self.psy)["facts"]["treatment_plan"])

    def test_the_summary_answer_holds_no_self_report_words(self):
        self._flag(answer="Lagi akong umiiyak sa gabi.")
        body = str(self._summary(self.sw)) + str(self._summary(self.psy))
        self.assertNotIn("umiiyak", body)


class ChildNumberTest(PanelFixture):
    def test_a_chip_names_a_child_by_number_within_scope(self):
        self.assertEqual("Maria Santos", self._summary(self.psy)["child"]["name"])

    def test_another_social_workers_child_is_not_found(self):
        for user in (self.sw, self.psy):
            with self.subTest(user=user.email):
                result = self._summary(user, self.other_child)
                self.assertEqual("none", result["match"])
                self.assertNotIn("child", result)
                self.assertNotIn("facts", result)

    def test_the_isa_reaches_both(self):
        self.assertEqual("one", self._summary(self.admin, self.other_child)["match"])

    def test_the_care_gaps_of_one_child_are_scoped_the_same_way(self):
        mine = self._follow(self.sw, "list_care_gaps", {"child_id": self.child.id}).data["result"]
        self.assertEqual([self.child.id], [g["child_id"] for g in mine["groups"]])
        theirs = self._follow(self.sw, "list_care_gaps",
                              {"child_id": self.other_child.id}).data["result"]
        self.assertEqual([], theirs["groups"])
        self.assertEqual(0, theirs["total"])

    def test_a_typed_question_cannot_pass_one(self):
        # The model returns a child number: it is discarded, and with no name
        # the call is refused as the model's own would be.
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_child_summary", {"child_id": self.child.id})):
            res = self.client.post(ASK, {"question": "tell me about her"}, format="json")
        self.assertEqual(200, res.status_code)
        self.assertFalse(res.data["ok"])
        self.assertNotIn("result", res.data)

    def test_a_typed_question_that_also_names_one_is_looked_up_by_the_name(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_child_summary",
                                        {"name": "Maria Santos", "child_id": self.other_child.id})):
            res = self.client.post(ASK, {"question": "tell me about Maria Santos"}, format="json")
        self.assertEqual("Maria Santos", res.data["result"]["child"]["name"])

    def test_a_chip_is_stored_without_the_name(self):
        self._follow(self.psy, "get_child_summary", {"child_id": self.child.id},
                     "Maria Santos (C-0001)")
        job = AssistantJob.objects.get()
        self.assertNotIn("Maria", job.input_ref + job.output_text)
        self.assertNotIn("Santos", job.input_ref + job.output_text)

    def test_the_number_must_be_a_number(self):
        res = self._follow(self.psy, "get_child_summary", {"child_id": "Maria"})
        self.assertEqual(400, res.status_code)


class FollowupToolsTest(PanelFixture):
    def test_the_starter_tools_are_allowed(self):
        for tool in ("list_care_gaps", "list_self_report_flags", "list_unassigned_children",
                     "search_children_by_concern", "get_child_summary"):
            self.assertIn(tool, tools.FOLLOWUP_TOOLS)
        for tool in ("count_people", "find_availability", "answer_directly"):
            self.assertEqual(tool in tools.FOLLOWUP_TOOLS, tool == "find_availability")

    def test_care_gaps_stay_scoped(self):
        for user, mine in ((self.psy, {"Maria Santos"}), (self.sw, {"Maria Santos"}),
                           (self.admin, {"Maria Santos", "Juan Dela Cruz"})):
            with self.subTest(user=user.email):
                groups = self._follow(user, "list_care_gaps", {}).data["result"]["groups"]
                self.assertEqual(mine, {g["child"] for g in groups})

    def test_flags_stay_scoped(self):
        self._flag()
        mine = self._follow(self.psy, "list_self_report_flags", {}).data["result"]
        self.assertEqual(1, mine["total"])
        previous = self._follow(self.previous, "list_self_report_flags", {}).data["result"]
        self.assertEqual(0, previous["total"])

    def test_unassigned_children_stay_scoped(self):
        Child.objects.create(fullname="Ana Unassigned", social_worker=self.sw)
        Child.objects.create(fullname="Beth Unassigned", social_worker=self.other_sw)
        names = lambda user: [c["name"] for c in self._follow(  # noqa: E731
            user, "list_unassigned_children", {}).data["result"]["items"]]
        self.assertEqual(["Ana Unassigned"], names(self.sw))
        self.assertEqual(["Ana Unassigned", "Beth Unassigned"], names(self.admin))
        self.assertEqual([], names(self.psy))

    def test_a_concern_search_by_chip_answers_without_the_model(self):
        ProblemEntry.objects.create(child=self.child, description="Sleep disturbance")
        with patch.object(services.OllamaClient, "choose_tool",
                          side_effect=AssertionError("the model was called")):
            res = self._follow(self.psy, "search_children_by_concern", {"concern": "sleep"})
        self.assertEqual([{"id": self.child.id, "name": "Maria Santos",
                           "problems": ["Sleep disturbance"]}], res.data["result"]["items"])

    def test_a_search_that_finds_nothing_offers_the_recorded_concerns_as_chips(self):
        ProblemEntry.objects.create(child=self.child, description="Sleep disturbance")
        ProblemEntry.objects.create(child=self.child, description="Separation anxiety")
        res = self._follow(self.psy, "search_children_by_concern", {"concern": "zzzz nothing"})
        self.assertEqual(["Separation anxiety", "Sleep disturbance"],
                         res.data["result"]["available"])
        chips = res.data["followups"]
        self.assertEqual(["Separation anxiety", "Sleep disturbance"], [c["label"] for c in chips])
        # Choosing one finds the child, with no model in between.
        chosen = self._follow(self.psy, chips[1]["tool"], chips[1]["args"], chips[1]["label"])
        self.assertEqual([self.child.id], [i["id"] for i in chosen.data["result"]["items"]])
        # And it is scoped: another psychologist is offered, and finds, nothing.
        other = self._follow(self.previous, "search_children_by_concern", {"concern": "zzzz"})
        self.assertEqual([], other.data["result"]["available"])

    def test_the_old_refusals_still_stand(self):
        self.assertEqual(400, self._follow(self.psy, "count_people", {"role": "staff"}).status_code)
        self.assertEqual(400, self._follow(self.psy, "answer_directly", {}).status_code)
        self.assertEqual(0, AssistantJob.objects.count())


class StarterQuestionsTest(PanelFixture):
    def test_every_example_validates_and_resolves_for_its_role(self):
        factory = APIRequestFactory()
        for user in (self.psy, self.sw, self.admin):
            role = user.role.role_name
            self.client.force_authenticate(user)
            examples = self.client.get(CAPS).data["examples"]
            self.assertEqual(4, len(examples), role)
            for ex in examples:
                with self.subTest(role=role, label=ex["label"]):
                    self.assertEqual({"label", "tool", "args"}, set(ex))
                    call = tools.validate(ex["tool"], ex["args"])
                    self.assertTrue(call.ok, call.error)
                    self.assertEqual(call.args, ex["args"])        # the validated args
                    self.assertIn(ex["tool"], tools.FOLLOWUP_TOOLS)
                    request = factory.get(ASK)
                    request.user = user
                    result = tools.REGISTRY[ex["tool"]]["resolve"](request, call.args)
                    self.assertIn("kind", result)
                    # And through the endpoint a chip uses, with no model.
                    with patch.object(services.OllamaClient, "choose_tool",
                                      side_effect=AssertionError("the model was called")):
                        res = self._follow(user, ex["tool"], ex["args"], ex["label"])
                    self.assertEqual(200, res.status_code, res.data)
                    self.assertTrue(res.data["ok"])

    def test_a_social_workers_first_starter_is_their_to_do_list(self):
        self.client.force_authenticate(self.sw)
        first = self.client.get(CAPS).data["examples"][0]
        self.assertEqual(("What do I need to do?", "list_care_gaps"),
                         (first["label"], first["tool"]))

    def test_a_bad_table_entry_is_a_loud_error_not_a_quiet_gap(self):
        with patch.dict(tools._EXAMPLES, {"Staff": [("x", "get_statistics", {"measure": "salaries"})]}):
            with self.assertRaises(ValueError):
                tools.capability_examples("Staff")

    def test_the_social_workers_capability_text_mentions_the_flags(self):
        self.assertIn("own words", tools.capability_text("Staff"))


class CapabilitiesEnabledTest(PanelFixture):
    def test_enabled_follows_the_administrators_switch_for_everyone(self):
        for user in (self.psy, self.sw, self.admin):
            self.client.force_authenticate(user)
            self.assertIs(True, self.client.get(CAPS).data["enabled"])
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        for user in (self.psy, self.sw, self.admin):
            self.client.force_authenticate(user)
            res = self.client.get(CAPS)
            self.assertEqual(200, res.status_code)
            self.assertIs(False, res.data["enabled"])


class ReasonCodeTest(PanelFixture):
    def test_switched_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.client.force_authenticate(self.psy)
        for res in (self.client.post(ASK, {"question": "hello"}, format="json"),
                    self._follow(self.psy, "list_care_gaps", {}),
                    self.client.post(f"/api/assistant/brief/child/{self.child.id}/")):
            self.assertEqual(503, res.status_code)
            self.assertEqual("switched_off", res.data["reason"])

    def test_the_runtime_not_answering(self):
        err = services.AIUnavailable("Local AI runtime unreachable: <urlopen error 111>")
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "choose_tool", side_effect=err):
            res = self.client.post(ASK, {"question": "hello"}, format="json")
        self.assertEqual(503, res.status_code)
        self.assertEqual("runtime_unreachable", res.data["reason"])

    def test_the_code_is_not_read_from_the_sentence(self):
        # Words that look like another reason in the runtime's own error text
        # change nothing: the code is set where the exception is raised.
        err = services.AIUnavailable("The assistant is switched off. hosted model refused")
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "choose_tool", side_effect=err):
            res = self.client.post(ASK, {"question": "hello"}, format="json")
        self.assertEqual("runtime_unreachable", res.data["reason"])

    def test_the_runtime_unreachable_from_the_hosted_client_too(self):
        client = services.OpenAICompatibleClient("https://api.example.invalid/v1", "m", "t")
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            with self.assertRaises(services.AIUnavailable) as caught:
                client.choose_tool("hi", [], "sys")
        self.assertEqual("runtime_unreachable", caught.exception.reason)

    @override_settings(**HOSTED)
    def test_a_drafting_door_on_a_hosted_deployment(self):
        self.client.force_authenticate(self.psy)
        res = self.client.post(f"/api/assistant/brief/child/{self.child.id}/")
        self.assertEqual(503, res.status_code)
        self.assertEqual("hosted_not_allowed", res.data["reason"])
        res = self.client.post("/api/assistant/prefetch-briefs/")
        self.assertEqual("hosted_not_allowed", res.data["reason"])

    def test_a_remote_runtime_address_that_is_not_used(self):
        cfg = AssistantSetting.load()
        cfg.ollama_url = "http://models.example.invalid:11434"
        cfg.save()
        self.client.force_authenticate(self.psy)
        res = self.client.post(ASK, {"question": "hello"}, format="json")
        self.assertEqual(503, res.status_code)
        self.assertEqual("runtime_unreachable", res.data["reason"])
