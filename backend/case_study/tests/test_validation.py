"""What each kind of box will and will not store.

Every refusal is ONE plain sentence, because the screen shows it as it is. The
date `today` is passed in (8 Oct 2026) so nothing here reads the clock; one
test checks the default does by patching it.
"""
from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from case_study.sections import PAP_ROWS, entry_for
from case_study.validation import (
    check_against_other_sections, clean_date_prepared, clean_value, partner_of)

TODAY = date(2026, 10, 8)


def clean(key, value):
    return clean_value(entry_for(key), value, today=TODAY)


class Refusals(SimpleTestCase):
    def refused(self, key, value):
        with self.assertRaises(ValidationError) as caught:
            clean(key, value)
        self.assertEqual(1, len(caught.exception.messages))
        sentence = caught.exception.messages[0]
        self.assertTrue(sentence.endswith("."), sentence)
        self.assertNotIn("{", sentence)
        return sentence


class ProseTest(Refusals):
    def test_it_is_stripped(self):
        self.assertEqual("Left at the gate.", clean("a2_circumstances", "  Left at the gate.\n"))

    def test_nothing_entered_is_blank(self):
        self.assertEqual("", clean("a2_circumstances", None))
        self.assertEqual("", clean("a2_circumstances", "   "))

    def test_twenty_thousand_characters_are_the_limit(self):
        self.assertEqual(20000, len(clean("a2_circumstances", "x" * 20000)))
        self.assertIn("too long", self.refused("a2_circumstances", "x" * 20001))

    def test_it_must_be_text(self):
        self.refused("a2_circumstances", ["a"])
        self.refused("a2_circumstances", 12)

    def test_a_nul_character_is_dropped(self):
        # PostgreSQL's JSON type cannot store one: a server error on the hosted
        # database only, so it never gets that far.
        self.assertEqual("ab", clean("a2_circumstances", "a\x00b\x00"))
        self.assertEqual(["ab"], clean("a2_sources", ["a\x00b"]))
        self.assertEqual("ab", clean("b1_paps", {"female": {"full_name": "a\x00b"}})
                         ["female"]["full_name"])


class ListTest(Refusals):
    def test_blank_lines_are_dropped_and_the_rest_kept_in_order(self):
        self.assertEqual(["The child", "Grandmother"],
                         clean("a2_sources", [" The child ", "", "  ", "Grandmother"]))

    def test_fifty_lines_at_most(self):
        self.assertEqual(50, len(clean("a2_sources", ["a"] * 50)))
        self.assertIn("at most 50", self.refused("a2_sources", ["a"] * 51))

    def test_a_blank_line_does_not_count_towards_the_limit(self):
        self.assertEqual(50, len(clean("a2_sources", ["a"] * 50 + [""] * 10)))

    def test_each_line_is_text(self):
        self.refused("a2_sources", [1])
        self.refused("a2_sources", "one line")

    def test_nothing_entered_is_an_empty_list(self):
        self.assertEqual([], clean("a2_sources", None))


