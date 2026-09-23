"""Follow-up chips: refinements offered by the server, run without the model.

They are not a new trust boundary - the model's output was always untrusted
input to the validator - so these pin that a chip goes through exactly the
same checks: only the tools chips come from, only arguments the schema
accepts, and scope from the caller, never from the call.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services, tools
from assistant.models import AssistantJob, AssistantSetting
from assistant.views import FOLLOWUP_MODEL
from children.models import Child
from scheduling.models import Appointment

User = get_user_model()
URL = "/api/assistant/followup/"


def labels(offers):
    return [o["label"] for o in offers]


class OffersTest(SimpleTestCase):
    def _offers(self, tool, args, result, role=Role.ADMINISTRATOR):
        return tools.followups(tools.validate(tool, args), result, role)

    def _stats(self, measure="children", by="none", period=None):
        return {"kind": "breakdown", "measure": measure, "by": by, "period": period}

    def test_a_child_count_offers_the_forms_breakdowns_first(self):
        self.assertEqual(["By case type?", "By age group?", "By sex?", "By psychologist?"],
                         labels(self._offers("get_statistics", {}, self._stats())))

    def test_a_psychologist_is_not_offered_a_one_row_breakdown_of_themselves(self):
        offers = self._offers("get_statistics", {}, self._stats(), role=Role.PSYCHOLOGIST)
        self.assertNotIn("By psychologist?", labels(offers))
        self.assertIn("By case stage?", labels(offers))

    def test_the_breakdown_already_shown_is_not_offered_again(self):
        offers = self._offers("get_statistics", {"by": "case_type"},
                              self._stats(by="case_type"))
        self.assertNotIn("By case type?", labels(offers))

    def test_a_new_period_keeps_the_breakdown(self):
        offers = self._offers("get_statistics", {"measure": "closures", "by": "reason"},
                              self._stats("closures", "reason"))
        this_year = next(o for o in offers if o["label"] == "This year?")
        self.assertEqual(("closures", "reason", "this_year"),
                         (this_year["args"]["measure"], this_year["args"]["by"],
                          this_year["args"]["period"]))

    def test_the_schedule_offers_the_periods_either_side(self):
        self.assertEqual(["Last week?", "Next week?"],
                         labels(self._offers("list_my_appointments", {"when": "this_week"},
                                             {"kind": "appointments"})))

    def test_flags_offer_the_reviewed_ones_and_wider_periods(self):
        self.assertEqual(["Include reviewed ones?", "This month?", "This year?"],
                         labels(self._offers("list_self_report_flags", {},
                                             {"kind": "self_report_flags"})))

    def test_several_matching_children_become_a_choice(self):
        result = {"kind": "summary", "match": "several",
                  "items": [{"id": 1, "name": "Maria Santos"}, {"id": 2, "name": "Maria Cruz"}]}
        offers = self._offers("get_child_summary", {"name": "Maria"}, result)
        self.assertEqual(["Maria Santos", "Maria Cruz"], labels(offers))
        self.assertEqual({"name": "Maria Santos"}, offers[0]["args"])

    def test_nothing_is_offered_after_a_greeting_or_a_refused_call(self):
        self.assertEqual([], self._offers("answer_directly", {}, {"kind": "message"}))
        refused = tools.validate("get_statistics", {"measure": "staff"})
        self.assertEqual([], tools.followups(refused, {"kind": "breakdown"}, Role.STAFF))

    def test_at_most_four_and_every_offer_is_a_call_the_validator_accepts(self):
        cases = [
            ("get_statistics", {}, self._stats()),
            ("get_statistics", {"measure": "closures"}, self._stats("closures")),
            ("get_statistics", {"measure": "intake"}, self._stats("intake")),
            ("list_my_appointments", {"when": "today"}, {"kind": "appointments"}),
            ("find_availability", {"when": "tomorrow"}, {"kind": "availability"}),
            ("list_self_report_flags", {"period": "this_month"}, {"kind": "self_report_flags"}),
        ]
        for tool, args, result in cases:
            for role in (Role.ADMINISTRATOR, Role.STAFF, Role.PSYCHOLOGIST):
                offers = self._offers(tool, args, result, role)
                self.assertLessEqual(len(offers), tools.FOLLOWUP_LIMIT)
                for o in offers:
                    self.assertTrue(tools.validate(o["tool"], o["args"]).ok, o)
                    self.assertIn(o["tool"], tools.FOLLOWUP_TOOLS)


class FollowupBase(APITestCase):
    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.other = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        Child.objects.create(fullname="Maria Santos", assigned_psychologist=self.psy,
                             case_type="Adoption")
        Child.objects.create(fullname="Juan Dela Cruz", assigned_psychologist=self.other,
                             case_type="Foster Care")
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.psy)

    def _follow(self, tool="get_statistics", args=None, label="By case type?"):
        return self.client.post(URL, {"tool": tool, "args": args if args is not None
                                      else {"by": "case_type"}, "label": label},
                                format="json")


class EndpointTest(FollowupBase):
    def test_it_answers_without_the_model(self):
        with patch.object(services.OllamaClient, "choose_tool",
                          side_effect=AssertionError("the model was called")):
            res = self._follow()
        self.assertEqual(200, res.status_code)
        self.assertEqual({"Adoption": 1},
                         {r["label"]: r["count"] for r in res.data["result"]["rows"]})

    def test_it_is_logged_like_any_turn_and_marked_as_a_follow_up(self):
        res = self._follow()
        job = AssistantJob.objects.get()
        self.assertEqual(("chat", "By case type?", FOLLOWUP_MODEL, AssistantJob.DATA, 1),
                         (job.job_type, job.input_ref, job.model_used, job.answer,
                          job.result_count))
        # It can be rated, and it offers its own refinements.
        self.assertEqual(job.id, res.data["job"])
        self.assertNotIn("By case type?", labels(res.data["followups"]))

    def test_scope_comes_from_the_caller_never_the_call(self):
        # An invented argument is discarded by the validator, as with the model.
        res = self._follow(args={"by": "none", "psychologist": self.other.id,
                                 "assigned_to_me": False})
        self.assertEqual(1, res.data["result"]["total"])


class RefusalTest(FollowupBase):
    def assertRefused(self, res):
        self.assertEqual(400, res.status_code)
        self.assertEqual(0, AssistantJob.objects.count())   # nothing ran, nothing logged

    def test_only_tools_that_chips_come_from(self):
        self.assertRefused(self._follow(tool="count_people", args={"role": "staff"}))
        self.assertRefused(self._follow(tool="answer_directly", args={}))
        self.assertRefused(self._follow(tool="not_a_tool", args={}))

    def test_arguments_the_schema_refuses(self):
        res = self._follow(args={"measure": "salaries"})
        self.assertRefused(res)
        self.assertIn("measure", res.data["detail"])

    def test_arguments_that_are_not_an_object(self):
        self.assertRefused(self._follow(args=["by", "sex"]))

    def test_a_label_is_required_and_bounded(self):
        self.assertRefused(self._follow(label=""))
        self.assertRefused(self._follow(label="x" * 1000))

    def test_off_when_the_assistant_is_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertEqual(503, self._follow().status_code)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertIn(self._follow().status_code, (401, 403))


class ChoiceTest(FollowupBase):
    """A "which one?" answer becomes chips, and a chip lands on one child."""

    def setUp(self):
        super().setUp()
        Child.objects.create(fullname="Maria Santos-Cruz", assigned_psychologist=self.psy)

    def test_an_exact_full_name_wins_over_a_longer_one_containing_it(self):
        res = self._follow(tool="get_child_summary", args={"name": "Maria Santos"},
                           label="Maria Santos")
        self.assertEqual(("one", "Maria Santos"),
                         (res.data["result"]["match"], res.data["result"]["child"]["name"]))

    def test_a_partial_name_still_asks_which_one_and_every_choice_lands(self):
        res = self._follow(tool="get_child_summary", args={"name": "Maria"}, label="Maria")
        self.assertEqual("several", res.data["result"]["match"])
        for chip in res.data["followups"]:
            picked = self._follow(tool=chip["tool"], args=chip["args"], label=chip["label"])
            self.assertEqual(("one", chip["label"]),
                             (picked.data["result"]["match"],
                              picked.data["result"]["child"]["name"]))

    def test_two_children_with_the_identical_name_are_not_guessed_between(self):
        Child.objects.create(fullname="Maria Santos", assigned_psychologist=self.psy)
        res = self._follow(tool="get_child_summary", args={"name": "Maria Santos"},
                           label="Maria Santos")
        self.assertEqual("several", res.data["result"]["match"])


class ThroughTheAskEndpointTest(FollowupBase):
    def test_an_answer_offers_chips_and_a_chip_runs(self):
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_statistics", {"measure": "children"})):
            asked = self.client.post("/api/assistant/ask/",
                                     {"question": "How many children do I have?"},
                                     format="json")
        chip = asked.data["followups"][0]
        self.assertEqual("By case type?", chip["label"])
        ran = self.client.post(URL, chip, format="json")
        self.assertEqual("children", ran.data["result"]["measure"])
        self.assertEqual("case_type", ran.data["result"]["by"])

    def test_chip_turns_stay_off_the_unanswered_card(self):
        # "This year?" means nothing out of context, and a refinement that
        # finds nothing is not a question anybody typed.
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        admin = User.objects.create_user(email="a@racco1.gov.ph", username="a",
                                         password="pass1234", role=admin_role)
        self._follow(args={"measure": "closures", "period": "this_year"}, label="This year?")
        self.assertEqual(0, AssistantJob.objects.get().result_count)     # empty, and logged
        self.client.force_authenticate(admin)
        card = self.client.get("/api/assistant/unanswered/").data
        self.assertEqual(([], 0), (card["questions"], card["breakdown"]["total"]))


class SchedulePeriodsTest(FollowupBase):
    def test_a_schedule_chip_is_scoped_like_the_schedule(self):
        from datetime import datetime, time, timedelta
        from django.utils import timezone
        day = timezone.localdate() + timedelta(days=1)
        start = timezone.make_aware(datetime.combine(day, time(12, 0)),
                                    timezone.get_current_timezone())
        for child, who in ((Child.objects.get(fullname="Maria Santos"), self.psy),
                           (Child.objects.get(fullname="Juan Dela Cruz"), self.other)):
            Appointment.objects.create(child=child, psychologist=who, start=start)
        res = self._follow(tool="list_my_appointments", args={"when": "tomorrow"},
                           label="Tomorrow?")
        self.assertEqual(("own", 1), (res.data["result"]["scope"], res.data["result"]["total"]))
