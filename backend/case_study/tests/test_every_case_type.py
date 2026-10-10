"""Block A for every case type (owner's decision, 10 Oct 2026).

The child's sources and circumstances, background, birth family and the
termination or abandonment facts are the child's profile whatever the case type
is: a Foster Care, Kinship Care, Residential Care, Family Tracing &
Reunification or Independent Living record has a case study too. Blocks B (the
prospective adoptive parents) and C (the placement) stay an adoption's.

Changing the case type hides, and never deletes (the system's rule), so a record
that moves away from Adoption keeps its B and C text and gets it back if it
moves back. The clock is pinned by the base class.
"""
from datetime import date

from case_study.access import FINAL_SENTENCE
from case_study.completeness import DATE_PREPARED, missing_sections
from case_study.models import CaseStudy, CaseStudyFinal, CaseStudySection
from case_study.sections import BLOCK_A_KEYS, SCSR_SECTIONS, applies
from case_study.tests import test_final
from case_study.tests.test_completeness import good_value
from children.models import Child

NOT_APPLY = "That section does not apply to this child."


class NonAdoptionCase(test_final.FinalTestCase):
    """The adoption child of the base class (complete, as FinalTestCase leaves
    it) and a Foster Care child of the same social worker with a case study
    started and nothing in it."""

    def setUp(self):
        super().setUp()
        self.foster = Child.objects.create(
            first_name="Ben", last_name="Reyes", gender="Male",
            birth_date=date(2018, 4, 4), case_type="Foster Care", case_category="Dependent",
            social_worker=self.sw, assigned_psychologist=self.psy)
        self.foster_study = CaseStudy.objects.create(child=self.foster, created_by=self.sw)

    def block_a_titles(self, child):
        study = CaseStudy.objects.get(child=child)
        return [e["title"] for e in SCSR_SECTIONS
                if e["block"] == "A" and applies(e, child, study)]

    def fill_block_a(self, child, user=None):
        study = CaseStudy.objects.get(child=child)
        for entry in SCSR_SECTIONS:
            if entry["block"] == "A" and applies(entry, child, study):
                CaseStudySection.objects.update_or_create(
                    case_study=study, key=entry["key"],
                    defaults={"value": good_value(entry), "updated_by": user or self.sw})
        CaseStudy.objects.filter(pk=study.pk).update(date_prepared=date(2026, 10, 1))

    def get(self, child, user=None):
        return self.as_user(user or self.sw).get(self.url(child))


