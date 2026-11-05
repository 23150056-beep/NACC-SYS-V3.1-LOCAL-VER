"""What the chatbot's model is told, and what the audit row keeps.

A typed question routinely names a child, and on the demo it goes to a hosted
model. These hold three things: the model is never given a name of a child the
asker can see (nor a phone number or an email address); the server puts the
name back so the lookup still finds the right child; and the audit row keeps
the masked question, so the ISA who reads it learns nothing about a record that
is not theirs.
"""
import json
import unicodedata
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import prompts, redact, services, tools
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child

User = get_user_model()
ASK = "/api/assistant/ask/"
FOLLOWUP = "/api/assistant/followup/"
HOSTED = {
    "ASSISTANT_ALLOW_HOSTED_MODEL": True,
    "ASSISTANT_MODEL_URL": "https://api.example.invalid/v1",
    "ASSISTANT_MODEL_TOKEN": "a-token",
    "ASSISTANT_MODEL_NAME": "@cf/meta/llama-4-scout-17b-16e-instruct",
}


def person(email, role_name):
    role, _ = Role.objects.get_or_create(role_name=role_name)
    return User.objects.create_user(email=email, username=email, password="pass1234",
                                    role=role)


def child(first, last, *, sw=None, psy=None, middle=""):
    return Child.objects.create(first_name=first, middle_name=middle, last_name=last,
                                social_worker=sw, assigned_psychologist=psy)


class Request:
    """The one thing the scope rule reads."""

    def __init__(self, user):
        self.user = user


