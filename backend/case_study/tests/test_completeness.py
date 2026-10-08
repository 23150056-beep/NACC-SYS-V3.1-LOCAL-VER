"""What is still missing from a case study (case_study/completeness.py).

The one function behind the social worker's list, the ISA's count and, in the
next phase, the refusal to finalize. Titles come back in the catalogue's order.
"""
from datetime import date

from case_study.completeness import DATE_PREPARED, missing_sections
from case_study.models import CaseStudy, CaseStudySection
from case_study.sections import SCSR_SECTIONS, applies
from case_study.tests.base import CaseStudyTestCase
from children.models import Child

TITLES = {e["key"]: e["title"] for e in SCSR_SECTIONS}

GOOD = {
    "prose": "Written text.",
    "list": ["One line"],
    "table": None,  # per section, below
    "pap_table": {"female": {"full_name": "Maria Reyes"}, "male": {}},
    "date": "2026-03-01",
    "tick": True,
    "measurements": {"height_cm": 98, "weight_kg": 14},
    "placement": {"entrustment_date": "2026-02-01"},
}


def good_value(entry):
    if entry["kind"] == "table":
        row = {}
        for column in entry["columns"]:
            if column["type"] == "choice":
                row[column["key"]] = column["options"][0]["value"]
            elif column["type"] in ("date", "partial_date"):
                row[column["key"]] = "2026-01-01"
            elif column["type"] == "int":
                row[column["key"]] = "5"
            else:
                row[column["key"]] = "text"
        return [row]
    return GOOD[entry["kind"]]