class WhatAFosterCareRecordGetsTest(NonAdoptionCase):
    def test_every_box_of_block_b_and_c_is_listed_as_not_applying(self):
        body = self.get(self.foster).data
        self.assertEqual([e["key"] for e in SCSR_SECTIONS], [s["key"] for s in body["sections"]])
        blocks = {e["key"]: e["block"] for e in SCSR_SECTIONS}
        for section in body["sections"]:
            if blocks[section["key"]] in ("B", "C"):
                self.assertFalse(section["applies"], section["key"])
        a_flags = {s["key"]: s["applies"] for s in body["sections"] if blocks[s["key"]] == "A"}
        self.assertEqual(
            self.block_a_titles(self.foster),
            [e["title"] for e in SCSR_SECTIONS if a_flags.get(e["key"])])
        self.assertTrue(a_flags["a2_sources"] and a_flags["a5_summary"])
        # A Dependent child: no Deed, no facts of abandonment.
        self.assertFalse(a_flags["a5_dvc_signed"] or a_flags["a5_abandonment"])

    def test_block_a_keeps_its_own_rules_by_category(self):
        Child.objects.filter(pk=self.foster.pk).update(case_category="Surrendered")
        flags = {s["key"]: s["applies"] for s in self.get(self.foster).data["sections"]}
        self.assertTrue(flags["a5_dvc_signed"] and flags["a5_counselling"]
                        and flags["a5_aware_irrevocable"])
        self.assertFalse(flags["a5_abandonment"])
        Child.objects.filter(pk=self.foster.pk).update(case_category="Without Known Parents")
        flags = {s["key"]: s["applies"] for s in self.get(self.foster).data["sections"]}
        self.assertTrue(flags["a5_abandonment"] and flags["a5_search_efforts"])
        self.assertFalse(flags["a5_dvc_signed"])

    def test_a_box_of_block_b_or_c_is_refused_and_saves_nothing(self):
        for key in ("b1_paps", "b4_motivation", "c1_placement", "c2_on_placement",
                    "c3_stc_report", "c6_recommendation"):
            res = self.save_section(key, "Written text.", child=self.foster)
            self.assertEqual(400, res.status_code, key)
            self.assertEqual(NOT_APPLY, res.data["detail"], key)
        self.assertFalse(CaseStudySection.objects.filter(case_study=self.foster_study).exists())

    def test_block_a_saves(self):
        res = self.save_section("a2_sources", ["The child", "The foster parent"], child=self.foster)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(1, res.data["version"])
        self.assertNotIn("Sources of information", res.data["missing"])
        res = self.save_section("a5_summary", "The child entered care in 2025.", child=self.foster)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(2, CaseStudySection.objects.filter(case_study=self.foster_study).count())

    def test_the_header_saves_and_the_custody_question_is_not_asked(self):
        client = self.as_user(self.sw)
        res = client.patch(self.url(self.foster), {"date_prepared": "2026-09-30"}, format="json")
        self.assertEqual(200, res.status_code)
        res = client.patch(self.url(self.foster), {"custody_over_two_years": True}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertIsNone(self.get(self.foster).data["custody_pre_answer"])

    def test_what_is_missing_is_block_a_and_the_date_prepared_only(self):
        missing = self.get(self.foster).data["missing"]
        self.assertEqual([DATE_PREPARED] + self.block_a_titles(self.foster), missing)
        b_and_c = {e["title"] for e in SCSR_SECTIONS if e["block"] in ("B", "C")}
        self.assertFalse(b_and_c & set(missing))
        # And the adoption child of the same social worker is asked for all three.
        self.assertEqual([], self.get(self.child).data["missing"])
        CaseStudySection.objects.filter(case_study=self.study, key="b1_paps").delete()
        self.assertEqual(["Prospective adoptive parents: identifying information"],
                         self.get(self.child).data["missing"])

    def test_a_block_a_box_leaves_the_list_when_it_is_filled(self):
        self.save_section("a3_description", "A quiet child.", child=self.foster)
        self.assertNotIn("Description of the child upon admission or entrustment",
                         self.get(self.foster).data["missing"])
        # The date prepared and every other box of block A are still to do.
        self.assertEqual(len(self.block_a_titles(self.foster)),
                         len(self.get(self.foster).data["missing"]))

    def test_every_case_type_is_asked_block_a_and_nothing_more(self):
        for number, (case_type, _) in enumerate(Child.CASE_TYPE_CHOICES):
            if case_type == "Adoption":
                continue
            child = Child.objects.create(
                first_name=f"Kid{number}", last_name="Lim", gender="Female",
                birth_date=date(2017, 1, 1), case_type=case_type, case_category="Neglected",
                social_worker=self.sw)
            CaseStudy.objects.create(child=child, created_by=self.sw)
            missing = self.get(child).data["missing"]
            self.assertEqual([DATE_PREPARED] + self.block_a_titles(child), missing, case_type)
            applying = [s["key"] for s in self.get(child).data["sections"] if s["applies"]]
            self.assertTrue(applying, case_type)
            self.assertTrue(all(key in BLOCK_A_KEYS for key in applying), case_type)

    def test_the_psychologist_reads_block_a_of_it_as_before(self):
        self.fill_block_a(self.foster)
        res = self.get(self.foster, self.psy)
        self.assertEqual(200, res.status_code)
        self.assertTrue(res.data["read_only"])
        self.assertNotIn("missing", res.data)
        self.assertEqual(list(BLOCK_A_KEYS), [s["key"] for s in res.data["sections"]])
        self.assertEqual("Foster Care", res.data["record_facts"]["case_type"])

    def test_the_isa_sees_a_status_that_counts_block_a_only(self):
        res = self.get(self.foster, self.isa)
        self.assertEqual(200, res.status_code)
        self.assertNotIn("sections", res.data)
        self.assertEqual(1 + len(self.block_a_titles(self.foster)), res.data["missing_count"])
        self.fill_block_a(self.foster)
        self.assertEqual(0, self.get(self.foster, self.isa).data["missing_count"])

    def test_other_people_still_get_a_404(self):
        self.assertEqual(404, self.get(self.foster, self.sw2).status_code)
        self.assertEqual(404, self.get(self.foster, self.psy2).status_code)

    def test_part_one_names_the_case_type(self):
        facts = self.get(self.foster).data["record_facts"]
        self.assertEqual("Foster Care", facts["case_type"])
        self.assertEqual("Adoption", self.get(self.child).data["record_facts"]["case_type"])


class FinalAndReopenForAFosterCareRecordTest(NonAdoptionCase):
    def test_it_is_refused_with_block_a_still_to_complete(self):
        res = self.make_final(child=self.foster)
        self.assertEqual(400, res.status_code)
        self.assertEqual("Complete these before making it final.", res.data["detail"])
        self.assertEqual([DATE_PREPARED] + self.block_a_titles(self.foster), res.data["missing"])
        self.assertEqual(0, CaseStudyFinal.objects.filter(case_study=self.foster_study).count())

    def test_it_becomes_final_with_block_a_complete_and_no_adoptive_parent_named(self):
        self.fill_block_a(self.foster)
        self.assertEqual([], missing_sections(CaseStudy.objects.get(child=self.foster)))
        self.assertFalse(CaseStudySection.objects.filter(
            case_study=self.foster_study, key__startswith="b").exists())
        res = self.make_final(child=self.foster)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("final", res.data["status"])
        self.assertEqual(1, len(res.data["finals"]))

    def test_its_copy_holds_block_a_only(self):
        self.fill_block_a(self.foster)
        self.make_final(child=self.foster)
        final = CaseStudyFinal.objects.get(case_study=self.foster_study)
        snap = final.snapshot
        wanted = [e["key"] for e in SCSR_SECTIONS
                  if e["block"] == "A" and applies(e, self.foster, self.foster_study)]
        self.assertEqual(wanted, list(snap["sections"]))
        self.assertEqual("Foster Care", snap["child"]["case_type"])
        self.assertEqual("Foster Care", snap["part_one"]["case_type"])
        self.assertEqual("2026-10-01", snap["date_prepared"])
        # The social worker prints it through the same endpoint as an adoption's.
        copy = self.as_user(self.sw).get(self.finals_url(final.pk, self.foster))
        self.assertEqual(200, copy.status_code)
        self.assertEqual(wanted, list(copy.data["snapshot"]["sections"]))

    def test_a_ticked_not_applicable_box_is_copied_without_its_text(self):
        self.fill_block_a(self.foster)
        CaseStudySection.objects.filter(
            case_study=self.foster_study, key="a4_family_description"
        ).update(not_applicable=True, value="Kept text.")
        self.make_final(child=self.foster)
        box = CaseStudyFinal.objects.get(case_study=self.foster_study).snapshot[
            "sections"]["a4_family_description"]
        self.assertEqual({"value": None, "not_applicable": True}, box)

    def test_a_final_one_is_read_only_until_it_is_reopened(self):
        self.fill_block_a(self.foster)
        self.make_final(child=self.foster)
        body = self.get(self.foster).data
        self.assertTrue(body["read_only"])
        self.assertEqual(FINAL_SENTENCE, body["read_only_reason"])
        res = self.save_section("a2_sources", ["x"], version=1, child=self.foster)
        self.assertEqual(400, res.status_code)
        self.assertEqual(FINAL_SENTENCE, res.data["detail"])

    def test_it_can_be_reopened_and_made_final_again(self):
        self.fill_block_a(self.foster)
        self.make_final(child=self.foster)
        res = self.reopen(child=self.foster)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("draft", res.data["status"])
        self.assertFalse(res.data["read_only"])
        self.assertEqual(200, self.save_section(
            "a2_sources", ["The child", "A second source"], version=1, child=self.foster).status_code)
        again = self.make_final(child=self.foster)
        self.assertEqual(200, again.status_code, again.data)
        self.assertEqual(2, len(again.data["finals"]))

    def test_a_draft_cannot_be_reopened(self):
        self.assertEqual(409, self.reopen(child=self.foster).status_code)

    def test_a_closed_case_still_cannot_be_reopened(self):
        self.fill_block_a(self.foster)
        self.make_final(child=self.foster)
        Child.objects.filter(pk=self.foster.pk).update(
            status=Child.INACTIVE, case_status=Child.STAGE_TERMINATED)
        res = self.reopen(child=self.foster)
        self.assertEqual(400, res.status_code)
        self.assertIn("closed", res.data["detail"])
        self.assertEqual("final", CaseStudy.objects.get(child=self.foster).status)

    def test_a_closed_case_still_cannot_be_written_or_started(self):
        Child.objects.filter(pk=self.foster.pk).update(
            status=Child.INACTIVE, case_status=Child.STAGE_TERMINATED)
        res = self.save_section("a2_sources", ["x"], child=self.foster)
        self.assertEqual(400, res.status_code)
        self.assertIn("closed", res.data["detail"])
        CaseStudy.objects.filter(pk=self.foster_study.pk).delete()
        self.assertEqual(400, self.as_user(self.sw).post(
            self.url(self.foster), {}, format="json").status_code)

    def test_only_the_social_worker_who_holds_the_record_may_finalize_or_reopen(self):
        self.fill_block_a(self.foster)
        self.assertEqual(403, self.make_final(user=self.psy, child=self.foster).status_code)
        self.assertEqual(403, self.make_final(user=self.isa, child=self.foster).status_code)
        self.assertEqual(404, self.make_final(user=self.sw2, child=self.foster).status_code)
        self.make_final(child=self.foster)
        self.assertEqual(403, self.reopen(user=self.psy, child=self.foster).status_code)
        self.assertEqual(404, self.reopen(user=self.sw2, child=self.foster).status_code)


class AMovedRecordTest(NonAdoptionCase):
    """The base class's adoption child, complete in all three blocks."""

    def move(self, case_type):
        Child.objects.filter(pk=self.child.pk).update(case_type=case_type)

    def rows(self, prefix):
        return {r.key: (r.value, r.version) for r in CaseStudySection.objects.filter(
            case_study=self.study, key__startswith=prefix)}

    def test_block_b_and_c_text_is_kept_and_comes_back(self):
        before_b, before_c = self.rows("b"), self.rows("c")
        self.assertTrue(before_b and before_c)
        self.move("Foster Care")
        # Hidden: not applying, not asked, not missing - and still stored.
        away = self.get(self.child).data
        flags = {s["key"]: s["applies"] for s in away["sections"]}
        self.assertFalse(any(flags[k] for k in before_b) or any(flags[k] for k in before_c))
        self.assertEqual([], away["missing"])
        self.assertEqual(before_b, self.rows("b"))
        self.assertEqual(before_c, self.rows("c"))
        # A block A box saves meanwhile; a block B box is refused.
        self.assertEqual(200, self.save_section(
            "a2_sources", ["Changed"], version=1, child=self.child).status_code)
        self.assertEqual(400, self.save_section(
            "b4_motivation", "Edited while away.", version=1, child=self.child).status_code)
        self.move("Adoption")
        back = self.get(self.child).data
        shown = {s["key"]: s for s in back["sections"]}
        for key, (value, version) in {**before_b, **before_c}.items():
            self.assertTrue(shown[key]["applies"], key)
            self.assertEqual((value, version), (shown[key]["value"], shown[key]["version"]), key)
        self.assertEqual([], back["missing"])
        self.assertEqual(before_b, self.rows("b"))

    def test_a_record_made_final_as_an_adoption_and_then_moved_can_be_reopened(self):
        self.assertEqual(200, self.make_final().status_code)
        first = CaseStudyFinal.objects.get(case_study=self.study)
        self.assertIn("b1_paps", first.snapshot["sections"])
        self.move("Foster Care")
        body = self.get(self.child).data
        self.assertTrue(body["read_only"])
        self.assertEqual(FINAL_SENTENCE, body["read_only_reason"])
        self.assertEqual("final", body["status"])
        res = self.reopen()
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("draft", res.data["status"])
        self.assertFalse(res.data["read_only"])
        self.assertEqual([], res.data["missing"])
        self.assertTrue(res.data["can_finalize"])
        # And it can be made final again, now as block A alone.
        again = self.make_final()
        self.assertEqual(200, again.status_code, again.data)
        self.assertEqual(2, len(again.data["finals"]))
        newest = CaseStudyFinal.objects.filter(case_study=self.study).latest("id")
        self.assertTrue(all(k.startswith("a") for k in newest.snapshot["sections"]))
        self.assertEqual("Foster Care", newest.snapshot["child"]["case_type"])
        # The first copy is what was signed: still the adoption report, whole.
        first.refresh_from_db()
        self.assertIn("b1_paps", first.snapshot["sections"])
        self.assertIn("c6_recommendation", first.snapshot["sections"])
        self.assertEqual("Adoption", first.snapshot["child"]["case_type"])
        # Its B and C text is still stored on the case study.
        self.assertTrue(self.rows("b") and self.rows("c"))

    def test_the_old_copy_is_still_printable_after_the_move(self):
        self.make_final()
        final = CaseStudyFinal.objects.get(case_study=self.study)
        self.move("Foster Care")
        res = self.as_user(self.sw).get(self.finals_url(final.pk))
        self.assertEqual(200, res.status_code)
        self.assertIn("b1_paps", res.data["snapshot"]["sections"])

    def test_completeness_and_final_follow_the_current_case_type(self):
        CaseStudySection.objects.filter(case_study=self.study, key="b4_motivation").delete()
        self.assertEqual(["Motivation to adopt"], self.get(self.child).data["missing"])
        self.assertEqual(400, self.make_final().status_code)
        self.move("Kinship Care")
        self.assertEqual([], self.get(self.child).data["missing"])
        self.assertEqual(200, self.make_final().status_code)
        self.assertNotIn("b4_motivation", self.snapshot()["sections"])
