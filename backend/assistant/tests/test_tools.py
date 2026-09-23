"""Validator tests, written from what the model actually did.

The spike measured 91% functional accuracy on tool calls. Every remaining
failure was the model ignoring its own schema, and every case below is one that
was observed rather than imagined — a key with a colon in it, an enum value
that was not in the enum, an argument silently dropped.

The validator is a pure function over the model's output, so none of this needs
Django or a running Ollama.
"""
from datetime import date

from django.test import SimpleTestCase

from assistant import tools


class NormalisesKeysTest(SimpleTestCase):
    def test_strips_punctuation_the_model_left_on_a_key(self):
        # Observed: {"when: ": "today"} — a naive args["when"] raises KeyError
        # and turns the whole turn into a 500.
        call = tools.validate("list_my_appointments", {"when: ": "today"})
        self.assertEqual(call.args, {"when": "today"})

    def test_strips_surrounding_whitespace(self):
        call = tools.validate("get_statistics", {"  status  ": "terminated"})
        # The key is cleaned and the value kept. Not the whole dict: the
        # schema's defaults are filled in beside it.
        self.assertEqual("terminated", call.args["status"])
        self.assertNotIn("  status  ", call.args)


class CoercesEnumsTest(SimpleTestCase):
    def test_maps_a_tagalog_time_word_to_its_enum_value(self):
        # Observed four times: the model returned the Tagalog word itself
        # rather than the enum member. This alias is what takes 91% to 100%.
        call = tools.validate("list_my_appointments", {"when": "bukas"})
        self.assertEqual(call.args["when"], "tomorrow")

    def test_maps_ngayon_to_today(self):
        call = tools.validate("list_my_appointments", {"when": "ngayon"})
        self.assertEqual(call.args["when"], "today")

    def test_accepts_a_valid_enum_value_untouched(self):
        call = tools.validate("list_my_appointments", {"when": "this_week"})
        self.assertEqual(call.args["when"], "this_week")

    def test_is_case_insensitive(self):
        call = tools.validate("list_my_appointments", {"when": "Tomorrow"})
        self.assertEqual(call.args["when"], "tomorrow")

    def test_rejects_a_value_that_is_in_no_enum_and_has_no_alias(self):
        call = tools.validate("list_my_appointments", {"when": "someday"})
        self.assertFalse(call.ok)
        self.assertIn("when", call.error)


class RequiredArgumentsTest(SimpleTestCase):
    def test_rejects_a_missing_required_argument(self):
        # The model dropped optional free-text arguments on every single call,
        # so the ones that matter are required and their absence is fatal.
        call = tools.validate("search_children_by_concern", {})
        self.assertFalse(call.ok)
        self.assertIn("concern", call.error)

    def test_rejects_a_blank_required_argument(self):
        call = tools.validate("get_child_summary", {"name": "   "})
        self.assertFalse(call.ok)

    def test_accepts_a_tool_that_takes_no_arguments(self):
        call = tools.validate("list_care_gaps", {})
        self.assertTrue(call.ok)
        self.assertEqual(call.args, {})


class RejectsUnknownTest(SimpleTestCase):
    def test_rejects_a_tool_that_does_not_exist(self):
        call = tools.validate("delete_everything", {})
        self.assertFalse(call.ok)

    def test_drops_an_argument_the_schema_does_not_declare(self):
        # Scope must never come from the model. No tool declares an
        # "assigned_to_me" parameter, so an invented one is discarded rather
        # than reaching a queryset.
        call = tools.validate("get_statistics",
                              {"status": "active", "assigned_to_me": False})
        self.assertTrue(call.ok)
        self.assertNotIn("assigned_to_me", call.args)


class EchoTest(SimpleTestCase):
    """The echo line is the mitigation for a silently dropped filter: the user
    sees what the system understood before they see the answer."""

    def test_describes_the_call_in_the_users_words(self):
        call = tools.validate("search_children_by_concern",
                              {"concern": "school refusal"})
        self.assertIn("school refusal", call.echo)

    def test_a_tool_without_arguments_still_describes_itself(self):
        call = tools.validate("list_care_gaps", {})
        self.assertTrue(call.echo)