class TableTest(Refusals):
    def row(self, **over):
        row = {"vaccine": "BCG", "date": "2019-05", "place": "RHU"}
        row.update(over)
        return row

    def test_a_good_table_is_kept(self):
        rows = [self.row(), self.row(vaccine="Hepatitis B", date="2019")]
        self.assertEqual(rows, clean("a3_immunizations", rows))

    def test_a_fully_blank_row_is_dropped(self):
        blank = {"vaccine": "", "date": "", "place": "  "}
        self.assertEqual([self.row()], clean("a3_immunizations", [self.row(), blank]))

    def test_a_row_must_have_exactly_the_columns(self):
        self.refused("a3_immunizations", [{"vaccine": "BCG", "date": "", "place": "",
                                           "extra": "x"}])
        self.refused("a3_immunizations", [{"vaccine": "BCG"}])
        self.refused("a3_immunizations", ["BCG"])
        self.refused("a3_immunizations", {"vaccine": "BCG"})

    def test_a_cell_is_at_most_five_hundred_characters(self):
        self.assertEqual(500, len(clean("a3_immunizations", [self.row(place="x" * 500)])[0]["place"]))
        self.refused("a3_immunizations", [self.row(place="x" * 501)])

    def test_fifty_rows_at_most(self):
        self.assertEqual(50, len(clean("a3_immunizations", [self.row()] * 50)))
        self.assertIn("at most 50", self.refused("a3_immunizations", [self.row()] * 51))

    def test_partial_dates(self):
        for good in ("2019", "2019-05", "2019-05-14", "2026", "2026-10", "2026-10-08"):
            self.assertEqual(good, clean("a3_immunizations", [self.row(date=good)])[0]["date"])
        for bad in ("May 2019", "19", "2019-5", "2019/05", "2019-13", "2019-02-30", "1850",
                    "2019-05-14T00:00"):
            self.refused("a3_immunizations", [self.row(date=bad)])

    def test_a_partial_date_is_not_in_the_future(self):
        for future in ("2027", "2026-11", "2026-10-09"):
            self.assertIn("future", self.refused("a3_immunizations", [self.row(date=future)]))

    def test_the_age_column_is_a_whole_number_up_to_150(self):
        def age(value):
            row = {c["key"]: "" for c in entry_for("a4_family_composition")["columns"]}
            row.update(name="Maria", age=value)
            return clean("a4_family_composition", [row])[0]["age"]

        self.assertEqual("0", age(0))
        self.assertEqual("150", age("150"))
        self.assertEqual("34", age(34))
        for bad in (151, -1, "abc", 3.5, "1e2", True):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                age(bad)

    def test_a_date_column_is_a_full_date_not_in_the_future(self):
        row = {"date": "2026-02-01", "stage": "before", "goals": "Grief work"}
        self.assertEqual([row], clean("a5_counselling", [row]))
        self.refused("a5_counselling", [dict(row, date="2026-02")])
        self.assertIn("future", self.refused("a5_counselling", [dict(row, date="2026-12-01")]))

    def test_a_choice_column_takes_only_what_it_offers(self):
        row = {"date": "2026-02-01", "stage": "before", "goals": ""}
        for stage in ("before", "during", "after"):
            self.assertEqual(stage, clean("a5_counselling", [dict(row, stage=stage)])[0]["stage"])
        self.refused("a5_counselling", [dict(row, stage="Before")])
        self.refused("a5_counselling", [dict(row, stage="sometime")])

    def test_the_search_efforts_choices(self):
        row = {"kind": "newspaper_publication", "date": "2026-03-01", "note": "Daily Tribune"}
        self.assertEqual([row], clean("a5_search_efforts", [row]))
        self.refused("a5_search_efforts", [dict(row, kind="rumour")])


class PapTableTest(Refusals):
    def test_two_columns_and_only_the_rows_that_exist(self):
        value = {"female": {"full_name": " Maria Reyes ", "religion": "Catholic",
                            "date_of_birth": "1985-06-01"},
                 "male": {"full_name": "Jose Reyes"}}
        self.assertEqual(
            {"female": {"full_name": "Maria Reyes", "date_of_birth": "1985-06-01",
                        "religion": "Catholic"},
             "male": {"full_name": "Jose Reyes"}},
            clean("b1_paps", value))

    def test_blank_answers_are_not_kept_and_a_missing_column_is_empty(self):
        self.assertEqual({"female": {"full_name": "Maria Reyes"}, "male": {}},
                         clean("b1_paps", {"female": {"full_name": "Maria Reyes", "religion": " "}}))
        self.assertEqual({"female": {}, "male": {}}, clean("b1_paps", None))

    def test_every_row_id_is_accepted(self):
        column = {row["id"]: ("1980-01-01" if row["id"] == "date_of_birth" else "x")
                  for row in PAP_ROWS}
        self.assertEqual(column, clean("b1_paps", {"female": column, "male": {}})["female"])

    def test_a_row_that_does_not_exist_is_refused(self):
        self.refused("b1_paps", {"female": {"shoe_size": "7"}})

    def test_a_third_column_is_refused(self):
        self.refused("b1_paps", {"female": {}, "male": {}, "other": {}})

    def test_the_date_of_birth_is_a_full_date_and_not_in_the_future(self):
        self.refused("b1_paps", {"female": {"date_of_birth": "1985"}})
        self.assertIn("future", self.refused("b1_paps", {"male": {"date_of_birth": "2030-01-01"}}))

    def test_an_answer_is_at_most_five_hundred_characters(self):
        self.refused("b1_paps", {"female": {"occupation": "x" * 501}})

    def test_the_shape_must_be_right(self):
        self.refused("b1_paps", [])
        self.refused("b1_paps", {"female": ["Maria"]})


