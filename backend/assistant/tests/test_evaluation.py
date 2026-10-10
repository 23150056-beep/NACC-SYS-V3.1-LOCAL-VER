"""Detector tests, built from strings the model actually produced.

Every fixture here is a real output captured while evaluating the shipped
drafting features against real remarks. A detector is only worth having if it
flags what we saw go wrong and stays quiet on what was fine, so both directions
are asserted.
"""
from django.test import SimpleTestCase

from assistant import evaluation


class InventedNamesTest(SimpleTestCase):
    """A capitalised word the model introduced mid-sentence is the signal that
    matters clinically: it reads as a person."""

    PROMPT = ("FACTS:\nFirst name: Yolanda\nAge: 9\nGender: female\n"
              "Recent remarks (newest first):\n"
              "- 2026-08-08: Settling in well. Nakikisalamuha na sa ibang bata "
              "during recreation.\n")

    def test_flags_a_name_the_model_invented(self):
        out = "This is a positive sign and part of Nakayuki's development."
        self.assertIn("Nakayuki", evaluation.invented_names(self.PROMPT, out))

    def test_flags_a_tagalog_verb_read_as_a_person(self):
        out = "She is settling well with peers, particularly Nakikisalamuha."
        # The word IS in the prompt, but as prose the model has turned it into
        # a person. Presence in the prompt is what clears it — so this must NOT
        # flag, and the repetition/drift detectors are what catch this case.
        self.assertEqual([], evaluation.invented_names(self.PROMPT, out))

    def test_does_not_flag_a_markdown_heading(self):
        out = "**What Has Changed Recently:**\nShe attended every session."
        self.assertEqual([], evaluation.invented_names(self.PROMPT, out))

    def test_does_not_flag_a_sentence_opener(self):
        out = "Continued positive engagement.\nAdditionally, her mood improved."
        self.assertEqual([], evaluation.invented_names(self.PROMPT, out))

    def test_does_not_flag_a_bulleted_opener(self):
        out = "- Observe if she can maintain focus during longer sessions."
        self.assertEqual([], evaluation.invented_names(self.PROMPT, out))

    def test_does_not_flag_the_childs_own_name(self):
        out = "During this session, check whether Yolanda seems withdrawn."
        self.assertEqual([], evaluation.invented_names(self.PROMPT, out))

    def test_does_not_flag_a_month_or_weekday(self):
        out = "She refused to join on August 22 and again on Friday."
        self.assertEqual([], evaluation.invented_names(self.PROMPT, out))

    def test_flags_only_the_novel_word_when_mixed(self):
        out = "Yolanda spoke about Marisol during the session."
        self.assertEqual(["Marisol"], evaluation.invented_names(self.PROMPT, out))


class RepeatedLinesTest(SimpleTestCase):
    def test_flags_a_heading_emitted_three_times(self):
        out = ("**Where the Case Stands:**\nShe is engaged.\n"
               "**What Has Changed Recently:**\n"
               "**What Has Changed Recently:**\n"
               "**What Has Changed Recently:**\n")
        self.assertIn("**What Has Changed Recently:**",
                      evaluation.repeated_lines(out))

    def test_ignores_blank_lines(self):
        self.assertEqual([], evaluation.repeated_lines("One.\n\n\nTwo.\n\n"))

    def test_clean_output_reports_nothing(self):
        out = "Where the case stands: engaged.\nWhat changed: nothing.\n"
        self.assertEqual([], evaluation.repeated_lines(out))