class CompletenessTest(CaseStudyTestCase):
    def setUp(self):
        super().setUp()
        self.study = self.start()

    def set(self, key, value=None, **fields):
        row, _ = CaseStudySection.objects.update_or_create(
            case_study=self.study, key=key, defaults={"value": value, "updated_by": self.sw, **fields})
        return row

    def fill_everything(self):
        CaseStudy.objects.filter(pk=self.study.pk).update(date_prepared=date(2026, 9, 1))
        self.study.refresh_from_db()
        child = Child.objects.get(pk=self.child.pk)
        for entry in SCSR_SECTIONS:
            if applies(entry, child, self.study):
                self.set(entry["key"], good_value(entry))

    def missing(self):
        return missing_sections(CaseStudy.objects.get(pk=self.study.pk))

    def test_a_new_one_lacks_the_date_and_every_applicable_box_in_order(self):
        wanted = [DATE_PREPARED] + [e["title"] for e in SCSR_SECTIONS
                                    if applies(e, self.child, self.study)]
        self.assertEqual(wanted, self.missing())

    def test_a_box_that_does_not_apply_is_not_missing(self):
        missing = self.missing()
        # A surrendered child: no facts of abandonment, no search efforts.
        self.assertNotIn(TITLES["a5_abandonment"], missing)
        self.assertNotIn(TITLES["a5_search_efforts"], missing)
        self.assertIn(TITLES["a5_dvc_signed"], missing)

    def test_everything_filled_in_leaves_nothing_missing(self):
        self.fill_everything()
        self.assertEqual([], self.missing())

    def test_filling_a_box_takes_it_off_the_list(self):
        self.set("a2_circumstances", "Left at a clinic.")
        self.assertNotIn(TITLES["a2_circumstances"], self.missing())

    def test_blank_text_does_not_count(self):
        self.set("a2_circumstances", "   ")
        self.set("a2_sources", [])
        self.set("a3_immunizations", [])
        self.assertTrue({TITLES["a2_circumstances"], TITLES["a2_sources"],
                         TITLES["a3_immunizations"]} <= set(self.missing()))

    def test_not_applicable_counts_as_answered_only_where_allowed(self):
        self.set("a4_family_composition", None, not_applicable=True)
        self.set("a2_sources", None, not_applicable=True)
        missing = self.missing()
        self.assertNotIn(TITLES["a4_family_composition"], missing)
        self.assertIn(TITLES["a2_sources"], missing)

    def test_a_ticked_box_is_answered_whatever_text_it_keeps(self):
        # Not applicable hides the text and keeps it (views.py), so the tick,
        # not the text, decides: kept text, a blank one and none all count.
        title = TITLES["a4_family_description"]
        self.set("a4_family_description", "Both parents are farmers.", not_applicable=True)
        self.assertNotIn(title, self.missing())
        self.set("a4_family_description", "", not_applicable=True)
        self.assertNotIn(title, self.missing())
        self.set("a4_family_description", None, not_applicable=True)
        self.assertNotIn(title, self.missing())
        # Unticked, the kept text is the answer; with none, it is missing again.
        self.set("a4_family_description", "Both parents are farmers.", not_applicable=False)
        self.assertNotIn(title, self.missing())
        self.set("a4_family_description", "", not_applicable=False)
        self.assertIn(title, self.missing())

    def test_the_date_prepared_is_required(self):
        self.fill_everything()
        CaseStudy.objects.filter(pk=self.study.pk).update(date_prepared=None)
        self.assertEqual([DATE_PREPARED], self.missing())

    def test_both_surrendered_ticks_are_required(self):
        self.fill_everything()
        self.set("a5_aware_irrevocable", True)
        self.set("a5_explained_vernacular", False)
        self.assertEqual([TITLES["a5_explained_vernacular"]], self.missing())
        self.set("a5_aware_irrevocable", False)
        self.set("a5_explained_vernacular", True)
        self.assertEqual([TITLES["a5_aware_irrevocable"]], self.missing())

    def test_the_ticks_are_not_asked_of_a_child_who_was_not_surrendered(self):
        Child.objects.filter(pk=self.child.pk).update(case_category="Abandoned")
        self.fill_everything()
        self.assertEqual([], self.missing())
        self.assertNotIn(TITLES["a5_aware_irrevocable"], self.missing())

    def test_at_least_one_adoptive_parent_must_be_named(self):
        self.fill_everything()
        self.set("b1_paps", {"female": {"religion": "Catholic"}, "male": {}})
        self.assertEqual([TITLES["b1_paps"]], self.missing())
        self.set("b1_paps", {"female": {}, "male": {"full_name": "Jose Reyes"}})
        self.assertEqual([], self.missing())

    def test_the_entrustment_date_is_required_where_the_placement_history_applies(self):
        self.fill_everything()
        self.set("c1_placement", {"racco_cpa": "RACCO 1", "matching_date": "2026-01-01"})
        self.assertEqual([TITLES["c1_placement"]], self.missing())

    def test_and_not_where_it_does_not_apply(self):
        Child.objects.filter(pk=self.child.pk).update(type_of_adoption="Adult")
        self.fill_everything()
        self.assertFalse(CaseStudySection.objects.filter(key="c1_placement").exists())
        self.assertEqual([], self.missing())
        self.assertNotIn(TITLES["c1_placement"], self.missing())

    def test_a_long_custody_takes_the_placement_history_off_the_list(self):
        Child.objects.filter(pk=self.child.pk).update(type_of_adoption="Domestic Relative")
        self.assertIn(TITLES["c1_placement"], self.missing())
        CaseStudy.objects.filter(pk=self.study.pk).update(custody_over_two_years=True)
        self.assertNotIn(TITLES["c1_placement"], self.missing())

    def test_height_and_weight_are_both_needed(self):
        self.fill_everything()
        self.set("c4_measurements", {"height_cm": 98})
        self.assertEqual([TITLES["c4_measurements"]], self.missing())

    def test_a_supervised_trial_custody_report_only_for_the_types_that_have_one(self):
        self.assertIn(TITLES["c3_stc_report"], self.missing())
        Child.objects.filter(pk=self.child.pk).update(type_of_adoption="Step-parent")
        self.assertNotIn(TITLES["c3_stc_report"], self.missing())

    def test_a_section_that_is_no_longer_in_the_catalogue_is_ignored(self):
        self.fill_everything()
        self.set("z9_retired", "old text")
        self.assertEqual([], self.missing())

    def test_it_reads_the_prefetched_sections_without_more_queries(self):
        study = CaseStudy.objects.prefetch_related("sections").get(pk=self.study.pk)
        study.child = self.child
        with self.assertNumQueries(0):
            missing_sections(study)