class SchemaShapeTest(SimpleTestCase):
    """Constraints the spike established, asserted so a later edit cannot
    quietly undo them."""

    def test_there_are_exactly_ten_tools(self):
        # Four naive tools produced a misroute; six hardened ones scored 100%
        # on selection. Every addition since has come with its own evaluation
        # run rather than a hunch: list_self_report_flags, then count_people
        # and list_unassigned_children together, then find_availability. An
        # eleventh needs the same before this number moves again.
        self.assertEqual(10, len(tools.REGISTRY))

    def test_no_tool_declares_an_optional_free_text_parameter(self):
        # Measured: enum and required parameters survived every call; optional
        # free-text parameters were dropped on every call.
        for name, spec in tools.REGISTRY.items():
            for arg, meta in spec["schema"].items():
                with self.subTest(tool=name, arg=arg):
                    if not meta.get("required"):
                        self.assertIn("enum", meta)

    def test_no_tool_accepts_a_scope_argument(self):
        banned = {"assigned_to_me", "psychologist", "user", "child_id", "all"}
        for name, spec in tools.REGISTRY.items():
            with self.subTest(tool=name):
                self.assertFalse(banned & set(spec["schema"]))

    def test_every_tool_has_a_resolver(self):
        for name, spec in tools.REGISTRY.items():
            with self.subTest(tool=name):
                self.assertTrue(callable(spec["resolve"]))


class AnswerDirectlyTest(SimpleTestCase):
    """Measured: in one run of 28, the model called answer_directly with no
    `reason`. The reply is the same fixed copy regardless, so requiring it
    turned a correct routing decision into a failed turn."""

    def test_accepts_a_missing_reason(self):
        call = tools.validate("answer_directly", {})
        self.assertTrue(call.ok)

    def test_still_coerces_a_reason_when_given(self):
        call = tools.validate("answer_directly", {"reason": "general_knowledge"})
        self.assertEqual(call.args["reason"], "general_knowledge")


class MisrouteGuardTest(SimpleTestCase):
    """Observed in the browser: "how many psychologist are in the system?"
    answered "40 active children".

    The cause was this tool's own description — it said "Use for questions
    starting 'how many'", so the model obeyed. Rewording it fixed 2 of 4
    phrasings, which is not good enough for a tool that answers with a
    confident number. A confidently wrong answer is worse than "I can't".
    """

    def _guard(self, question, tool="get_statistics", args=None):
        call = tools.validate(tool, args if args is not None else {"status": "active"})
        return tools.correct_obvious_misroute(question, call)

    def test_counting_psychologists_is_not_a_child_count(self):
        self.assertEqual("answer_directly",
                         self._guard("how many psychologist are in the system?").tool)

    def test_counting_staff_is_not_a_child_count(self):
        self.assertEqual("answer_directly",
                         self._guard("How many staff are in the system?").tool)

    def test_counting_users_is_not_a_child_count(self):
        self.assertEqual("answer_directly",
                         self._guard("How many users are there?").tool)

    def test_the_tagalog_phrasing_is_guarded_too(self):
        self.assertEqual("answer_directly",
                         self._guard("Ilan ang mga psychologist dito?").tool)

    def test_a_real_child_count_is_untouched(self):
        call = self._guard("How many children am I handling?")
        self.assertEqual("get_statistics", call.tool)
        self.assertEqual("active", call.args["status"])

    def test_a_tagalog_child_count_is_untouched(self):
        self.assertEqual("get_statistics", self._guard("Ilan ang mga bata ko?").tool)

    def test_a_question_naming_both_still_counts_children(self):
        # "children" is the subject; "staff" is incidental. Guarding this would
        # refuse a question the tool can actually answer.
        self.assertEqual("get_statistics",
                         self._guard("How many children were referred by staff?").tool)

    def test_it_only_guards_the_count_tool(self):
        # A psychologist's NAME in a summary lookup must not be guarded.
        call = self._guard("Tell me about the psychologist's case for Ana",
                           tool="get_child_summary", args={"name": "Ana"})
        self.assertEqual("get_child_summary", call.tool)

    def test_a_rejected_call_is_left_alone(self):
        call = tools.validate("get_statistics", {"status": "someday"})
        guarded = tools.correct_obvious_misroute("how many staff?", call)
        self.assertFalse(guarded.ok)