class LanguageDriftTest(SimpleTestCase):
    """Remark polish is instructed to return clear professional English. It
    returned Tagalog instead, and once returned garbled Tagalog that lost the
    original meaning."""

    def test_flags_the_garbled_tagalog_polish(self):
        out = ("Nakikisalamuha ng anak ng mother dito sa mga pagkakaiba ng "
               "bata sa recreation time.")
        self.assertTrue(evaluation.language_drift(out))

    def test_flags_untranslated_input_echoed_back(self):
        out = ("NOTE: Nag-aalala pa rin tungkol sa school. Hindi masyado "
               "nagsasalita ngayon.")
        self.assertTrue(evaluation.language_drift(out))

    def test_clean_english_does_not_drift(self):
        out = "Settling in well. Mixing with the other children during recreation."
        self.assertEqual([], evaluation.language_drift(out))

    def test_english_words_are_not_mistaken_for_tagalog(self):
        # "may" and "para" are deliberately absent from the marker set because
        # they are ordinary English; a detector that flags them is useless.
        out = "She may attend on Friday. The parameters of the plan are set."
        self.assertEqual([], evaluation.language_drift(out))


class RepeatedPhrasesTest(SimpleTestCase):
    """`repeated_lines` works on whole lines, so it missed a real defect sitting
    in its own evaluation output: "Nakikisalamuha na Nakikisalamuha" — the same
    word twice in one sentence. Line-level checks cannot see inside a line.
    """

    def test_flags_a_word_repeated_within_a_sentence(self):
        out = "Nakikisalamuha na Nakikisalamuha sa ibang bata dito sa recreation."
        self.assertIn("Nakikisalamuha", evaluation.repeated_phrases(out))

    def test_flags_an_immediate_stutter(self):
        self.assertIn("child", evaluation.repeated_phrases("The child child is well."))

    def test_ignores_short_function_words(self):
        # "the end of the day" repeats "the" close together and is ordinary.
        self.assertEqual([], evaluation.repeated_phrases("At the end of the day."))

    def test_ignores_a_word_reused_further_along(self):
        out = "Settling in well and mixing well with the other children."
        self.assertEqual([], evaluation.repeated_phrases(out))

    def test_clean_output_reports_nothing(self):
        out = "The child attended the session and completed the drawing task."
        self.assertEqual([], evaluation.repeated_phrases(out))

    def test_ignores_ordinary_english_reduplication(self):
        # "more and more", "step by step" and friends are idiomatic, not
        # stutters. The first version of this detector flagged all of them,
        # which put a false 13% defect rate into an evaluation run.
        for phrase in ("She is more and more engaged.",
                       "Progress has been step by step.",
                       "She opened up little by little.",
                       "They sat side by side.",
                       "He asked over and over about the visit."):
            with self.subTest(phrase=phrase):
                self.assertEqual([], evaluation.repeated_phrases(phrase))

    def test_still_flags_a_repeat_across_a_foreign_connector(self):
        # "na" is a Tagalog linker, not an English reduplication connector.
        out = "Nakikisalamuha na Nakikisalamuha sa ibang bata."
        self.assertIn("Nakikisalamuha", evaluation.repeated_phrases(out))

    def test_ignores_a_word_repeated_across_a_heading_boundary(self):
        # "**Percival's Case Brief** **Case Status:** Active" — "Case" twice in
        # two adjacent headings is document structure, not a stutter. Counting
        # across boundaries put a false 13% defect rate into a 60-run report.
        out = "**Percival's Case Brief**\n**Case Status:** Active and engaged."
        self.assertEqual([], evaluation.repeated_phrases(out))

    def test_ignores_a_word_repeated_across_a_sentence_boundary(self):
        out = "Recent Changes. Recent progress has been steady."
        self.assertEqual([], evaluation.repeated_phrases(out))

    def test_still_flags_a_stutter_inside_one_segment(self):
        out = "**Case Status:** Nakikisalamuha na Nakikisalamuha sa ibang bata."
        self.assertIn("Nakikisalamuha", evaluation.repeated_phrases(out))


# --- the case brief's string checks ---------------------------------------------
#
# Dates and numbers are compared with the FACTS the model was given. These need
# no model and no database: they are what the PASS line of
# `ai_eval --feature case_brief` stands on, so both directions are asserted - a
# draft that invents is flagged, and a draft that restates the same date in
# another dress is left alone.