class MaskingTest(TestCase):
    def setUp(self):
        self.sw = person("sw@racco1.gov.ph", Role.STAFF)
        self.other_sw = person("sw2@racco1.gov.ph", Role.STAFF)
        self.isa = person("isa@racco1.gov.ph", Role.ADMINISTRATOR)
        self.ana = child("Ana", "Reyes", sw=self.sw, middle="Marie")   # "Ana M. Reyes"
        self.pena = child("Joaquín", "Peña", sw=self.sw)
        self.theirs = child("Juan", "Magsaysay", sw=self.other_sw)
        self.request = Request(self.sw)

    def mask(self, text, request=None):
        return redact.mask(text, request or self.request)

    def test_a_full_name_becomes_a_token_and_the_record_goes_back(self):
        masked, mapping = self.mask("Tell me about Ana Reyes")
        self.assertEqual("Tell me about CHILD", masked)
        # The recorded name, so the lookup is exact.
        self.assertEqual({"CHILD": "Ana M. Reyes"}, mapping)

    def test_the_name_with_its_middle_name_is_masked_whole(self):
        self.assertEqual("Sino si CHILD?", self.mask("Sino si Ana Marie Reyes?")[0])
        self.assertEqual("Sino si CHILD?", self.mask("Sino si Ana M. Reyes?")[0])

    def test_a_middle_name_the_record_does_not_have_does_not_stay_behind(self):
        # Joaquín Peña has none recorded; someone types one anyway.
        self.assertEqual("Sino si CHILD?", self.mask("Sino si Joaquín Luis Peña?")[0])
        self.assertEqual("Sino si CHILD?", self.mask("Sino si Joaquín L. Peña?")[0])

    def test_case_and_accents_do_not_hide_a_name(self):
        for typed in ("joaquín peña", "JOAQUIN PENA", "Joaquín PEÑA", "joaquin Peña"):
            with self.subTest(typed):
                self.assertEqual("Tell me about CHILD",
                                 self.mask(f"Tell me about {typed}")[0])

    def test_a_decomposed_accent_goes_with_its_letter(self):
        # "ñ" typed as n + a combining tilde: nothing may be left behind.
        typed = unicodedata.normalize("NFD", "Joaquín Peña")
        self.assertNotEqual("Joaquín Peña", typed)
        self.assertEqual("Tell me about CHILD", self.mask(f"Tell me about {typed}")[0])
        self.assertEqual("CHILD?", self.mask(f"{typed}?")[0])

    def test_a_list_style_name_is_one_name(self):
        self.assertEqual("Tell me about CHILD", self.mask("Tell me about Reyes, Ana")[0])

    def test_a_last_name_alone_is_masked_a_first_name_alone_is_not(self):
        self.assertEqual("What about CHILD?", self.mask("What about Reyes?")[0])
        self.assertEqual("What about Ana?", self.mask("What about Ana?")[0])

    def test_two_children_are_told_apart(self):
        masked, mapping = self.mask("Compare Ana Reyes with Joaquín Peña")
        self.assertEqual("Compare CHILD 1 with CHILD 2", masked)
        self.assertEqual({"CHILD 1": "Ana M. Reyes", "CHILD 2": "Joaquín Peña"}, mapping)

    def test_the_same_child_twice_is_one_token(self):
        masked, mapping = self.mask("Ana Reyes, yes, ana reyes")
        self.assertEqual("CHILD, yes, CHILD", masked)
        self.assertEqual(1, len(mapping))

    def test_a_child_the_asker_cannot_see_is_not_recognised(self):
        # Magsaysay is another social worker's. Not masked, so the model is
        # never told it is a child, and the matcher cannot be used to ask
        # whether a name is one.
        masked, mapping = self.mask("Tell me about Juan Magsaysay")
        self.assertEqual("Tell me about Juan Magsaysay", masked)
        self.assertEqual({}, mapping)

    def test_the_matcher_reads_only_the_requesters_visible_children(self):
        with patch.object(redact, "visible_children",
                          wraps=redact.visible_children) as scope:
            redact.mask("Tell me about Ana Reyes", self.request)
        scope.assert_called_once_with(self.request)

    def test_each_role_knows_its_own_scope(self):
        # The ISA sees every record; the other social worker only theirs.
        self.assertEqual("CHILD", self.mask("Juan Magsaysay", Request(self.isa))[0])
        self.assertEqual("CHILD", self.mask("Juan Magsaysay", Request(self.other_sw))[0])
        self.assertEqual("Ana Reyes", self.mask("Ana Reyes", Request(self.other_sw))[0])
        psy = person("psy@racco1.gov.ph", Role.PSYCHOLOGIST)
        child("Rosa", "Villanueva", psy=psy)
        self.assertEqual("CHILD", self.mask("Rosa Villanueva", Request(psy))[0])
        self.assertEqual("Ana Reyes", self.mask("Ana Reyes", Request(psy))[0])

    def test_a_short_surname_is_masked_only_inside_a_full_name(self):
        # "Ang" is a surname and the Tagalog word for "the".
        child("Ana", "Ang", sw=self.sw)
        question = "Sino ang mga bata na may problema?"
        self.assertEqual(question, self.mask(question)[0])
        self.assertEqual("Sino si CHILD?", self.mask("Sino si Ana Ang?")[0])

    def test_a_surname_that_is_a_word_of_the_question_is_left_alone(self):
        child("Maria", "Bata", sw=self.sw)
        self.assertEqual("Ilan ang bata ko?", self.mask("Ilan ang bata ko?")[0])
        self.assertEqual("Sino si CHILD?", self.mask("Sino si Maria Bata?")[0])

    def test_a_surname_shared_by_two_children_goes_back_as_typed(self):
        child("Ben", "Reyes", sw=self.sw)
        masked, mapping = self.mask("About Reyes")
        self.assertEqual("About CHILD", masked)
        self.assertEqual({"CHILD": "Reyes"}, mapping)

    def test_a_record_with_only_the_composed_name_is_masked_too(self):
        Child.objects.create(fullname="Maria Santos", social_worker=self.sw)
        self.assertEqual("About CHILD", self.mask("About Maria Santos")[0])
        self.assertEqual("About CHILD", self.mask("About santos")[0])

    def test_a_name_inside_an_email_address_is_the_addresss(self):
        masked, mapping = self.mask("mail ana.reyes@example.com about Reyes")
        self.assertEqual("mail EMAIL about CHILD", masked)
        self.assertEqual("ana.reyes@example.com", mapping["EMAIL"])

    def test_philippine_mobile_numbers_in_the_shapes_people_type_them(self):
        for number in ("09171234567", "0917 123 4567", "0917-123-4567",
                       "+63 917 123 4567", "+639171234567", "639171234567",
                       "(0917) 123-4567", "917 123 4567"):
            with self.subTest(number):
                masked, mapping = self.mask(f"Text {number} please")
                self.assertEqual("Text PHONE please", masked)
                self.assertEqual({"PHONE": number}, mapping)

    def test_other_numbers_are_questions_not_phones(self):
        for text in ("How many in 2026?", "C-0049 and C-0050", "Ilan ang 12345678 ?",
                     "5 of 40 children"):
            with self.subTest(text):
                self.assertEqual(text, self.mask(text)[0])

    def test_two_numbers_are_told_apart(self):
        masked, _ = self.mask("0917 123 4567 or 0918 765 4321")
        self.assertEqual("PHONE 1 or PHONE 2", masked)

    def test_a_question_with_nothing_to_hide_is_returned_untouched(self):
        question = "Ilan ang mga bata ko ngayong buwan?"
        self.assertEqual((question, {}), self.mask(question))