class PeriodRangeTest(SimpleTestCase):
    """Weeks start on Sunday, matching Schedule.jsx. End is exclusive."""

    # A Wednesday, so week boundaries are visible in both directions.
    WED = date(2026, 8, 26)

    def test_today_is_one_day(self):
        self.assertEqual(tools.period_range("today", self.WED),
                         (date(2026, 8, 26), date(2026, 8, 27)))

    def test_yesterday_is_the_day_before(self):
        self.assertEqual(tools.period_range("yesterday", self.WED),
                         (date(2026, 8, 25), date(2026, 8, 26)))

    def test_tomorrow_is_the_day_after(self):
        self.assertEqual(tools.period_range("tomorrow", self.WED),
                         (date(2026, 8, 27), date(2026, 8, 28)))

    def test_this_week_starts_on_the_preceding_sunday(self):
        # 26 Aug 2026 is a Wednesday; the Sunday before is the 23rd.
        self.assertEqual(tools.period_range("this_week", self.WED),
                         (date(2026, 8, 23), date(2026, 8, 30)))

    def test_a_sunday_starts_its_own_week(self):
        self.assertEqual(tools.period_range("this_week", date(2026, 8, 23)),
                         (date(2026, 8, 23), date(2026, 8, 30)))

    def test_last_week_and_next_week(self):
        self.assertEqual(tools.period_range("last_week", self.WED),
                         (date(2026, 8, 16), date(2026, 8, 23)))
        self.assertEqual(tools.period_range("next_week", self.WED),
                         (date(2026, 8, 30), date(2026, 9, 6)))

    def test_this_month_and_last_month(self):
        self.assertEqual(tools.period_range("this_month", self.WED),
                         (date(2026, 8, 1), date(2026, 9, 1)))
        self.assertEqual(tools.period_range("last_month", self.WED),
                         (date(2026, 7, 1), date(2026, 8, 1)))

    def test_month_arithmetic_crosses_the_year(self):
        self.assertEqual(tools.period_range("this_month", date(2026, 12, 5)),
                         (date(2026, 12, 1), date(2027, 1, 1)))
        self.assertEqual(tools.period_range("last_month", date(2026, 1, 5)),
                         (date(2025, 12, 1), date(2026, 1, 1)))

    def test_this_year_and_last_year(self):
        self.assertEqual(tools.period_range("this_year", self.WED),
                         (date(2026, 1, 1), date(2027, 1, 1)))
        self.assertEqual(tools.period_range("last_year", self.WED),
                         (date(2025, 1, 1), date(2026, 1, 1)))

    def test_an_unknown_period_raises(self):
        with self.assertRaises(KeyError):
            tools.period_range("last_fortnight", self.WED)