FACTS = (
    "First name: Maria\n"
    "Age: 9\n"
    "Case referral: 2 on file, the latest filed Saturday 3 October 2026 (5 days ago).\n"
    "Consent: signed, dated Saturday 12 September 2026 (26 days ago).\n"
    "Next session: Wednesday 14 October 2026 (in 6 days) at 9:30 AM, Follow-up.\n"
    "Last session held: 12 days ago.\n"
    "Self-report answers waiting to be read: 1\n"
    "Open problems on file: 3\n"
)


class InventedDatesTest(SimpleTestCase):
    def flagged(self, draft):
        return evaluation.invented_dates(FACTS, draft)

    def test_a_date_that_is_in_the_facts_is_not_invented_however_it_is_written(self):
        for draft in (
            "The next session is on 14 October.",
            "The next session is on 14 October 2026.",
            "The next session is on Oct 14.",
            "The next session is on October 14th, 2026.",
            "The next session is on the 14th of October.",
            "The next session is on 2026-10-14.",
            "The next session is on 14/10/2026.",
            "The next session is on 10/14/2026.",
            "The next session is on Wednesday 14 October at 9:30 AM.",
            "Seen on Wednesday the 14th.",
            "The referral came in on 3 Oct.",
            "Consent was signed on 12 September.",
        ):
            with self.subTest(draft):
                self.assertEqual([], self.flagged(draft))

    def test_a_wrong_day_is_invented(self):
        self.assertEqual(["15 October"], self.flagged("The next session is on 15 October."))
        self.assertEqual(["October 15"], self.flagged("The next session is on October 15."))
        self.assertEqual(["2026-10-15"], self.flagged("Booked for 2026-10-15."))
        self.assertEqual(["15/10/2026"], self.flagged("Booked for 15/10/2026."))

    def test_a_wrong_month_is_invented(self):
        self.assertEqual(["14 November"], self.flagged("Booked for 14 November."))

    def test_a_wrong_year_is_invented(self):
        self.assertEqual(["14 October 2025"], self.flagged("Booked for 14 October 2025."))

    def test_a_year_the_facts_never_gave_for_that_day_is_not_checked(self):
        facts = "Next session: 14 October (in 6 days)."
        self.assertEqual([], evaluation.invented_dates(facts, "On 14 October 2026."))

    def test_a_weekday_with_a_day_has_to_be_that_weekday_and_that_day(self):
        self.assertEqual(["Tuesday the 14th"],
                         self.flagged("Seen on Tuesday the 14th."))
        self.assertEqual(["Wednesday 15"], self.flagged("Seen on Wednesday 15."))
        self.assertIn("Fri 3", self.flagged("Seen on Fri 3 October."))

    def test_a_weekday_on_its_own_has_to_be_one_the_facts_mention(self):
        self.assertEqual([], self.flagged("Seen on Wednesday."))
        self.assertEqual([], self.flagged("Filed on a saturday."))
        self.assertEqual(["Monday"], self.flagged("The visit is on Monday."))

    def test_a_clock_time_after_a_weekday_is_not_a_date(self):
        self.assertEqual([], self.flagged("Seen on Wednesday, 9:30 AM."))
        self.assertEqual([], self.flagged("Seen on Wednesday 9 AM."))

    def test_ordinary_words_are_not_dates(self):
        for draft in ("She may 5 times a day need help.", "He sat 3 hours.",
                      "In March the case moved.", "A session may follow.",
                      "Next week, then 3 more visits.", "tomorrow and in 6 days"):
            with self.subTest(draft):
                self.assertEqual([], self.flagged(draft))

    def test_a_list_marker_on_the_next_line_is_not_a_day(self):
        # Found by the first eval test: "October.\n2." read as the 2nd of October.
        self.assertEqual(["20 October"],
                         self.flagged("Booked for 20 October.\n2. Check the consent."))
        self.assertEqual([], self.flagged("Filed on Saturday.\n3. Ask about the consent."))
        self.assertEqual([], self.flagged("Seen in October.\n2. Ask."))

    def test_a_full_stop_ends_a_sentence_unless_the_month_is_abbreviated(self):
        self.assertEqual([], self.flagged("It is due in October. 2 more visits follow."))
        self.assertEqual(["Oct. 20"], self.flagged("Booked Oct. 20."))
        self.assertEqual([], self.flagged("Booked 14 Oct. Then 3 weeks of follow-up."))

    def test_a_fraction_is_not_a_date(self):
        self.assertEqual([], self.flagged("About 3/4 of the forms are in."))

    def test_each_is_reported_once_in_the_order_written(self):
        out = "Booked 15 October, then 20 October, then 15 October again."
        self.assertEqual(["15 October", "20 October"], self.flagged(out))

    def test_no_dates_at_all_is_clean(self):
        self.assertEqual([], self.flagged("Ask about the consent and the next visit."))
        self.assertEqual([], evaluation.invented_dates("", "Nothing to say."))

    def test_a_date_with_no_dates_in_the_facts_is_invented(self):
        self.assertEqual(["12 October"],
                         evaluation.invented_dates("Next session: none booked.",
                                                   "Visit on 12 October."))


