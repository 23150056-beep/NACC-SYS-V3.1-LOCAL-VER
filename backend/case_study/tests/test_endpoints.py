"""Starting a case study, its header, and saving one box at a time."""
from datetime import date
from unittest.mock import patch

from activity.models import ActivityLog
from case_study import views
from case_study.models import CaseStudy, CaseStudySection
from case_study.sections import SCSR_SECTIONS
from case_study.tests.base import NOW, CaseStudyTestCase
from children.models import Child


class StartingOneTest(CaseStudyTestCase):
    def test_a_social_worker_starts_one(self):
        res = self.as_user(self.sw).post(self.url(), {}, format="json")
        self.assertEqual(201, res.status_code)
        self.assertTrue(res.data["exists"])
        self.assertEqual("draft", res.data["status"])
        self.assertIsNone(res.data["date_prepared"])
        study = CaseStudy.objects.get(child=self.child)
        self.assertEqual(self.sw, study.created_by)
        self.assertEqual(0, study.sections.count())

    def test_starting_twice_is_a_conflict(self):
        client = self.as_user(self.sw)
        client.post(self.url(), {}, format="json")
        res = client.post(self.url(), {}, format="json")
        self.assertEqual(409, res.status_code)
        self.assertIn("already", res.data["detail"])
        self.assertEqual(1, CaseStudy.objects.filter(child=self.child).count())

    def test_two_tabs_pressing_start_together(self):
        # The existence check passed in both; the database refuses the second.
        self.start()
        with patch.object(views.CaseStudy.objects, "filter") as lookup:
            lookup.return_value.exists.return_value = False
            res = self.as_user(self.sw).post(self.url(), {}, format="json")
        self.assertEqual(409, res.status_code)

    def test_only_an_adoption_record_has_one(self):
        Child.objects.filter(pk=self.child.pk).update(case_type="Foster Care")
        res = self.as_user(self.sw).post(self.url(), {}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertIn("adoption", res.data["detail"])
        self.assertFalse(CaseStudy.objects.exists())

    def test_a_closed_case_cannot_start_one(self):
        Child.objects.filter(pk=self.child.pk).update(
            status=Child.INACTIVE, case_status=Child.STAGE_TERMINATED)
        res = self.as_user(self.sw).post(self.url(), {}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertIn("closed", res.data["detail"])

    def test_before_one_is_started_the_screen_is_told_what_will_be_filled_in(self):
        res = self.as_user(self.sw).get(self.url())
        self.assertEqual(200, res.status_code)
        self.assertFalse(res.data["exists"])
        self.assertEqual([], res.data["sections"])
        self.assertEqual("Ana Cruz", res.data["record_facts"]["fullname"])
        self.assertIn("a2_circumstances", res.data["seeds"])

    def test_the_event_names_the_child_and_nothing_else(self):
        self.as_user(self.sw).post(self.url(), {}, format="json")
        event = ActivityLog.objects.get(entity_type="CaseStudy")
        self.assertEqual(self.child.pk, event.entity_id)
        self.assertEqual("Ana Cruz", event.entity_label)
        self.assertEqual(self.sw, event.actor)
        self.assertEqual(ActivityLog.RECORD, event.category)

    def test_the_event_reaches_the_social_worker_and_the_isa_not_a_stranger(self):
        self.as_user(self.sw).post(self.url(), {}, format="json")

        def feed(user):
            return [e for e in self.as_user(user).get("/api/activity/").data
                    if e["entity_type"] == "CaseStudy"]

        mine = feed(self.sw)
        self.assertEqual(1, len(mine))
        self.assertEqual("Ana Cruz", mine[0]["entity_label"])
        self.assertEqual(self.child.pk, mine[0]["entity_id"])
        self.assertEqual(1, len(feed(self.isa)))
        self.assertEqual([], feed(self.sw2))
        self.assertEqual([], feed(self.psy))


class TheHeaderTest(CaseStudyTestCase):
    def setUp(self):
        super().setUp()
        self.start()

    def patch_header(self, body, user=None):
        return self.as_user(user or self.sw).patch(self.url(), body, format="json")

    def test_date_prepared(self):
        res = self.patch_header({"date_prepared": "2026-09-30"})
        self.assertEqual(200, res.status_code)
        self.assertEqual("2026-09-30", res.data["date_prepared"])
        self.assertEqual(date(2026, 9, 30), CaseStudy.objects.get().date_prepared)

    def test_it_can_be_cleared(self):
        self.patch_header({"date_prepared": "2026-09-30"})
        res = self.patch_header({"date_prepared": None})
        self.assertIsNone(res.data["date_prepared"])

    def test_not_in_the_future(self):
        res = self.patch_header({"date_prepared": "2026-10-09"})
        self.assertEqual(400, res.status_code)
        self.assertIn("future", res.data["detail"])
        self.assertIsNone(CaseStudy.objects.get().date_prepared)

    def test_not_before_the_child_was_born(self):
        res = self.patch_header({"date_prepared": "2019-03-01"})
        self.assertEqual(400, res.status_code)

    def test_it_must_be_a_date(self):
        self.assertEqual(400, self.patch_header({"date_prepared": "last week"}).status_code)
        self.assertEqual(400, self.patch_header({"date_prepared": 5}).status_code)

    def test_the_custody_question_is_for_a_domestic_relative_adoption_only(self):
        res = self.patch_header({"custody_over_two_years": True})
        self.assertEqual(400, res.status_code)
        self.assertIn("Domestic Relative", res.data["detail"])
        self.assertIsNone(CaseStudy.objects.get().custody_over_two_years)

    def test_a_domestic_relative_adoption_answers_it(self):
        Child.objects.filter(pk=self.child.pk).update(type_of_adoption="Domestic Relative")
        res = self.patch_header({"custody_over_two_years": True})
        self.assertEqual(200, res.status_code)
        self.assertIs(True, res.data["custody_over_two_years"])
        res = self.patch_header({"custody_over_two_years": False})
        self.assertIs(False, res.data["custody_over_two_years"])
        res = self.patch_header({"custody_over_two_years": None})
        self.assertIsNone(res.data["custody_over_two_years"])
        self.assertEqual(400, self.patch_header({"custody_over_two_years": "yes"}).status_code)

    def test_a_long_custody_hides_the_placement_history(self):
        Child.objects.filter(pk=self.child.pk).update(type_of_adoption="Domestic Relative")
        def applies():
            body = self.as_user(self.sw).get(self.url()).data
            return {s["key"]: s["applies"] for s in body["sections"]}["c1_placement"]

        self.assertTrue(applies())
        self.patch_header({"custody_over_two_years": True})
        self.assertFalse(applies())
        self.assertEqual(400, self.save_section("c1_placement", {}).status_code)

    def test_nothing_else_can_be_changed_here(self):
        for body in ({"status": "final"}, {"date_prepared": "2026-09-30", "child": 99}):
            res = self.patch_header(body)
            self.assertEqual(400, res.status_code)
        self.assertEqual("draft", CaseStudy.objects.get().status)

    def test_there_is_nothing_to_change_before_one_is_started(self):
        CaseStudy.objects.all().delete()
        self.assertEqual(404, self.patch_header({"date_prepared": "2026-09-30"}).status_code)

    def test_it_moves_the_time_it_was_last_edited(self):
        CaseStudy.objects.update(updated_at=NOW.replace(month=1))
        res = self.patch_header({"date_prepared": "2026-09-30"})
        self.assertEqual(NOW, CaseStudy.objects.get().updated_at)
        self.assertIsNotNone(res.data["updated_at"])


class SavingABoxTest(CaseStudyTestCase):
    def setUp(self):
        super().setUp()
        self.study = self.start()

    def test_the_first_save_makes_version_one(self):
        res = self.save_section("a2_circumstances", " Left at a clinic. ")
        self.assertEqual(200, res.status_code)
        self.assertEqual(
            {"key", "value", "not_applicable", "version", "updated_by_name", "updated_at",
             "applies", "missing", "case_study_updated_at"}, set(res.data))
        self.assertEqual("a2_circumstances", res.data["key"])
        self.assertEqual("Left at a clinic.", res.data["value"])
        self.assertEqual(1, res.data["version"])
        self.assertEqual("Editha Pascua", res.data["updated_by_name"])
        self.assertIs(True, res.data["applies"])
        self.assertIs(False, res.data["not_applicable"])
        self.assertNotIn("Circumstances of referral or admission", res.data["missing"])
        self.assertIn("Sources of information", res.data["missing"])

    def test_a_save_with_the_current_version_moves_it_on(self):
        self.save_section("a2_circumstances", "one", version=0)
        res = self.save_section("a2_circumstances", "two", version=1)
        self.assertEqual(200, res.status_code)
        self.assertEqual(2, res.data["version"])
        row = CaseStudySection.objects.get(key="a2_circumstances")
        self.assertEqual(("two", 2, self.sw), (row.value, row.version, row.updated_by))

    def test_a_missing_version_means_a_box_never_saved(self):
        self.assertEqual(200, self.save_section("a2_circumstances", "one").status_code)
        self.assertEqual(409, self.save_section("a2_circumstances", "again").status_code)

    def test_a_stale_version_is_a_conflict_that_hands_back_what_is_saved(self):
        self.save_section("a2_circumstances", "first", version=0)
        self.save_section("a2_circumstances", "second", version=1, user=self.sw)
        res = self.save_section("a2_circumstances", "mine", version=1)
        self.assertEqual(409, res.status_code)
        self.assertEqual(
            "This section was saved from another tab or by someone else since you opened it.",
            res.data["detail"])
        current = res.data["current"]
        self.assertEqual(
            {"value", "not_applicable", "version", "updated_by_name", "updated_at"}, set(current))
        self.assertEqual("second", current["value"])
        self.assertEqual(2, current["version"])
        self.assertEqual("Editha Pascua", current["updated_by_name"])
        # And nothing was overwritten.
        self.assertEqual("second", CaseStudySection.objects.get(key="a2_circumstances").value)

    def test_saying_nothing_has_been_saved_when_something_has_is_a_conflict(self):
        self.save_section("a2_circumstances", "first")
        res = self.save_section("a2_circumstances", "mine", version=0)
        self.assertEqual(409, res.status_code)
        self.assertEqual(1, res.data["current"]["version"])

    def test_expecting_a_version_that_was_never_there_is_a_conflict(self):
        res = self.save_section("a2_circumstances", "mine", version=3)
        self.assertEqual(409, res.status_code)
        self.assertEqual(0, res.data["current"]["version"])
        self.assertIsNone(res.data["current"]["value"])

    def test_two_first_saves_at_once_the_second_loses(self):
        """Both looked, found no row, and tried to make one: the unique
        constraint refuses the second, and it is told so like any conflict."""
        CaseStudySection.objects.create(
            case_study=self.study, key="a2_circumstances", value="theirs",
            updated_by=self.sw2)
        real = views._stored_row
        calls = []

        def looked_too_early(case_study, key):
            calls.append(key)
            return None if len(calls) == 1 else real(case_study, key)

        with patch.object(views, "_stored_row", side_effect=looked_too_early):
            res = self.save_section("a2_circumstances", "mine", version=0)
        self.assertEqual(409, res.status_code)
        self.assertEqual("theirs", res.data["current"]["value"])
        self.assertEqual(1, CaseStudySection.objects.filter(key="a2_circumstances").count())
        self.assertEqual("theirs", CaseStudySection.objects.get(key="a2_circumstances").value)

    def test_a_save_moves_the_case_study_last_edited_time_and_writes_no_event(self):
        CaseStudy.objects.filter(pk=self.study.pk).update(updated_at=NOW.replace(year=2026, month=1))
        before = ActivityLog.objects.count()
        self.save_section("a2_circumstances", "text")
        self.assertEqual(NOW, CaseStudy.objects.get(pk=self.study.pk).updated_at)
        self.assertEqual(before, ActivityLog.objects.count())

    def test_the_version_must_be_a_whole_number(self):
        for bad in (-1, "x", 1.5, True, [1], "9" * 10, "1" * 5000):
            res = self.as_user(self.sw).put(
                self.section_url("a2_circumstances"),
                {"value": "x", "expected_version": bad}, format="json")
            self.assertEqual(400, res.status_code, bad)

    def test_an_unknown_key_is_refused(self):
        res = self.save_section("a9_nothing", "x")
        self.assertEqual(400, res.status_code)
        self.assertIn("no such section", res.data["detail"])
        self.assertFalse(CaseStudySection.objects.exists())

    def test_a_box_that_does_not_apply_is_refused(self):
        # A surrendered child: no facts of abandonment.
        res = self.save_section("a5_abandonment", "text")
        self.assertEqual(400, res.status_code)
        self.assertIn("does not apply", res.data["detail"])
        # An abandoned one: no Deed.
        Child.objects.filter(pk=self.child.pk).update(case_category="Abandoned")
        self.assertEqual(400, self.save_section("a5_dvc_signed", "2026-03-01").status_code)
        self.assertEqual(200, self.save_section("a5_abandonment", "Found at a bus stop.").status_code)

    def test_a_refusal_is_a_sentence_and_saves_nothing(self):
        res = self.save_section("a3_immunizations", [{"vaccine": "BCG"}])
        self.assertEqual(400, res.status_code)
        self.assertEqual({"detail"}, set(res.data))
        self.assertIsInstance(res.data["detail"], str)
        self.assertFalse(CaseStudySection.objects.exists())

    def test_not_applicable_only_where_the_template_allows_it(self):
        res = self.save_section("a2_circumstances", None, not_applicable=True)
        self.assertEqual(400, res.status_code)
        self.assertIn("Not applicable", res.data["detail"])
        self.assertEqual(400, self.save_section("a2_sources", None, not_applicable="yes").status_code)

    def row(self, key):
        return CaseStudySection.objects.get(key=key)

    def tick(self, key, version, **body):
        """A PUT that only says Not applicable (or not), sending no value."""
        return self.as_user(self.sw).put(
            self.section_url(key),
            {"not_applicable": body.pop("not_applicable"), "expected_version": version, **body},
            format="json")

    def test_ticking_not_applicable_counts_as_answered_and_returns_the_kept_value(self):
        self.save_section("a4_family_composition", [{
            "name": "Maria", "relationship": "Birth mother", "age": "", "sex": "",
            "civil_status": "", "education": "", "employment_income": ""}])
        res = self.tick("a4_family_composition", 1, not_applicable=True)
        self.assertEqual(200, res.status_code)
        self.assertTrue(res.data["not_applicable"])
        self.assertEqual("Maria", res.data["value"][0]["name"])
        self.assertEqual(2, res.data["version"])
        self.assertNotIn("Family composition", res.data["missing"])

    def test_ticking_it_on_a_box_never_filled_counts_as_answered_too(self):
        res = self.save_section("a4_family_composition", None, not_applicable=True)
        self.assertEqual(200, res.status_code)
        self.assertIsNone(res.data["value"])
        self.assertNotIn("Family composition", res.data["missing"])

    def test_not_applicable_hides_text_it_does_not_delete_it(self):
        self.save_section("a4_family_description", "Both parents are farmers.")
        ticked = self.tick("a4_family_description", 1, not_applicable=True)
        self.assertTrue(ticked.data["not_applicable"])
        self.assertEqual("Both parents are farmers.", ticked.data["value"])
        self.assertEqual("Both parents are farmers.", self.row("a4_family_description").value)
        # Counted as answered while ticked ...
        self.assertNotIn("Family description", ticked.data["missing"])
        # ... and the text is still there when it is unticked.
        unticked = self.tick("a4_family_description", 2, not_applicable=False)
        self.assertFalse(unticked.data["not_applicable"])
        self.assertEqual("Both parents are farmers.", unticked.data["value"])
        self.assertEqual("Both parents are farmers.", self.row("a4_family_description").value)
        self.assertEqual(3, unticked.data["version"])

    def test_a_null_value_with_the_tick_keeps_the_text_too(self):
        self.save_section("a4_family_description", "Both parents are farmers.")
        res = self.save_section("a4_family_description", None, version=1, not_applicable=True)
        self.assertTrue(res.data["not_applicable"])
        self.assertEqual("Both parents are farmers.", res.data["value"])

    def test_a_value_sent_with_the_tick_is_cleaned_and_stored_as_usual(self):
        self.save_section("a4_family_description", "Old text.")
        res = self.save_section("a4_family_description", "  New text.  ", version=1,
                                not_applicable=True)
        self.assertEqual(200, res.status_code)
        self.assertEqual("New text.", res.data["value"])
        self.assertEqual("New text.", self.row("a4_family_description").value)
        # And it is held to the rules: a value that is refused is refused.
        too_long = self.save_section("a4_family_description", "x" * 20001, version=2,
                                     not_applicable=True)
        self.assertEqual(400, too_long.status_code)
        self.assertEqual("New text.", self.row("a4_family_description").value)

    def test_unticking_it_and_writing_again(self):
        self.save_section("a4_family_description", None, not_applicable=True)
        res = self.save_section("a4_family_description", "Both parents are farmers.", version=1)
        self.assertFalse(res.data["not_applicable"])
        self.assertEqual("Both parents are farmers.", res.data["value"])

    def test_saying_null_with_the_tick_off_still_empties_the_box(self):
        self.save_section("a4_family_description", "Both parents are farmers.")
        res = self.save_section("a4_family_description", None, version=1)
        self.assertEqual("", res.data["value"])
        self.assertIn("Family description", res.data["missing"])

    def test_a_psychologist_does_not_read_text_that_is_hidden(self):
        self.save_section("a4_family_description", "Both parents are farmers.")
        self.tick("a4_family_description", 1, not_applicable=True)
        mine = {s["key"]: s for s in self.as_user(self.sw).get(self.url()).data["sections"]}
        theirs = {s["key"]: s for s in self.as_user(self.psy).get(self.url()).data["sections"]}
        self.assertEqual("Both parents are farmers.", mine["a4_family_description"]["value"])
        self.assertTrue(theirs["a4_family_description"]["not_applicable"])
        self.assertIsNone(theirs["a4_family_description"]["value"])

    def test_a_box_can_be_emptied(self):
        self.save_section("a2_sources", ["The child"])
        res = self.save_section("a2_sources", [], version=1)
        self.assertEqual([], res.data["value"])
        self.assertIn("Sources of information", res.data["missing"])

    def test_the_deed_is_not_notarized_before_it_was_signed(self):
        self.assertEqual(200, self.save_section("a5_dvc_signed", "2026-03-10").status_code)
        res = self.save_section("a5_dvc_notarized", "2026-03-09")
        self.assertEqual(400, res.status_code)
        self.assertIn("notarized before it was signed", res.data["detail"])
        self.assertEqual(200, self.save_section("a5_dvc_notarized", "2026-03-10").status_code)

    def test_and_the_signed_date_cannot_be_moved_past_a_notarized_one(self):
        self.save_section("a5_dvc_signed", "2026-03-10")
        self.save_section("a5_dvc_notarized", "2026-03-12")
        res = self.save_section("a5_dvc_signed", "2026-03-15", version=1)
        self.assertEqual(400, res.status_code)
        self.assertEqual(200, self.save_section("a5_dvc_signed", "2026-03-11", version=1).status_code)

    def test_notarizing_first_is_fine_when_nothing_is_signed_yet(self):
        self.assertEqual(200, self.save_section("a5_dvc_notarized", "2026-03-12").status_code)

    def test_a_closed_case_cannot_be_written(self):
        Child.objects.filter(pk=self.child.pk).update(
            status=Child.INACTIVE, case_status=Child.STAGE_TERMINATED)
        res = self.save_section("a2_sources", ["x"])
        self.assertEqual(400, res.status_code)
        self.assertIn("closed", res.data["detail"])
        self.assertFalse(CaseStudySection.objects.exists())

    def test_a_case_that_is_no_longer_an_adoption_cannot_be_written_but_keeps_its_rows(self):
        self.save_section("a2_sources", ["The child"])
        Child.objects.filter(pk=self.child.pk).update(case_type="Foster Care")
        res = self.save_section("a2_sources", ["x"], version=1)
        self.assertEqual(400, res.status_code)
        self.assertIn("adoption", res.data["detail"])
        self.assertEqual(["The child"], CaseStudySection.objects.get(key="a2_sources").value)
        body = self.as_user(self.sw).get(self.url()).data
        self.assertTrue(body["read_only"])

    def test_a_final_case_study_must_be_reopened_first(self):
        CaseStudy.objects.filter(pk=self.study.pk).update(status=CaseStudy.FINAL)
        res = self.save_section("a2_sources", ["x"])
        self.assertEqual(400, res.status_code)
        self.assertIn("Reopen", res.data["detail"])
        self.assertEqual(400, self.as_user(self.sw).patch(
            self.url(), {"date_prepared": "2026-09-30"}, format="json").status_code)

    def test_there_is_nothing_to_save_into_before_one_is_started(self):
        CaseStudy.objects.all().delete()
        self.assertEqual(404, self.save_section("a2_sources", ["x"]).status_code)


class WhatTheSocialWorkerGetsTest(CaseStudyTestCase):
    def test_every_box_in_the_catalogues_order_with_its_state(self):
        study = self.start()
        CaseStudySection.objects.create(
            case_study=study, key="a2_sources", value=["The child"], updated_by=self.sw)
        body = self.as_user(self.sw).get(self.url()).data
        self.assertEqual(
            {"exists", "read_only", "read_only_reason", "status", "date_prepared",
             "custody_over_two_years", "custody_pre_answer", "updated_at", "sections",
             "missing", "can_finalize", "finals", "record_facts", "seeds"}, set(body))
        self.assertEqual([e["key"] for e in SCSR_SECTIONS], [s["key"] for s in body["sections"]])
        first = body["sections"][0]
        self.assertEqual(
            {"key", "value", "not_applicable", "version", "updated_by_name", "updated_at",
             "applies"}, set(first))
        self.assertEqual((["The child"], 1, "Editha Pascua"),
                         (first["value"], first["version"], first["updated_by_name"]))
        second = body["sections"][1]
        self.assertEqual((None, False, 0, None, None),
                         (second["value"], second["not_applicable"], second["version"],
                          second["updated_by_name"], second["updated_at"]))

    def test_which_boxes_apply_to_a_surrendered_regular_adoption(self):
        self.start()
        applies = {s["key"]: s["applies"]
                   for s in self.as_user(self.sw).get(self.url()).data["sections"]}
        self.assertTrue(applies["a5_dvc_signed"] and applies["a5_counselling"])
        self.assertTrue(applies["c3_stc_report"] and applies["c1_placement"])
        self.assertFalse(applies["a5_abandonment"] or applies["a5_search_efforts"])

    def test_which_boxes_apply_to_an_adult_adoption_of_an_abandoned_person(self):
        Child.objects.filter(pk=self.child.pk).update(
            case_category="Without Known Parents", type_of_adoption="Adult")
        self.start()
        applies = {s["key"]: s["applies"]
                   for s in self.as_user(self.sw).get(self.url()).data["sections"]}
        self.assertTrue(applies["a5_abandonment"] and applies["a5_search_efforts"])
        self.assertFalse(applies["a5_dvc_signed"] or applies["c1_placement"]
                         or applies["c3_stc_report"])