class DateAndTickTest(Refusals):
    def test_a_date(self):
        self.assertEqual("2026-03-14", clean("a5_dvc_signed", "2026-03-14"))
        self.assertEqual("2026-10-08", clean("a5_dvc_signed", "2026-10-08"))
        self.assertIsNone(clean("a5_dvc_signed", None))
        self.assertIsNone(clean("a5_dvc_signed", ""))

    def test_a_date_is_a_real_one_in_iso_form(self):
        for bad in ("14/03/2026", "2026-3-14", "2026-02-30", "20260314", "yesterday", 20260314):
            self.refused("a5_dvc_signed", bad)

    def test_a_date_is_not_in_the_future(self):
        self.assertIn("future", self.refused("a5_dvc_signed", "2026-10-09"))

    def test_a_tick_is_a_yes_or_no(self):
        self.assertIs(True, clean("a5_aware_irrevocable", True))
        self.assertIs(False, clean("a5_aware_irrevocable", False))
        self.assertIs(False, clean("a5_aware_irrevocable", None))
        for bad in ("yes", 1, "true"):
            self.refused("a5_aware_irrevocable", bad)


class MeasurementsTest(Refusals):
    def test_each_part_is_optional(self):
        self.assertEqual({}, clean("c4_measurements", {}))
        self.assertEqual({"height_cm": 98}, clean("c4_measurements", {"height_cm": 98}))
        self.assertEqual({"weight_kg": 14.5}, clean("c4_measurements", {"weight_kg": "14.5"}))
        self.assertEqual({"height_cm": 98.3, "weight_kg": 14, "measured_on": "2026-10-01"},
                         clean("c4_measurements", {"height_cm": 98.26, "weight_kg": 14.0,
                                                   "measured_on": "2026-10-01"}))

    def test_the_ranges(self):
        for good in (30, 250):
            clean("c4_measurements", {"height_cm": good})
        for bad in (29, 251, 0, -5):
            self.refused("c4_measurements", {"height_cm": bad})
        for good in (2, 250):
            clean("c4_measurements", {"weight_kg": good})
        for bad in (1.9, 251, 0):
            self.refused("c4_measurements", {"weight_kg": bad})

    def test_it_must_be_a_number(self):
        for bad in ("tall", True, [1], "nan", "inf"):
            self.refused("c4_measurements", {"height_cm": bad})

    def test_the_date_measured_is_not_in_the_future(self):
        self.assertIn("future", self.refused("c4_measurements", {"measured_on": "2026-12-25"}))

    def test_nothing_else_is_kept(self):
        self.refused("c4_measurements", {"height": 98})


class PlacementTest(Refusals):
    def test_all_four_in_order(self):
        value = {"matching_date": "2026-01-10", "racco_cpa": " RACCO 1 ",
                 "accepted_date": "2026-01-20", "entrustment_date": "2026-02-01"}
        self.assertEqual(dict(value, racco_cpa="RACCO 1"), clean("c1_placement", value))

    def test_only_the_entrustment_date_is_needed(self):
        self.assertEqual({"entrustment_date": "2026-02-01"},
                         clean("c1_placement", {"entrustment_date": "2026-02-01"}))
        self.assertEqual({}, clean("c1_placement", {}))

    def test_dates_that_are_the_same_day_are_fine(self):
        day = "2026-02-01"
        clean("c1_placement", {"matching_date": day, "accepted_date": day,
                               "entrustment_date": day})

    def test_acceptance_is_not_before_matching(self):
        self.refused("c1_placement", {"matching_date": "2026-02-01",
                                      "accepted_date": "2026-01-31"})

    def test_entrustment_is_not_before_acceptance(self):
        self.refused("c1_placement", {"accepted_date": "2026-02-01",
                                      "entrustment_date": "2026-01-31"})

    def test_entrustment_is_not_before_matching_even_with_no_acceptance_date(self):
        self.refused("c1_placement", {"matching_date": "2026-02-01",
                                      "entrustment_date": "2026-01-31"})

    def test_a_date_in_the_future_is_refused(self):
        self.assertIn("future", self.refused("c1_placement", {"entrustment_date": "2026-10-09"}))

    def test_nothing_else_is_kept(self):
        self.refused("c1_placement", {"placed_on": "2026-02-01"})


