"""The Social Case Study Report's boxes: the server's list and the browser's.

backend/case_study/sections.py is the authority and frontend/src/config/scsr.js
its copy, the same arrangement as children/intake.py and caseData.js. The first
class pins the two together, because the failure when they drift is quiet: the
screen shows a box the server refuses to save, or hides one it requires.

The second holds the catalogue to the rules that keep saved text safe: a key
is never renamed or reused.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.test import SimpleTestCase

from case_study import sections
from case_study.sections import (
    ADOPTION, ADOPTION_ONLY_BLOCKS, ALL_KEYS_EVER, BLOCKS, KINDS, PAP_CONTACT_ROWS,
    PAP_ROWS, RULES, SCSR_SECTIONS, applies)
from children.models import Child

SCSR_JS = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "config" / "scsr.js")


def _js_json(source, name):
    """`export const NAME = [ ...JSON... ];` as Python data. The browser's file
    keeps these arrays in JSON on purpose, so this needs no JavaScript."""
    match = re.search(r"export\s+const\s+" + re.escape(name) + r"\s*=\s*(\[.*?\n\]);",
                      source, re.S)
    return json.loads(match.group(1)) if match else None


class TheBrowserAndTheServerAgreeTest(SimpleTestCase):
    def setUp(self):
        self.assertTrue(SCSR_JS.exists(), f"{SCSR_JS} has moved; this test is pinned to it")
        self.js = SCSR_JS.read_text(encoding="utf-8")
        self.browser = _js_json(self.js, "SCSR_SECTIONS")
        self.assertIsNotNone(self.browser, "SCSR_SECTIONS is not a JSON array in scsr.js")

    def test_the_same_boxes_in_the_same_order(self):
        self.assertEqual([e["key"] for e in SCSR_SECTIONS], [e["key"] for e in self.browser])

    def test_the_blocks_and_their_numbers(self):
        self.assertEqual([(e["key"], e["block"], e["number"]) for e in SCSR_SECTIONS],
                         [(e["key"], e["block"], e["number"]) for e in self.browser])
        self.assertEqual(list(BLOCKS), _js_json(self.js, "SCSR_BLOCKS"))

    def test_the_kinds(self):
        self.assertEqual([(e["key"], e["kind"]) for e in SCSR_SECTIONS],
                         [(e["key"], e["kind"]) for e in self.browser])

    def test_which_boxes_may_be_not_applicable(self):
        self.assertEqual([(e["key"], e["may_be_na"]) for e in SCSR_SECTIONS],
                         [(e["key"], e["may_be_na"]) for e in self.browser])

    def test_the_columns_of_every_table(self):
        self.assertEqual([(e["key"], e["columns"]) for e in SCSR_SECTIONS],
                         [(e["key"], e["columns"]) for e in self.browser])

    def test_who_each_box_applies_to(self):
        self.assertEqual([(e["key"], e["applies"]) for e in SCSR_SECTIONS],
                         [(e["key"], e["applies"]) for e in self.browser])

    def test_the_titles_and_the_guidance(self):
        # Not ids, but the screen and the print read them: one copy changed
        # alone would make the two screens disagree.
        self.assertEqual([(e["key"], e["title"], e["hints"]) for e in SCSR_SECTIONS],
                         [(e["key"], e["title"], e["hints"]) for e in self.browser])

    def test_every_entry_is_identical(self):
        self.assertEqual(list(SCSR_SECTIONS), self.browser)

    def test_the_adoptive_parents_rows(self):
        self.assertEqual(list(PAP_ROWS), _js_json(self.js, "PAP_ROWS"))

    @skipUnless(shutil.which("node"), "node is not installed here")
    def test_the_browsers_applies_rule_gives_the_servers_answers(self):
        """Run the browser's `appliesTo` over every combination that decides it,
        and compare with the server's `applies`."""
        categories = [c for c, _ in Child.CASE_CATEGORY_CHOICES] + [""]
        adoptions = [t for t, _ in Child.TYPE_OF_ADOPTION_CHOICES] + [""]
        # Every case type: blocks B and C apply to an Adoption record only.
        case_types = [t for t, _ in Child.CASE_TYPE_CHOICES] + [""]
        custody = [None, True, False]
        script = (
            f"import {{ SCSR_SECTIONS, appliesTo }} from {json.dumps(SCSR_JS.as_uri())};\n"
            f"const cats = {json.dumps(categories)}, types = {json.dumps(adoptions)};\n"
            f"const caseTypes = {json.dumps(case_types)};\n"
            f"const custody = {json.dumps(custody)};\n"
            "const out = [];\n"
            "for (const entry of SCSR_SECTIONS) for (const ct of caseTypes)\n"
            "  for (const c of cats) for (const t of types)\n"
            "    for (const k of custody)\n"
            "      out.push(appliesTo(entry,\n"
            "        { case_type: ct, case_category: c, type_of_adoption: t },\n"
            "        k === null ? null : { custody_over_two_years: k }));\n"
            "console.log(JSON.stringify(out));\n")
        done = subprocess.run(["node", "--input-type=module", "-e", script],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        browser = json.loads(done.stdout)
        server = []
        labels = []
        for entry in SCSR_SECTIONS:
            for ct in case_types:
                for c in categories:
                    for t in adoptions:
                        for k in custody:
                            child = SimpleNamespace(
                                case_type=ct, case_category=c, type_of_adoption=t)
                            case_study = (None if k is None
                                          else SimpleNamespace(custody_over_two_years=k))
                            server.append(applies(entry, child, case_study))
                            labels.append((entry["key"], ct, c, t, k))
        self.assertEqual(len(server), len(browser))
        # Not assertEqual on the two lists: tens of thousands of answers, and a
        # failing diff of that size takes minutes. Name the first few instead.
        differ = [(labels[i], server[i], browser[i])
                  for i in range(len(server)) if server[i] != browser[i]]
        self.assertEqual([], differ[:5], f"{len(differ)} answers differ (key, case type, "
                         "category, adoption type, custody), server then browser")


class TheCatalogueKeepsItsPromisesTest(SimpleTestCase):
    def test_no_key_is_listed_twice(self):
        keys = [e["key"] for e in SCSR_SECTIONS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_key_is_one_that_was_issued(self):
        self.assertEqual(set(), {e["key"] for e in SCSR_SECTIONS} - set(ALL_KEYS_EVER))

    def test_the_list_of_issued_keys_has_no_duplicates(self):
        # A reused key would hand one box's old text to another.
        self.assertEqual(len(ALL_KEYS_EVER), len(set(ALL_KEYS_EVER)))

    def test_a_key_fits_the_column(self):
        self.assertTrue(all(len(key) <= 60 for key in ALL_KEYS_EVER))

    def test_the_ids_the_design_fixes_for_block_b(self):
        wanted = ["b1_paps", "b2_household", "b3_family_background", "b4_motivation",
                  "b5_child_care_plans", "b6_marital_history", "b7_children_in_family",
                  "b8_other_individuals", "b9_family_attitude", "b10_parenting_experience",
                  "b11_employment_finances", "b12_home_community", "b13_identity",
                  "b14_health_history", "b15_references_clearances", "b16_trainings",
                  "b17_adoption_telling"]
        self.assertEqual(wanted, [e["key"] for e in SCSR_SECTIONS if e["block"] == "B"])

    def test_the_ids_the_design_fixes_for_blocks_a_and_c(self):
        self.assertEqual(
            ["a2_sources", "a2_circumstances", "a3_description", "a3_medical",
             "a3_psych_highlights", "a3_immunizations", "a3_development",
             "a4_family_composition", "a4_family_description", "a5_summary",
             "a5_dvc_signed", "a5_dvc_notarized", "a5_counselling", "a5_assistance",
             "a5_aware_irrevocable", "a5_explained_vernacular", "a5_abandonment",
             "a5_search_efforts"],
            [e["key"] for e in SCSR_SECTIONS if e["block"] == "A"])
        self.assertEqual(
            ["c1_placement", "c2_on_placement", "c3_stc_report", "c4_measurements",
             "c4_functioning", "c5_assessment", "c6_recommendation"],
            [e["key"] for e in SCSR_SECTIONS if e["block"] == "C"])

    def test_the_blocks_come_in_order(self):
        order = [e["block"] for e in SCSR_SECTIONS]
        self.assertEqual(sorted(order), order)
        self.assertEqual({"A", "B", "C"}, set(order))

    def test_every_entry_is_whole(self):
        for entry in SCSR_SECTIONS:
            self.assertEqual(
                {"key", "block", "number", "title", "kind", "columns", "may_be_na",
                 "applies", "hints"}, set(entry), entry["key"])
            self.assertIn(entry["kind"], KINDS)
            self.assertIn(entry["applies"], RULES)
            self.assertTrue(entry["title"] and entry["number"])
            self.assertTrue(entry["hints"], f"{entry['key']} has no guidance")
            self.assertTrue(all(isinstance(h, str) and h for h in entry["hints"]))

    def test_only_tables_have_columns_and_every_table_has_them(self):
        for entry in SCSR_SECTIONS:
            if entry["kind"] == "table":
                self.assertTrue(entry["columns"], entry["key"])
                keys = [c["key"] for c in entry["columns"]]
                self.assertEqual(len(keys), len(set(keys)), entry["key"])
                for column in entry["columns"]:
                    self.assertIn(column["type"], sections.COLUMN_TYPES)
                    self.assertEqual(column["type"] == "choice", "options" in column)
            else:
                self.assertEqual([], entry["columns"], entry["key"])

    def test_titles_are_different_so_a_missing_list_is_unambiguous(self):
        titles = [e["title"] for e in SCSR_SECTIONS]
        self.assertEqual(len(titles), len(set(titles)))

    def test_only_the_boxes_the_design_names_may_be_not_applicable(self):
        self.assertEqual(
            {"a4_family_composition", "a4_family_description", "b6_marital_history",
             "b7_children_in_family", "b8_other_individuals", "c3_stc_report"},
            {e["key"] for e in SCSR_SECTIONS if e["may_be_na"]})

    def test_seventeen_adoptive_parent_rows_with_their_own_ids(self):
        self.assertEqual(17, len(PAP_ROWS))
        ids = [r["id"] for r in PAP_ROWS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(set(PAP_CONTACT_ROWS) <= set(ids))

    def test_who_each_rule_applies_to(self):
        def child(category="", adoption=""):
            return SimpleNamespace(case_type=ADOPTION, case_category=category,
                                   type_of_adoption=adoption)

        def entry(rule):
            return {"block": "B", "applies": rule}

        self.assertTrue(applies(entry("always"), child(), None))
        self.assertTrue(applies(entry("surrendered"), child("Surrendered"), None))
        self.assertFalse(applies(entry("surrendered"), child("Abandoned"), None))
        for category in ("Abandoned", "Without Known Parents"):
            self.assertTrue(applies(entry("abandoned"), child(category), None))
        self.assertFalse(applies(entry("abandoned"), child("Surrendered"), None))
        for adoption in ("Regular", "IP", "Foster-Adopt"):
            self.assertTrue(applies(entry("stc"), child(adoption=adoption), None))
        for adoption in ("Domestic Relative", "Step-parent", "Adult", "SIBRA", ""):
            self.assertFalse(applies(entry("stc"), child(adoption=adoption), None))

    def test_placement_history_is_hidden_for_adult_and_for_a_long_custody(self):
        rule = {"block": "C", "applies": "placement_history"}

        def child(adoption):
            return SimpleNamespace(case_type=ADOPTION, case_category="",
                                   type_of_adoption=adoption)

        yes, no, unanswered = (SimpleNamespace(custody_over_two_years=v)
                               for v in (True, False, None))
        self.assertFalse(applies(rule, child("Adult"), None))
        self.assertFalse(applies(rule, child("Adult"), no))
        self.assertFalse(applies(rule, child("Domestic Relative"), yes))
        self.assertTrue(applies(rule, child("Domestic Relative"), no))
        self.assertTrue(applies(rule, child("Domestic Relative"), unanswered))
        self.assertTrue(applies(rule, child("Domestic Relative"), None))
        # The answer is asked for a Domestic Relative adoption only.
        self.assertTrue(applies(rule, child("Regular"), yes))

    def test_the_values_the_rules_compare_with_are_ones_the_record_stores(self):
        categories = {c for c, _ in Child.CASE_CATEGORY_CHOICES}
        adoptions = {t for t, _ in Child.TYPE_OF_ADOPTION_CHOICES}
        self.assertTrue(set(sections.SURRENDERED) | set(sections.ABANDONED) <= categories)
        self.assertTrue(set(sections.STC_TYPES) | {sections.ADULT, sections.DOMESTIC_RELATIVE}
                        <= adoptions)


class OnlyAnAdoptionRecordHasBlocksBAndCTest(SimpleTestCase):
    """Owner's decision, 10 Oct 2026: block A is the child's profile for every
    case type; the adoptive parents and the placement are an adoption's."""

    def child(self, case_type, category="Surrendered", adoption="Regular"):
        return SimpleNamespace(case_type=case_type, case_category=category,
                               type_of_adoption=adoption)

    def keys(self, child, block=None, case_study=None):
        return [e["key"] for e in SCSR_SECTIONS
                if (block is None or e["block"] == block) and applies(e, child, case_study)]

    def test_the_blocks_that_belong_to_an_adoption(self):
        self.assertEqual(("B", "C"), ADOPTION_ONLY_BLOCKS)
        self.assertEqual("Adoption", ADOPTION)

    def test_every_case_type_that_is_not_an_adoption_has_block_a_only(self):
        case_types = [t for t, _ in Child.CASE_TYPE_CHOICES if t != ADOPTION]
        self.assertGreaterEqual(len(case_types), 5)
        for case_type in case_types:
            child = self.child(case_type)
            self.assertEqual([], self.keys(child, "B"), case_type)
            self.assertEqual([], self.keys(child, "C"), case_type)
            self.assertTrue(self.keys(child, "A"), case_type)

    def test_block_a_keeps_its_own_rules_for_every_case_type(self):
        for case_type in [t for t, _ in Child.CASE_TYPE_CHOICES] + [""]:
            surrendered = self.keys(self.child(case_type, "Surrendered"), "A")
            abandoned = self.keys(self.child(case_type, "Abandoned"), "A")
            unknown = self.keys(self.child(case_type, "Without Known Parents"), "A")
            dependent = self.keys(self.child(case_type, "Dependent"), "A")
            self.assertIn("a5_dvc_signed", surrendered, case_type)
            self.assertNotIn("a5_abandonment", surrendered, case_type)
            self.assertIn("a5_abandonment", abandoned, case_type)
            self.assertNotIn("a5_dvc_signed", abandoned, case_type)
            self.assertIn("a5_search_efforts", unknown, case_type)
            for key in ("a5_dvc_signed", "a5_abandonment"):
                self.assertNotIn(key, dependent, case_type)
            # The rest of block A is everyone's, whatever the category.
            for key in ("a2_sources", "a3_immunizations", "a4_family_composition", "a5_summary"):
                self.assertIn(key, dependent, case_type)

    def test_an_adoption_has_all_three_blocks_as_before(self):
        child = self.child("Adoption")
        for block in ("A", "B", "C"):
            self.assertTrue(self.keys(child, block), block)
        self.assertIn("b1_paps", self.keys(child))
        self.assertIn("c3_stc_report", self.keys(child))

    def test_a_record_moved_away_from_adoption_and_back_is_asked_the_same_again(self):
        adoption = self.keys(self.child("Adoption"))
        self.assertEqual(adoption, self.keys(self.child("Adoption")))
        self.assertNotEqual(adoption, self.keys(self.child("Foster Care")))
        self.assertEqual(
            [k for k in adoption if not k.startswith(("b", "c"))],
            self.keys(self.child("Foster Care")))

    def test_the_adoption_type_cannot_make_a_block_c_box_apply_to_another_case_type(self):
        # A Foster Care record can still carry an old adoption type.
        child = self.child("Foster Care", adoption="Regular")
        self.assertNotIn("c3_stc_report", self.keys(child))
        self.assertNotIn("c1_placement", self.keys(child))