class RestoringTest(TestCase):
    def setUp(self):
        self.sw = person("sw@racco1.gov.ph", Role.STAFF)
        child("Ana", "Reyes", sw=self.sw)
        self.masker = redact.Masker(Request(self.sw))
        self.masker.mask_text("About Ana Reyes")

    def test_a_token_in_an_argument_is_replaced_whole(self):
        self.assertEqual({"name": "Ana Reyes"}, self.masker.restore({"name": "CHILD"}))

    def test_the_model_writing_the_token_in_another_case_still_restores(self):
        self.assertEqual({"name": "Ana Reyes"}, self.masker.restore({"name": "Child"}))

    def test_the_word_child_inside_other_text_is_just_a_word(self):
        self.assertEqual({"concern": "child neglect"},
                         self.masker.restore({"concern": "child neglect"}))

    def test_a_token_written_exactly_inside_text_is_replaced(self):
        self.assertEqual({"name": "Ana Reyes please"},
                         self.masker.restore({"name": "CHILD please"}))

    def test_what_is_not_text_comes_back_as_it_was(self):
        self.assertEqual({"n": 3, "flag": True, "none": None},
                         self.masker.restore({"n": 3, "flag": True, "none": None}))

    def test_nothing_to_restore_is_a_no_op(self):
        bare = redact.Masker(Request(self.sw))
        self.assertEqual({"name": "CHILD"}, bare.restore({"name": "CHILD"}))


def fake_model(tool="get_child_summary", args=None, seen=None):
    """A stand-in for choose_tool that records everything it was given."""
    def choose(self, question, tool_payload, system=None):
        if seen is not None:
            seen.append({"question": question, "system": system, "tools": tool_payload})
        return tool, dict(args if args is not None else {"name": "CHILD"})
    return choose


class ChatAskBase(APITestCase):
    def setUp(self):
        self.sw = person("sw@racco1.gov.ph", Role.STAFF)
        self.other_sw = person("sw2@racco1.gov.ph", Role.STAFF)
        self.ana = child("Ana", "Reyes", sw=self.sw)
        self.joaquin = child("Joaquín", "Peña", sw=self.sw)
        self.theirs = child("Juan", "Magsaysay", sw=self.other_sw)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.sw)

    def ask(self, question, **model):
        seen = []
        with patch.object(services.OllamaClient, "choose_tool",
                          fake_model(seen=seen, **model)):
            res = self.client.post(ASK, {"question": question}, format="json")
        return res, seen

    def everything_stored(self):
        """Every text field of every job, as one string."""
        return json.dumps([
            [j.input_ref, j.output_text, j.error, j.model_used]
            for j in AssistantJob.objects.all()], ensure_ascii=False)