class AcrossBoxesTest(Refusals):
    def test_notarized_is_not_before_signed(self):
        check_against_other_sections(entry_for("a5_dvc_notarized"), "2026-03-02", "2026-03-01")
        check_against_other_sections(entry_for("a5_dvc_notarized"), "2026-03-01", "2026-03-01")
        with self.assertRaises(ValidationError) as caught:
            check_against_other_sections(entry_for("a5_dvc_notarized"), "2026-02-28", "2026-03-01")
        self.assertEqual(["The Deed cannot be notarized before it was signed."],
                         caught.exception.messages)

    def test_and_from_the_other_side(self):
        # Moving the signed date past a notarized one already saved.
        with self.assertRaises(ValidationError):
            check_against_other_sections(entry_for("a5_dvc_signed"), "2026-03-05", "2026-03-01")
        check_against_other_sections(entry_for("a5_dvc_signed"), "2026-03-01", "2026-03-01")

    def test_nothing_to_compare_with_is_fine(self):
        check_against_other_sections(entry_for("a5_dvc_notarized"), "2026-03-01", None)
        check_against_other_sections(entry_for("a5_dvc_notarized"), None, "2026-03-01")
        check_against_other_sections(entry_for("a2_circumstances"), "text", "other")

    def test_which_boxes_are_compared(self):
        self.assertEqual("a5_dvc_signed", partner_of("a5_dvc_notarized"))
        self.assertEqual("a5_dvc_notarized", partner_of("a5_dvc_signed"))
        self.assertIsNone(partner_of("a2_sources"))


class DatePreparedTest(Refusals):
    def test_a_date_before_today_after_the_birth(self):
        self.assertEqual(date(2026, 9, 1), clean_date_prepared("2026-09-01", date(2019, 3, 2), TODAY))
        self.assertEqual(TODAY, clean_date_prepared("2026-10-08", date(2019, 3, 2), TODAY))

    def test_none_clears_it(self):
        self.assertIsNone(clean_date_prepared(None, date(2019, 3, 2), TODAY))
        self.assertIsNone(clean_date_prepared("", date(2019, 3, 2), TODAY))

    def test_not_in_the_future_and_not_before_the_birth(self):
        with self.assertRaises(ValidationError) as caught:
            clean_date_prepared("2026-10-09", date(2019, 3, 2), TODAY)
        self.assertIn("future", caught.exception.messages[0])
        with self.assertRaises(ValidationError) as caught:
            clean_date_prepared("2019-03-01", date(2019, 3, 2), TODAY)
        self.assertIn("date of birth", caught.exception.messages[0])

    def test_a_child_with_no_birth_date_is_not_blocked(self):
        self.assertEqual(date(2020, 1, 1), clean_date_prepared("2020-01-01", None, TODAY))


class TheClockTest(SimpleTestCase):
    def test_without_a_date_it_uses_the_clock_and_the_clock_can_be_pinned(self):
        from datetime import datetime, timezone as dt_timezone
        entry = entry_for("a5_dvc_signed")
        with patch("django.utils.timezone.now",
                   return_value=datetime(2026, 10, 8, 4, 0, tzinfo=dt_timezone.utc)):
            self.assertEqual("2026-10-08", clean_value(entry, "2026-10-08"))
            with self.assertRaises(ValidationError):
                clean_value(entry, "2026-10-09")
        with patch("django.utils.timezone.now",
                   return_value=datetime(2026, 10, 10, 4, 0, tzinfo=dt_timezone.utc)):
            self.assertEqual("2026-10-09", clean_value(entry, "2026-10-09"))
