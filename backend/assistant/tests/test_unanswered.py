"""The unanswered-questions list: what to teach the assistant next.

Built from real use, so these pin what counts as unanswered, what is merely
counted, and what the administrator reading it is and is not shown.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child

User = get_user_model()
URL = "/api/assistant/unanswered/"


class UnansweredBase(APITestCase):
    def setUp(self):
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=admin_role, first_name="Ada", last_name="Lim")
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=psy_role, first_name="Pia", last_name="Reyes")
        self.client.force_authenticate(self.admin)

    def _chat(self, question, answer, count=None, outcome=AssistantJob.PENDING,
              by=None, days_ago=0):
        job = AssistantJob.objects.create(
            job_type="chat", input_ref=question, answer=answer,
            result_count=count, outcome=outcome, created_by=by or self.psy)
        if days_ago:
            AssistantJob.objects.filter(pk=job.pk).update(
                created_at=timezone.now() - timedelta(days=days_ago))
        return job

    def _more_people(self, role_name, count, status=None):
        """Accounts of a role, so that role is shared by enough people to be
        named. `status` makes them something other than active."""
        role = Role.objects.get(role_name=role_name)
        made = []
        for n in range(count):
            email = f"{role_name.lower()}{n}{status or ''}@racco1.gov.ph"
            made.append(User.objects.create_user(
                email=email, username=email, password="pass1234", role=role,
                **({"status": status} if status else {})))
        return made

    def _get(self):
        res = self.client.get(URL)
        self.assertEqual(200, res.status_code)
        return res.data


class WhatIsListedTest(UnansweredBase):
    def test_each_kind_of_miss_is_listed_with_its_reason(self):
        self._chat("How many sessions were no-shows?", AssistantJob.DECLINED)
        self._chat("appointments on the moon", AssistantJob.NOT_UNDERSTOOD)
        self._chat("Any children with nightmares?", AssistantJob.DATA, count=0)
        self._chat("Who needs follow-up?", AssistantJob.DATA, count=4,
                   outcome=AssistantJob.DISCARDED)
        why = {q["question"]: q["why"] for q in self._get()["questions"]}
        self.assertEqual({
            "How many sessions were no-shows?": "declined",
            "appointments on the moon": "not_understood",
            "Any children with nightmares?": "empty",
            "Who needs follow-up?": "not_helpful",
        }, why)

    def test_what_is_not_a_question_to_learn_is_not_listed(self):
        # Answered and unrated or helpful; a greeting; a request to change
        # something; a runtime outage. Counted, never listed.
        self._chat("How many children do I have?", AssistantJob.DATA, count=14)
        self._chat("Who is free tomorrow?", AssistantJob.DATA, count=3,
                   outcome=AssistantJob.ACCEPTED)
        self._chat("Salamat po!", AssistantJob.GREETING)
        self._chat("book Ana for Friday", AssistantJob.ACTION)
        self._chat("Who am I seeing today?", AssistantJob.FAILED)
        self.assertEqual([], self._get()["questions"])

    def test_the_mechanical_reason_wins_over_the_verdict(self):
        # Empty AND marked not helpful: "found nothing" says what to fix.
        self._chat("Any children with nightmares?", AssistantJob.DATA, count=0,
                   outcome=AssistantJob.DISCARDED)
        self.assertEqual("empty", self._get()["questions"][0]["why"])

    def test_a_lookup_from_before_sizes_were_recorded_is_not_called_empty(self):
        # Null means unknown. Listing it as "found nothing" would be a claim
        # nobody can support.
        self._chat("Who needs follow-up?", AssistantJob.DATA, count=None)
        data = self._get()
        self.assertEqual([], data["questions"])
        self.assertEqual(1, data["breakdown"]["unmeasured"])
        self.assertEqual(0, data["breakdown"]["empty"])


class GroupingTest(UnansweredBase):
    def test_the_same_question_is_one_row_with_a_count(self):
        # Case, spacing and a trailing question mark are not a new question.
        self._chat("How many no-shows last month?", AssistantJob.DECLINED, days_ago=3)
        self._chat("how many  no-shows last month", AssistantJob.DECLINED, days_ago=2)
        self._chat("HOW MANY NO-SHOWS LAST MONTH?!", AssistantJob.DECLINED, days_ago=1)
        rows = self._get()["questions"]
        self.assertEqual(1, len(rows))
        self.assertEqual(3, rows[0]["times"])
        # The wording kept is the most recent one.
        self.assertEqual("HOW MANY NO-SHOWS LAST MONTH?!", rows[0]["question"])

    def test_most_asked_first(self):
        self._chat("Asked once", AssistantJob.DECLINED)
        for _ in range(3):
            self._chat("Asked three times", AssistantJob.DECLINED)
        self.assertEqual(["Asked three times", "Asked once"],
                         [q["question"] for q in self._get()["questions"]])

    def test_the_window_is_thirty_days(self):
        self._chat("An old question", AssistantJob.DECLINED, days_ago=45)
        data = self._get()
        self.assertEqual([], data["questions"])
        self.assertEqual(0, data["breakdown"]["total"])

    def test_the_list_is_capped_and_says_how_many_there_were(self):
        from assistant.views import AssistantUnansweredView
        cap = AssistantUnansweredView.LIST_LIMIT
        for n in range(cap + 2):
            self._chat(f"Distinct question {n}", AssistantJob.DECLINED)
        data = self._get()
        self.assertEqual(cap, len(data["questions"]))
        self.assertEqual(cap + 2, data["distinct"])


class PrivacyAndAccessTest(UnansweredBase):
    def test_roles_are_shown_never_people(self):
        # What the agency needs, not who asked it. A role is named once enough
        # accounts share it (see ThreeOfAKindTest); here each has one or two.
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.psy)
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.admin)
        self._more_people(Role.ADMINISTRATOR, 2)
        self._more_people(Role.PSYCHOLOGIST, 2)
        res = self.client.get(URL)
        self.assertEqual([Role.ADMINISTRATOR, Role.PSYCHOLOGIST],
                         res.data["questions"][0]["roles"])
        body = res.content.decode()
        # Names chosen so none is a substring of a role name, or the check
        # below would pass or fail on the role labels themselves.
        for private in ("p@racco1.gov.ph", "a@racco1.gov.ph", "Pia", "Reyes", "Ada", "Lim"):
            self.assertNotIn(private, body)

    def test_administrators_only(self):
        self.client.force_authenticate(self.psy)
        self.assertEqual(403, self.client.get(URL).status_code)

    def test_it_still_works_with_the_assistant_switched_off(self):
        # Reading history is how an administrator decides whether to turn the
        # assistant back on.
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertEqual(200, self.client.get(URL).status_code)


class EndToEndTest(UnansweredBase):
    def test_an_answer_rated_not_helpful_reaches_the_list(self):
        # Through the real endpoints: a psychologist asks, gets a full answer,
        # says it did not help, and the administrator sees it.
        Child.objects.create(fullname="Maria Santos", assigned_psychologist=self.psy)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_statistics", {"status": "active"})):
            asked = self.client.post("/api/assistant/ask/",
                                     {"question": "How many kids do I have?"},
                                     format="json")
        rated = self.client.post(f"/api/assistant/jobs/{asked.data['job']}/feedback/",
                                 {"outcome": "discarded"}, format="json")
        self.assertEqual(200, rated.status_code)

        self.client.force_authenticate(self.admin)
        data = self._get()
        self.assertEqual([("How many kids do I have?", "not_helpful")],
                         [(q["question"], q["why"]) for q in data["questions"]])
        self.assertEqual(1, data["breakdown"]["not_helpful"])
        self.assertEqual(1, data["breakdown"]["answered"])


class ThreeOfAKindTest(UnansweredBase):
    """"A psychologist asked this" says who it was when the agency has one
    psychologist. A role is named only once at least three active accounts
    hold it."""

    def _roles(self):
        return self._get()["questions"][0]["roles"]

    def test_a_role_with_fewer_than_three_people_is_a_colleague(self):
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.psy)
        self.assertEqual(["a colleague"], self._roles())
        self._more_people(Role.PSYCHOLOGIST, 1)                  # two psychologists
        self.assertEqual(["a colleague"], self._roles())

    def test_a_role_with_three_is_named(self):
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.psy)
        self._more_people(Role.PSYCHOLOGIST, 2)                  # three
        self.assertEqual([Role.PSYCHOLOGIST], self._roles())

    def test_only_active_accounts_count(self):
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.psy)
        self._more_people(Role.PSYCHOLOGIST, 1)
        self._more_people(Role.PSYCHOLOGIST, 3, status="archived")
        self._more_people(Role.PSYCHOLOGIST, 3, status="pending")
        self.assertEqual(["a colleague"], self._roles())

    def test_each_role_is_judged_on_its_own_size(self):
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.psy)
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.admin)
        self._more_people(Role.PSYCHOLOGIST, 2)                  # three psychologists
        self.assertEqual(["Psychologist", "a colleague"], sorted(self._roles()))

    def test_several_small_roles_are_still_one_colleague(self):
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.psy)
        self._chat("How many no-shows?", AssistantJob.DECLINED, by=self.admin)
        self.assertEqual(["a colleague"], self._roles())

    def test_an_account_that_has_gone_is_a_colleague_too(self):
        job = self._chat("How many no-shows?", AssistantJob.DECLINED)
        AssistantJob.objects.filter(pk=job.pk).update(created_by=None)
        self.assertEqual(["a colleague"], self._roles())

    def test_the_threshold_is_three(self):
        from assistant.views import AssistantUnansweredView
        self.assertEqual(3, AssistantUnansweredView.MIN_ROLE_SIZE)


class NoChildNameIsShownTest(UnansweredBase):
    """A question that resolved a child lookup names the child, so the log
    keeps it masked (assistant/redact.py) - and the list masks again what was
    written before that, because the ISA sees every child."""

    def setUp(self):
        super().setUp()
        Role.objects.get_or_create(role_name=Role.STAFF)
        self.sw = self._more_people(Role.STAFF, 1)[0]
        self.ana = Child.objects.create(first_name="Ana", last_name="Reyes",
                                        social_worker=self.sw)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()

    def _ask_as_sw(self, question):
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_child_summary", {"name": "CHILD"})):
            res = self.client.post("/api/assistant/ask/", {"question": question},
                                   format="json")
        self.assertEqual(200, res.status_code)
        self.client.force_authenticate(self.admin)

    def test_a_lookup_that_found_nothing_does_not_name_the_child(self):
        self._ask_as_sw("Tell me about Ana Reyes please")
        AssistantJob.objects.update(answer=AssistantJob.DATA, result_count=0)
        body = self.client.get(URL).content.decode()
        self.assertIn("Tell me about CHILD please", body)
        for name in ("Ana", "Reyes"):
            self.assertNotIn(name, body)

    def test_a_lookup_the_asker_marked_not_helpful_does_not_either(self):
        self._ask_as_sw("Tell me about Ana Reyes please")
        job = AssistantJob.objects.get()
        job.outcome = AssistantJob.DISCARDED
        job.save()
        body = self.client.get(URL).content.decode()
        self.assertIn("CHILD", body)
        self.assertNotIn("Reyes", body)

    def test_a_question_written_before_masking_is_masked_on_the_way_out(self):
        self._chat("Tell me about Ana Reyes, 0917 123 4567", AssistantJob.DATA, count=0)
        (row,) = self._get()["questions"]
        self.assertEqual("Tell me about CHILD, PHONE", row["question"])

    def test_the_same_child_asked_about_in_two_spellings_is_one_question(self):
        self._chat("Tell me about Ana Reyes", AssistantJob.DATA, count=0)
        self._chat("tell me about REYES, Ana?", AssistantJob.DATA, count=0)
        (row,) = self._get()["questions"]
        self.assertEqual(2, row["times"])
        self.assertEqual("tell me about CHILD?", row["question"])

    def test_two_different_questions_each_start_again_at_child(self):
        self._chat("Tell me about Ana Reyes", AssistantJob.DATA, count=0)
        self._chat("Is Ana Reyes ready for closure?", AssistantJob.DATA, count=0)
        questions = sorted(q["question"] for q in self._get()["questions"])
        self.assertEqual(["Is CHILD ready for closure?", "Tell me about CHILD"], questions)