class WhatTheModelIsToldTest(ChatAskBase):
    def test_the_model_gets_the_token_not_the_name(self):
        _, seen = self.ask("Tell me about Ana Reyes")
        self.assertEqual(["Tell me about CHILD"], [s["question"] for s in seen])

    def test_no_child_name_reaches_the_model_in_any_spelling(self):
        for typed in ("ANA REYES", "ana reyes", "Reyes, Ana", "joaquin pena",
                      "Joaquín Peña", "PEÑA"):
            with self.subTest(typed):
                _, seen = self.ask(f"Tell me about {typed} please")
                # The question itself; the prompt and the tools are held to
                # the same by the tests on the static prefix.
                told = seen[0]["question"].lower()
                for name in ("ana", "reyes", "joaquín", "joaquin", "peña", "pena"):
                    self.assertNotIn(name, told)

    def test_a_phone_number_and_an_email_do_not_reach_the_model(self):
        _, seen = self.ask("text 0917 123 4567 or write ana@example.com")
        self.assertEqual("text PHONE or write EMAIL", seen[0]["question"])

    def test_a_child_nobody_here_can_see_is_passed_through_untouched(self):
        # Not in scope, so not recognised: the model is not told it is a child.
        _, seen = self.ask("Tell me about Juan Magsaysay")
        self.assertEqual("Tell me about Juan Magsaysay", seen[0]["question"])

    def test_the_prompt_is_one_constant_whatever_is_asked(self):
        _, first = self.ask("Tell me about Ana Reyes")
        _, second = self.ask("Sino si Joaquín Peña?")
        _, third = self.ask("Ilan ang mga bata ko?", tool="get_statistics", args={})
        for call in first + second + third:
            self.assertEqual(prompts.CHAT_SYSTEM, call["system"])
            self.assertEqual(tools.ollama_payload(), call["tools"])

    def test_the_static_prefix_names_no_child(self):
        for name in ("Ana", "Reyes", "Maria", "Santos"):
            self.assertNotIn(name, prompts.CHAT_SYSTEM)
        self.assertIn('get_child_summary(name="CHILD")', prompts.CHAT_SYSTEM)

    def test_a_hosted_model_is_sent_no_child_name(self):
        sent = []

        def post(self, body):
            sent.append(body)
            return {"choices": [{"message": {"tool_calls": [{"function": {
                "name": "get_child_summary", "arguments": '{"name": "CHILD"}'}}]}}]}

        with override_settings(**HOSTED), \
                patch.object(services.OpenAICompatibleClient, "_post", post):
            res = self.client.post(
                ASK, {"question": "Tell me about Ana Reyes, 0917 123 4567"},
                format="json")
        self.assertEqual(200, res.status_code)
        wire = json.dumps(sent, ensure_ascii=False)
        for private in ("Ana", "Reyes", "0917", "123 4567"):
            self.assertNotIn(private, wire)
        self.assertIn("Tell me about CHILD, PHONE", wire)


class TheLookupStillFindsTheChildTest(ChatAskBase):
    def test_the_summary_resolves_the_right_child(self):
        res, _ = self.ask("Tell me about Ana Reyes")
        self.assertEqual(200, res.status_code)
        self.assertEqual("one", res.data["result"]["match"])
        self.assertEqual(self.ana.id, res.data["result"]["child"]["id"])

    def test_an_accented_name_typed_without_its_accent_still_resolves(self):
        res, _ = self.ask("Sino si joaquin pena?")
        self.assertEqual(self.joaquin.id, res.data["result"]["child"]["id"])

    def test_the_model_echoing_the_token_in_another_case_still_resolves(self):
        res, _ = self.ask("Tell me about Ana Reyes", args={"name": "Child"})
        self.assertEqual(self.ana.id, res.data["result"]["child"]["id"])

    def test_two_children_each_resolve_to_their_own(self):
        res, _ = self.ask("Compare Ana Reyes and Joaquín Peña",
                          args={"name": "CHILD 2"})
        self.assertEqual(self.joaquin.id, res.data["result"]["child"]["id"])

    def test_the_echo_line_still_reads_naturally_to_the_person_who_asked(self):
        res, _ = self.ask("Tell me about Ana Reyes")
        self.assertEqual("Looking up: Ana Reyes", res.data["echo"])

    def test_a_name_the_asker_cannot_see_finds_nothing(self):
        res, _ = self.ask("Tell me about Juan Magsaysay", args={"name": "Juan Magsaysay"})
        self.assertEqual("none", res.data["result"]["match"])

    def test_a_guard_reads_the_question_as_typed(self):
        # "how many staff ..." is not about children, and the guard declines to
        # answer it with a child count. Masked, the question would carry the
        # word "child" and the guard would let the number through.
        res, _ = self.ask("How many staff are there besides Ana Reyes?",
                          tool="get_statistics", args={"measure": "children"})
        self.assertEqual("answer_directly", res.data["tool"])

    def test_an_action_is_still_refused_as_an_action(self):
        res, _ = self.ask("Book Ana Reyes for Friday", tool="list_my_appointments",
                          args={"when": "today"})
        self.assertEqual("answer_directly", res.data["tool"])
        job = AssistantJob.objects.get()
        self.assertEqual(AssistantJob.ACTION, job.answer)