class InventedNumbersTest(SimpleTestCase):
    def flagged(self, draft):
        return evaluation.invented_numbers(FACTS, draft)

    def test_numbers_in_the_facts_are_fine(self):
        self.assertEqual([], self.flagged(
            "Maria is 9. Two referrals are on file, 5 days old; 3 problems are open, "
            "the last session was 12 days ago, the next in 6 days at 9:30."))

    def test_a_number_the_facts_never_held_is_invented(self):
        self.assertEqual(["7"], self.flagged("There are 7 open problems."))
        self.assertEqual(["40"], self.flagged("Seen about 40 days ago."))

    def test_the_numbered_parts_are_not_numbers_the_draft_states(self):
        draft = ("1. Why Maria was referred.\n2. Next session in 6 days.\n"
                 "3. Ask about the consent.")
        self.assertEqual([], self.flagged(draft))

    def test_markdown_dressed_markers_are_not_numbers_either(self):
        for draft in ("**1.** Why.\n**2.** Booked.\n**3.** Ask.",
                      "1) Why.\n2) Booked.\n3) Ask.",
                      "- 1. Why.\n- 2. Booked.", "(1) Why.\n(2) Booked.\n(3) Ask."):
            with self.subTest(draft):
                self.assertEqual([], self.flagged(draft))

    def test_a_number_that_only_looks_like_a_marker_still_counts(self):
        # Mid-line, or a decimal at the start of a line.
        self.assertEqual(["8"], self.flagged("Seen 8. times"))
        self.assertEqual(["4", "7"], self.flagged("4.7 hours are needed."))

    def test_the_instructions_do_not_count_as_facts(self):
        # "3 parts", "150 words": digits of the instructions, not of the case.
        self.assertEqual(["150"], self.flagged("Within 150 words."))

    def test_leading_zeros_are_the_same_number(self):
        self.assertEqual([], self.flagged("Booked at 09:30."))

    def test_the_digits_of_a_date_are_the_date_checks_business(self):
        # "14 October 2026" restated as ISO is a legitimate restatement; its
        # month number is not "an invented 10".
        self.assertEqual([], self.flagged("The next session is on 2026-10-14."))
        self.assertEqual([], self.flagged("The next session is on 14/10/2026."))

    def test_each_is_reported_once(self):
        self.assertEqual(["7", "8"], self.flagged("7 and 8 and 7 again."))

    def test_a_draft_with_no_numbers_is_clean(self):
        self.assertEqual([], self.flagged("Ask about the consent."))


class WordsOverTest(SimpleTestCase):
    def test_exactly_the_limit_is_fine(self):
        self.assertEqual(0, evaluation.words_over("word " * 150))

    def test_one_over_is_reported_with_the_count(self):
        self.assertEqual(151, evaluation.words_over("word " * 151))

    def test_the_limit_is_150_words(self):
        self.assertEqual(150, evaluation.WORD_LIMIT)

    def test_a_different_limit(self):
        self.assertEqual(11, evaluation.words_over("word " * 11, limit=10))