class KahaponMeansYesterdayTest(SimpleTestCase):
    """Regression. It was aliased to `today`, so "sino ang nakita ko kahapon?"
    answered with today's appointments and said nothing about it."""

    def test_kahapon_maps_to_yesterday(self):
        call = tools.validate("list_my_appointments", {"when": "kahapon"})
        self.assertTrue(call.ok, call.error)
        self.assertEqual(call.args["when"], "yesterday")

    def test_month_and_week_words_map(self):
        for word, expected in [("ngayong buwan", "this_month"),
                               ("nakaraang buwan", "last_month"),
                               ("last week", "last_week")]:
            call = tools.validate("list_my_appointments", {"when": word})
            self.assertTrue(call.ok, f"{word}: {call.error}")
            self.assertEqual(call.args["when"], expected, word)

    def test_year_words_map_on_the_tool_that_takes_a_year(self):
        # Appointments stop at the month, so the year words are exercised
        # where a year is actually a question someone asks.
        for word, expected in [("ngayong taon", "this_year"),
                               ("nakaraang taon", "last_year")]:
            call = tools.validate("list_self_report_flags", {"period": word})
            self.assertTrue(call.ok, f"{word}: {call.error}")
            self.assertEqual(call.args["period"], expected, word)

    def test_every_appointment_period_is_accepted_by_the_validator(self):
        for period in tools.APPOINTMENT_PERIODS:
            call = tools.validate("list_my_appointments", {"when": period})
            self.assertTrue(call.ok, f"{period}: {call.error}")

    def test_appointments_stop_at_the_month(self):
        # Offering the year sent "what have I got this year?" to list_care_gaps
        # 3/3, and nobody asks to see a year of appointments. Flags still reach
        # a year, because reviewing them over one is a real question.
        self.assertNotIn("this_year", tools.APPOINTMENT_PERIODS)
        self.assertIn("this_year", tools.PERIODS)
        self.assertFalse(tools.validate(
            "list_my_appointments", {"when": "this_year"}).ok)
        self.assertTrue(tools.validate(
            "list_self_report_flags", {"period": "this_year"}).ok)


class RoleAliasTest(SimpleTestCase):
    """Measured: asked "how many users are in the system?", the model answered
    role="any" against an enum offering "anyone", and the call was rejected —
    a correctly routed question turned into an apology over one synonym."""

    def test_any_means_anyone(self):
        call = tools.validate("count_people", {"role": "any"})
        self.assertTrue(call.ok, call.error)
        self.assertEqual(call.args["role"], "anyone")

    def test_users_means_anyone(self):
        self.assertEqual(
            tools.validate("count_people", {"role": "users"}).args["role"], "anyone")

    def test_a_plural_role_is_singularised(self):
        self.assertEqual(
            tools.validate("count_people", {"role": "psychologists"}).args["role"],
            "psychologist")

    def test_a_tagalog_role_word_maps(self):
        self.assertEqual(
            tools.validate("count_people", {"role": "kawani"}).args["role"], "staff")

    def test_a_role_that_is_not_a_role_is_still_rejected(self):
        self.assertFalse(tools.validate("count_people", {"role": "children"}).ok)


class UnassignedDescriptionTest(SimpleTestCase):
    """The Tagalog example in this description pulled "sino ang mga bata na
    ayaw pumasok sa eskwela" — a concern question — into the assignment tool
    3 times out of 3. The model matched the shape of the phrase, not its
    subject. CHAT_SYSTEM carries the Tagalog framing for every tool."""

    def test_it_carries_no_tagalog_example_of_its_own(self):
        description = tools.REGISTRY["list_unassigned_children"]["description"]
        for word in ("sino", "walang", "mga bata"):
            self.assertNotIn(word, description.lower())

    def test_it_points_concern_questions_at_the_right_tool(self):
        description = tools.REGISTRY["list_unassigned_children"]["description"]
        self.assertIn("search_children_by_concern", description)


class AppointmentDescriptionCoversThePastTest(SimpleTestCase):
    """The enum grew to cover yesterday and last week; the description did
    not. "What did I do last week?" went to list_care_gaps 3 times out of 3
    because the only tool that could answer it never claimed it could."""

    def test_the_description_claims_past_work(self):
        description = tools.REGISTRY["list_my_appointments"]["description"].lower()
        self.assertTrue(
            any(w in description for w in ("already did", "history", "past")),
            "a tool offering yesterday and last_week must say it answers about "
            "work already done, or the model will route those questions away")

    def test_every_past_period_the_enum_offers_is_resolvable(self):
        for period in tools.APPOINTMENT_PERIODS:
            self.assertTrue(
                tools.validate("list_my_appointments", {"when": period}).ok, period)