class WhatTheLogKeepsTest(ChatAskBase):
    def test_the_audit_row_holds_the_masked_question(self):
        self.ask("Tell me about Ana Reyes")
        job = AssistantJob.objects.get()
        self.assertEqual("Tell me about CHILD", job.input_ref)

    def test_the_stored_tool_call_is_masked_too(self):
        self.ask("Tell me about Ana Reyes")
        job = AssistantJob.objects.get()
        self.assertEqual("get_child_summary({'name': 'CHILD'})", job.output_text)

    def test_no_stored_field_holds_a_child_name(self):
        self.ask("Tell me about Ana Reyes")
        self.ask("Sino si Joaquín Peña?", args={"name": "CHILD"})
        self.ask("Compare Ana Reyes and Joaquín Peña", args={"name": "CHILD 1"})
        self.ask("Tell me about Ana Reyes please", args={"name": "Ana Marie Reyes"})
        stored = self.everything_stored()
        for name in ("Ana", "Reyes", "Joaquín", "Peña"):
            self.assertNotIn(name, stored)

    def test_a_name_the_model_made_up_is_masked_in_the_stored_call(self):
        # The model returns a visible child's name it was never given.
        self.ask("Tell me about my newest child", args={"name": "Joaquín Peña"})
        job = AssistantJob.objects.get()
        self.assertEqual("get_child_summary({'name': 'CHILD'})", job.output_text)

    def test_a_refused_call_does_not_keep_the_name_in_its_error(self):
        # The model puts the token where an enum is expected; the validator's
        # sentence quotes the value it was given, which is by then the name.
        self.ask("Tell me about Ana Reyes", tool="list_my_appointments",
                 args={"when": "CHILD"})
        job = AssistantJob.objects.get()
        self.assertFalse(job.ok)
        self.assertNotIn("Ana", job.error)
        self.assertNotIn("Reyes", job.error)
        self.assertIn("CHILD", job.error)

    def test_an_outage_is_logged_masked_as_well(self):
        err = services.AIUnavailable("Local AI runtime unreachable: refused")
        with patch.object(services.OllamaClient, "choose_tool", side_effect=err):
            res = self.client.post(ASK, {"question": "About Ana Reyes"}, format="json")
        self.assertEqual(503, res.status_code)
        self.assertEqual("About CHILD", AssistantJob.objects.get().input_ref)

    def test_a_question_about_nothing_private_is_kept_as_asked(self):
        self.ask("Ilan ang mga bata ko?", tool="get_statistics", args={})
        self.assertEqual("Ilan ang mga bata ko?", AssistantJob.objects.get().input_ref)

    def test_a_follow_up_chip_is_stored_masked_too(self):
        # "Several children match - which one?" offers each full name as a
        # chip, and a click is logged with the chip's label.
        res = self.client.post(FOLLOWUP, {
            "tool": "get_child_summary", "args": {"name": "Ana M. Reyes"},
            "label": "Ana M. Reyes"}, format="json")
        self.assertEqual(200, res.status_code)
        job = AssistantJob.objects.get()
        self.assertEqual("CHILD", job.input_ref)
        self.assertEqual("get_child_summary({'name': 'CHILD'})", job.output_text)
        self.assertEqual(services_followup_model(), job.model_used)
        self.assertEqual(self.ana.id, res.data["result"]["child"]["id"])

    def test_a_follow_up_without_a_name_is_kept_as_it_was(self):
        self.client.post(FOLLOWUP, {"tool": "get_statistics", "args": {"by": "sex"},
                                    "label": "By sex?"}, format="json")
        self.assertEqual("By sex?", AssistantJob.objects.get().input_ref)

    def test_what_the_unanswered_list_shows_has_no_child_name(self):
        # An answer that found nothing is what the ISA's list is made of.
        self.ask("Tell me about Ana Reyes please")
        AssistantJob.objects.update(answer=AssistantJob.DATA, result_count=0)
        isa = person("isa@racco1.gov.ph", Role.ADMINISTRATOR)
        self.client.force_authenticate(isa)
        body = self.client.get("/api/assistant/unanswered/").content.decode()
        self.assertIn("Tell me about CHILD please", body)
        for name in ("Ana", "Reyes"):
            self.assertNotIn(name, body)


def services_followup_model():
    from assistant.views import FOLLOWUP_MODEL
    return FOLLOWUP_MODEL
