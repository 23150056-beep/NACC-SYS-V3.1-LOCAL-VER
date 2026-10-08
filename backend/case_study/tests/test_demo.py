"""Demo case studies: the seeder's drafts and finals, and their way to a hosted branch.

Seeded data must satisfy the rules the endpoint enforces (CLAUDE.md, Demo
data). A rule and a seeder maintained separately drift, and a test that only
asserts rows exist passes while the screen refuses them, so these run every
seeded value back through `clean_value` and read the result through the real
API.
"""
import json
import re
import shutil
import tempfile
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role
from case_study import demo_case_studies
from case_study.completeness import missing_sections
from case_study.finalize import CannotFinalize, finalize
from case_study.models import CaseStudy, CaseStudyFinal, CaseStudySection
from case_study.sections import (
    DVC_NOTARIZED, DVC_SIGNED, PAP_CONTACT_ROWS, PAP_ROWS, SCSR_SECTIONS, applies, entry_for)
from case_study.tests.base import NOW, TODAY, CaseStudyTestCase, make_user
from case_study.validation import clean_value
from children.management.commands.export_demo_data import DEMO_MODELS
from children.management.commands.import_demo_data import (
    rehome_people, strip_pap_contacts)
from children.models import Child
from locations.models import Barangay, Municipality, Province

CATEGORIES = ["Surrendered", "Abandoned", "Dependent", "Neglected",
              "Without Known Parents", "Orphaned"]
# A phone number, an e-mail address, a web address.
CONTACT = re.compile(r"\d{7,}|@|https?://", re.I)


def text_of(value):
    return json.dumps(value)


class SeededDraftsAreWhatTheEndpointWouldTake(CaseStudyTestCase):
    def children(self, n=7):
        made = [Child.objects.create(
            first_name=f"Demo{i}", last_name="Child", gender="Male",
            birth_date=TODAY - timedelta(days=(6 + i) * 366),
            case_type="Adoption", case_category=CATEGORIES[i % 6],
            type_of_adoption=["Regular", "Domestic Relative", "Step-parent"][i % 3],
            date_of_admission=TODAY - timedelta(days=60 + i),
            social_worker=[self.sw, self.sw2][i % 2], assigned_psychologist=self.psy)
            for i in range(n)]
        return made

    def test_every_third_adoption_child_gets_a_draft_and_a_few_others_a_final(self):
        kids = self.children(7)
        Child.objects.create(first_name="Fos", last_name="Ter", case_type="Foster Care",
                             social_worker=self.sw, birth_date=date(2015, 1, 1))
        made = demo_case_studies.install_case_studies(
            list(Child.objects.order_by("pk")), today=TODAY)
        # The setUpTestData child is the first adoption child, then the seven.
        adoption = [self.child] + kids
        drafts = {c.pk for c in adoption[::3]}
        finals = {c.pk for c in adoption[1::3][:demo_case_studies.FINALS]}
        self.assertEqual(len(drafts) + len(finals), made)
        self.assertEqual(drafts, set(CaseStudy.objects.filter(status=CaseStudy.DRAFT)
                                     .values_list("child_id", flat=True)))
        self.assertEqual(finals, set(CaseStudy.objects.filter(status=CaseStudy.FINAL)
                                     .values_list("child_id", flat=True)))
        self.assertFalse(drafts & finals)
        self.assertEqual(3, len(finals))

    def test_at_most_three_are_final(self):
        self.children(20)
        demo_case_studies.install_case_studies(list(Child.objects.order_by("pk")), today=TODAY)
        self.assertEqual(demo_case_studies.FINALS,
                         CaseStudy.objects.filter(status=CaseStudy.FINAL).count())
        self.assertGreater(CaseStudy.objects.filter(status=CaseStudy.DRAFT).count(), 1)

    def test_running_it_again_adds_nothing(self):
        self.children(7)
        everyone = list(Child.objects.order_by("pk"))
        demo_case_studies.install_case_studies(everyone, today=TODAY)
        sections = CaseStudySection.objects.count()
        self.assertEqual(0, demo_case_studies.install_case_studies(everyone, today=TODAY))
        self.assertEqual(sections, CaseStudySection.objects.count())
        self.assertEqual(3, CaseStudyFinal.objects.count())

    def test_a_child_with_no_social_worker_gets_none(self):
        Child.objects.filter(pk=self.child.pk).update(social_worker=None)
        self.assertEqual(0, demo_case_studies.install_case_studies(
            [Child.objects.get(pk=self.child.pk)], today=TODAY))

    def test_a_draft_is_its_social_workers_own(self):
        self.children(7)
        demo_case_studies.install_case_studies(list(Child.objects.order_by("pk")), today=TODAY)
        for study in CaseStudy.objects.select_related("child"):
            self.assertEqual(study.child.social_worker, study.created_by)
            for section in study.sections.all():
                self.assertEqual(study.child.social_worker, section.updated_by)
                self.assertEqual(1, section.version)
        for final in CaseStudyFinal.objects.select_related("case_study__child"):
            self.assertEqual(final.case_study.child.social_worker, final.finalized_by)

    def test_every_seeded_section_passes_the_real_rules_for_every_kind_of_child(self):
        for turn in range(3):
            for category in CATEGORIES:
                for adoption in ("Regular", "Domestic Relative", "Adult"):
                    child = Child.objects.create(
                        first_name="Demo", last_name=f"{category[:3]}{turn}{adoption[:2]}",
                        birth_date=date(2016, 5, 5), case_type="Adoption",
                        case_category=category, type_of_adoption=adoption,
                        date_of_admission=TODAY - timedelta(days=40),
                        social_worker=self.sw2)
                    draft = demo_case_studies.draft_for(child, turn, TODAY)
                    self.assertTrue(draft)
                    for key, (value, not_applicable) in draft.items():
                        entry = entry_for(key)
                        label = f"{key} / {category} / {adoption} / turn {turn}"
                        self.assertTrue(applies(entry, child, None), label)
                        self.assertEqual("A", entry["block"], label)
                        if not_applicable:
                            self.assertTrue(entry["may_be_na"], label)
                            self.assertIsNone(value, label)
                        else:
                            self.assertEqual(value, clean_value(entry, value, today=TODAY), label)
                            self.assertFalse(CONTACT.search(text_of(value)), label)
                    if DVC_SIGNED in draft and DVC_NOTARIZED in draft:
                        self.assertLessEqual(draft[DVC_SIGNED][0], draft[DVC_NOTARIZED][0])

    def test_the_drafts_stored_pass_the_rules_again_and_are_only_partly_filled(self):
        self.children(13)
        demo_case_studies.install_case_studies(list(Child.objects.order_by("pk")), today=TODAY)
        studies = list(CaseStudy.objects.filter(status=CaseStudy.DRAFT))
        self.assertGreaterEqual(len(studies), 4)
        for study in studies:
            for section in study.sections.all():
                entry = entry_for(section.key)
                self.assertIsNotNone(entry, section.key)
                self.assertTrue(applies(entry, study.child, study), section.key)
                if not section.not_applicable:
                    self.assertEqual(
                        section.value, clean_value(entry, section.value, today=TODAY), section.key)
            # Partly filled: some of block A, none of block B or C, never done.
            keys = {s.key for s in study.sections.all()}
            self.assertTrue(keys)
            self.assertTrue(all(k.startswith("a") for k in keys), keys)
            self.assertTrue(missing_sections(study))

    def test_the_drafts_are_not_all_filled_to_the_same_depth(self):
        self.children(13)
        demo_case_studies.install_case_studies(list(Child.objects.order_by("pk")), today=TODAY)
        depths = {s.sections.count() for s in CaseStudy.objects.filter(status=CaseStudy.DRAFT)}
        self.assertGreater(len(depths), 1)

    def test_no_seeded_text_names_a_person_or_gives_a_number(self):
        self.children(13)
        demo_case_studies.install_case_studies(list(Child.objects.order_by("pk")), today=TODAY)
        for section in CaseStudySection.objects.all():
            self.assertFalse(CONTACT.search(text_of(section.value)), section.key)

    def test_dates_are_never_in_the_future_even_for_a_case_that_began_today(self):
        child = Child.objects.create(
            first_name="New", last_name="Arrival", birth_date=date(2016, 5, 5),
            case_type="Adoption", case_category="Surrendered", type_of_adoption="Regular",
            date_of_admission=TODAY, social_worker=self.sw)
        boxes = demo_case_studies._future_dates_clamped(
            demo_case_studies._boxes(child, TODAY, 0), TODAY)
        for key in (DVC_SIGNED, DVC_NOTARIZED):
            self.assertLessEqual(boxes[key], TODAY.isoformat())
        for row in boxes["a5_counselling"]:
            self.assertLessEqual(row["date"], TODAY.isoformat())
        draft = demo_case_studies.draft_for(child, 2, TODAY)
        self.assertLessEqual(draft[DVC_SIGNED][0], draft[DVC_NOTARIZED][0])

    def test_a_child_without_known_parents_has_no_family_to_describe(self):
        child = Child.objects.create(
            first_name="Found", last_name="Child", birth_date=date(2016, 5, 5),
            case_type="Adoption", case_category="Without Known Parents",
            type_of_adoption="Regular", date_of_admission=TODAY - timedelta(days=90),
            social_worker=self.sw)
        draft = demo_case_studies.draft_for(child, 2, TODAY)
        self.assertEqual((None, True), draft["a4_family_composition"])
        self.assertEqual((None, True), draft["a4_family_description"])
        self.assertIn("a5_abandonment", draft)
        self.assertNotIn(DVC_SIGNED, draft)

    def test_the_social_worker_the_psychologist_and_the_isa_each_read_it_their_own_way(self):
        kids = self.children(7)
        demo_case_studies.install_case_studies(list(Child.objects.order_by("pk")), today=TODAY)
        mine = next(c for c in [self.child] + kids if CaseStudy.objects.filter(child=c).exists())
        sw_view = self.as_user(mine.social_worker).get(self.url(mine))
        self.assertEqual(200, sw_view.status_code)
        self.assertFalse(sw_view.data["read_only"])
        filled = [s for s in sw_view.data["sections"] if s["version"] == 1]
        self.assertTrue(filled)
        psychologist = self.as_user(self.psy).get(self.url(mine))
        self.assertEqual(200, psychologist.status_code)
        self.assertTrue(psychologist.data["read_only"])
        isa = self.as_user(self.isa).get(self.url(mine))
        self.assertNotIn("sections", isa.data)
        self.assertGreater(isa.data["missing_count"], 0)


class SeededFinalsAreWhatTheEndpointWouldMakeTest(CaseStudyTestCase):
    """A seeded final went through `finalize()`, so it can only exist if it
    passed `missing_sections()` - and these hold that to every kind of child."""

    def adoption(self, category, adoption, name="Demo", **extra):
        return Child.objects.create(
            first_name=name, last_name=f"{category[:3]}{adoption[:3]}",
            birth_date=date(2015, 5, 5), gender="Female", case_type="Adoption",
            case_category=category, type_of_adoption=adoption,
            date_of_admission=TODAY - timedelta(days=90),
            social_worker=self.sw2, assigned_psychologist=self.psy, **extra)

    def test_every_kind_of_child_can_be_given_a_final_that_passes_the_real_rules(self):
        for category in CATEGORIES:
            for adoption in ("Regular", "Domestic Relative", "Step-parent", "Adult", "IP"):
                for turn in range(3):
                    child = self.adoption(category, adoption, name=f"T{turn}")
                    label = f"{category} / {adoption} / turn {turn}"
                    demo_case_studies._write_final(child, turn, TODAY)
                    study = CaseStudy.objects.get(child=child)
                    self.assertEqual(CaseStudy.FINAL, study.status, label)
                    self.assertEqual([], missing_sections(study), label)
                    self.assertIsNotNone(study.date_prepared, label)
                    final = CaseStudyFinal.objects.get(case_study=study)
                    wanted = [e["key"] for e in SCSR_SECTIONS if applies(e, child, study)]
                    self.assertEqual(wanted, list(final.snapshot["sections"]), label)
                    for key, box in final.snapshot["sections"].items():
                        entry = entry_for(key)
                        if box["not_applicable"]:
                            self.assertTrue(entry["may_be_na"], label)
                        else:
                            self.assertEqual(
                                box["value"], clean_value(entry, box["value"], today=TODAY),
                                f"{label} {key}")

    def test_a_seeded_final_is_complete_in_every_block(self):
        child = self.adoption("Surrendered", "Regular")
        demo_case_studies._write_final(child, 0, TODAY)
        study = CaseStudy.objects.get(child=child)
        keys = set(study.sections.values_list("key", flat=True))
        self.assertTrue({"a2_sources", "b1_paps", "b17_adoption_telling", "c1_placement",
                         "c6_recommendation", "c3_stc_report"} <= keys)
        self.assertEqual([], missing_sections(study))

    def test_a_domestic_relative_with_long_custody_has_no_placement_history(self):
        child = self.adoption("Surrendered", "Domestic Relative",
                              date_of_placement_to_custodian=TODAY - timedelta(days=4 * 365))
        demo_case_studies._write_final(child, 0, TODAY)
        study = CaseStudy.objects.get(child=child)
        self.assertTrue(study.custody_over_two_years)
        self.assertNotIn("c1_placement", CaseStudyFinal.objects.get().snapshot["sections"])
        self.assertEqual([], missing_sections(study))

    def test_a_seeded_final_cannot_exist_if_it_is_incomplete(self):
        child = self.adoption("Surrendered", "Regular")
        boxes, custody = demo_case_studies.complete_for(child, 0, TODAY, TODAY)
        del boxes["b4_motivation"]
        study = CaseStudy.objects.create(
            child=child, created_by=self.sw2, date_prepared=TODAY, custody_over_two_years=custody)
        for key, (value, na) in boxes.items():
            CaseStudySection.objects.create(
                case_study=study, key=key, value=value, not_applicable=na, updated_by=self.sw2)
        with self.assertRaises(CannotFinalize) as caught:
            finalize(study, self.sw2)
        self.assertEqual(["Motivation to adopt"], caught.exception.missing)
        self.assertIn("Motivation to adopt", str(caught.exception))
        self.assertEqual(0, CaseStudyFinal.objects.count())
        self.assertEqual(CaseStudy.DRAFT, CaseStudy.objects.get(pk=study.pk).status)

    def test_the_adoptive_parents_have_names_dates_and_no_contact_rows(self):
        for turn in range(3):
            child = self.adoption("Surrendered", "Regular", name=f"P{turn}")
            demo_case_studies._write_final(child, turn, TODAY)
            table = CaseStudyFinal.objects.get(case_study__child=child).snapshot[
                "sections"]["b1_paps"]["value"]
            self.assertTrue(table["female"]["full_name"])
            self.assertTrue(table["female"]["date_of_birth"])
            self.assertTrue(table["female"]["religion"])
            # One in three has a single adopter, which the table allows.
            self.assertEqual(turn == 2, table["male"] == {})
            for side in table.values():
                self.assertTrue(set(PAP_CONTACT_ROWS).isdisjoint(side), side)
            self.assertFalse(CONTACT.search(text_of(table)))

    def test_nothing_in_a_seeded_final_gives_a_number_or_an_address_to_write_to(self):
        for turn, category in enumerate(CATEGORIES):
            child = self.adoption(category, "Regular", name=f"C{turn}")
            demo_case_studies._write_final(child, turn, TODAY)
        for final in CaseStudyFinal.objects.all():
            self.assertFalse(CONTACT.search(text_of(final.snapshot["sections"])))
            self.assertFalse(CONTACT.search(text_of(final.snapshot["preparer"])))
            self.assertEqual(
                {"name": "Rosa Santos", "license_number": "", "license_valid_until": None},
                final.snapshot["preparer"])

    def test_the_dates_are_in_order_and_never_in_the_future(self):
        child = self.adoption("Surrendered", "Regular")
        demo_case_studies._write_final(child, 0, TODAY)
        snap = CaseStudyFinal.objects.get().snapshot
        self.assertLess(snap["date_prepared"], TODAY.isoformat())
        place = snap["sections"]["c1_placement"]["value"]
        self.assertLessEqual(place["matching_date"], place["accepted_date"])
        self.assertLessEqual(place["accepted_date"], place["entrustment_date"])
        self.assertLessEqual(place["entrustment_date"], TODAY.isoformat())
        self.assertLessEqual(snap["sections"]["c4_measurements"]["value"]["measured_on"],
                             TODAY.isoformat())

    def test_the_social_worker_and_the_psychologist_read_a_seeded_final(self):
        child = self.adoption("Surrendered", "Regular")
        demo_case_studies._write_final(child, 0, TODAY)
        url = f"/api/case-studies/child/{child.pk}/"
        mine = self.as_user(self.sw2).get(url).data
        self.assertEqual("final", mine["status"])
        self.assertTrue(mine["read_only"])
        self.assertFalse(mine["can_finalize"])
        self.assertEqual(1, len(mine["finals"]))
        self.assertEqual("Rosa Santos", mine["finals"][0]["finalized_by_name"])
        copy = self.as_user(self.sw2).get(f"{url}finals/{mine['finals'][0]['id']}/")
        self.assertEqual(200, copy.status_code)
        theirs = self.as_user(self.psy).get(url).data
        self.assertEqual("final", theirs["status"])
        self.assertIsNotNone(theirs["last_finalized_at"])
        self.assertEqual(0, self.as_user(self.isa).get(url).data["missing_count"])
        # And the social worker can reopen it and finalize it again.
        self.assertEqual(200, self.as_user(self.sw2).post(f"{url}reopen/", {}, format="json").status_code)
        again = self.as_user(self.sw2).post(
            f"{url}final/", {"expected_updated_at": self.as_user(self.sw2).get(url).data["updated_at"]},
            format="json")
        self.assertEqual(200, again.status_code, again.data)
        self.assertEqual(2, len(again.data["finals"]))


@override_settings(DEBUG=True)
class SeederTest(TestCase):
    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def seed(self, children):
        province = Province.objects.create(psgc_code="012800000", name="Ilocos Norte")
        town = Municipality.objects.create(psgc_code="012812000", name="Laoag City",
                                           province=province)
        Barangay.objects.create(psgc_code="012812001", name="Barangay 1", municipality=town)
        out = StringIO()
        call_command("seed_demo_data", children=children, stdout=out)
        return out.getvalue()

    def test_the_seeder_gives_adoption_children_a_draft_and_a_few_a_final(self):
        out = self.seed(60)
        adoption = list(Child.objects.filter(case_type="Adoption").order_by("pk"))
        self.assertGreaterEqual(len(adoption), 7, "the seed should draw some adoptions")
        drafts = CaseStudy.objects.filter(status=CaseStudy.DRAFT).count()
        finals = CaseStudy.objects.filter(status=CaseStudy.FINAL).count()
        self.assertEqual(len(adoption[::3]), drafts)
        self.assertGreaterEqual(finals, 2)
        self.assertLessEqual(finals, demo_case_studies.FINALS)
        self.assertEqual(finals, CaseStudyFinal.objects.count())
        self.assertIn(f"case studies written: {drafts + finals}, {finals} of them final", out)
        for section in CaseStudySection.objects.select_related("case_study__child"):
            entry = entry_for(section.key)
            self.assertTrue(applies(entry, section.case_study.child, section.case_study))
            if not section.not_applicable:
                self.assertEqual(section.value, clean_value(entry, section.value, today=TODAY))

    def test_what_the_default_caseload_shows(self):
        # The size a demo is usually built at: a draft to write in AND a final
        # to print, reopen and finalize again.
        self.seed(40)
        self.assertGreaterEqual(CaseStudy.objects.filter(status=CaseStudy.DRAFT).count(), 1)
        self.assertGreaterEqual(CaseStudy.objects.filter(status=CaseStudy.FINAL).count(), 1)


class ToTheHostedDemoTest(TestCase):
    """export_demo_data / import_demo_data carry the case studies, with the
    people re-homed and no contact details in the adoptive parents' table."""

    def test_the_export_includes_the_three_models(self):
        for model in ("case_study.CaseStudy", "case_study.CaseStudySection",
                      "case_study.CaseStudyFinal"):
            self.assertIn(model, DEMO_MODELS)

    def test_phone_numbers_and_emails_are_stripped_from_the_adoptive_parents(self):
        contact = ("mobile_phone", "home_phone", "work_phone", "email", "employer_address")
        column = {row["id"]: "x" for row in PAP_ROWS}
        rows = [
            {"model": "case_study.casestudysection", "pk": 1,
             "fields": {"key": "b1_paps", "value": {"female": dict(column), "male": dict(column)}}},
            {"model": "case_study.casestudysection", "pk": 2,
             "fields": {"key": "b4_motivation", "value": "Text with 09171234567 in it."}},
            {"model": "case_study.casestudyfinal", "pk": 3,
             "fields": {"snapshot": {"sections": {"b1_paps": {"female": dict(column),
                                                              "male": {}}}}}},
        ]
        self.assertTrue(strip_pap_contacts(rows))
        for side in ("female", "male"):
            kept = rows[0]["fields"]["value"][side]
            self.assertTrue(set(contact).isdisjoint(kept), kept)
            self.assertEqual(len(PAP_ROWS) - len(contact), len(kept))
            self.assertEqual("x", kept["full_name"])
        final = rows[2]["fields"]["snapshot"]["sections"]["b1_paps"]["female"]
        self.assertTrue(set(contact).isdisjoint(final))
        # Only the adoptive parents' table is touched.
        self.assertIn("09171234567", rows[1]["fields"]["value"])
        self.assertFalse(strip_pap_contacts(rows))

    def test_an_export_without_any_is_left_alone(self):
        self.assertFalse(strip_pap_contacts([{"model": "children.child", "pk": 1, "fields": {}}]))
        self.assertFalse(strip_pap_contacts([
            {"model": "case_study.casestudysection", "pk": 1,
             "fields": {"key": "b1_paps", "value": None}}]))

    def test_the_people_follow_the_childs_social_worker(self):
        psychologist = make_user("p@t.ph", Role.PSYCHOLOGIST, "Pia", "Reyes")
        staff = [make_user("s1@t.ph", Role.STAFF, "Sam", "One"),
                 make_user("s2@t.ph", Role.STAFF, "Sue", "Two")]
        rows = [
            {"model": "children.child", "pk": 10,
             "fields": {"assigned_psychologist": 501, "social_worker": 601}},
            {"model": "children.child", "pk": 11,
             "fields": {"assigned_psychologist": 501, "social_worker": 601}},
            {"model": "case_study.casestudy", "pk": 20,
             "fields": {"child": 11, "created_by": 777}},
            {"model": "case_study.casestudysection", "pk": 30,
             "fields": {"case_study": 20, "key": "a2_sources", "updated_by": 501}},
            {"model": "case_study.casestudyfinal", "pk": 31,
             "fields": {"case_study": 20, "finalized_by": 999, "snapshot": {}}},
        ]
        rehome_people(rows, [psychologist], staff)
        second_child_worker = rows[1]["fields"]["social_worker"]
        self.assertEqual(staff[1].pk, second_child_worker)
        self.assertEqual(second_child_worker, rows[2]["fields"]["created_by"])
        self.assertEqual(second_child_worker, rows[3]["fields"]["updated_by"])
        self.assertEqual(second_child_worker, rows[4]["fields"]["finalized_by"])

    def test_with_no_social_worker_here_they_are_left_unnamed(self):
        psychologist = make_user("p@t.ph", Role.PSYCHOLOGIST, "Pia", "Reyes")
        rows = [
            {"model": "children.child", "pk": 10, "fields": {"social_worker": 601}},
            {"model": "case_study.casestudy", "pk": 20,
             "fields": {"child": 10, "created_by": 601}},
            {"model": "case_study.casestudysection", "pk": 30,
             "fields": {"case_study": 20, "key": "a2_sources", "updated_by": 601}},
        ]
        rehome_people(rows, [psychologist], [])
        self.assertIsNone(rows[1]["fields"]["created_by"])
        self.assertIsNone(rows[2]["fields"]["updated_by"])

    def test_the_whole_import(self):
        make_user("p@t.ph", Role.PSYCHOLOGIST, "Pia", "Reyes")
        worker = make_user("s@t.ph", Role.STAFF, "Sam", "One")
        stamp = "2026-08-01T00:00:00Z"
        rows = [
            {"model": "children.child", "pk": 900,
             "fields": {"fullname": "Demo Child", "case_type": "Adoption",
                        "assigned_psychologist": 501, "social_worker": 601,
                        "created_at": stamp, "updated_at": stamp}},
            {"model": "case_study.casestudy", "pk": 5,
             "fields": {"child": 900, "status": "draft", "created_by": 601,
                        "date_prepared": None, "custody_over_two_years": None,
                        "created_at": stamp, "updated_at": stamp}},
            {"model": "case_study.casestudysection", "pk": 6,
             "fields": {"case_study": 5, "key": "b1_paps", "value": {
                 "female": {"full_name": "Maria Reyes", "mobile_phone": "09171234567",
                            "email": "maria@example.com"}, "male": {}},
                        "not_applicable": False, "version": 1, "updated_by": 601,
                        "updated_at": stamp}},
        ]
        path = Path(tempfile.mkdtemp()) / "demo.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        try:
            out = StringIO()
            call_command("import_demo_data", fixture=str(path), stdout=out)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        study = CaseStudy.objects.get(child_id=900)
        self.assertEqual(worker, study.created_by)
        section = study.sections.get(key="b1_paps")
        self.assertEqual(worker, section.updated_by)
        self.assertEqual({"female": {"full_name": "Maria Reyes"}, "male": {}}, section.value)
        self.assertIn("left behind", out.getvalue())
        # And the social worker it was dealt to can open it.
        client = APIClient()
        client.force_authenticate(worker)
        res = client.get(f"/api/case-studies/child/{study.child_id}/")
        self.assertEqual(200, res.status_code)
        saved = {s["key"]: s for s in res.data["sections"]}
        self.assertEqual("Maria Reyes", saved["b1_paps"]["value"]["female"]["full_name"])

    # --- the final copies --------------------------------------------------------

    @staticmethod
    def a_snapshot():
        contact = {"mobile_phone": "09171234567", "home_phone": "0722222222",
                   "work_phone": "0733333333", "email": "maria@example.com",
                   "employer_address": "Rizal St., call 09170000000"}
        return {
            "date_prepared": "2026-10-01",
            "child": {"id": 900, "fullname": "Demo Child"},
            "sections": {
                "b1_paps": {"value": {
                    "female": {"full_name": "Maria Reyes", "religion": "Roman Catholic", **contact},
                    "male": {"full_name": "Jose Reyes", **contact}}, "not_applicable": False},
                "b4_motivation": {"value": "Call 09171234567 any time.", "not_applicable": False},
            },
            "preparer": {"name": "A Social Worker On The Exporting Machine",
                         "license_number": "0012345", "license_valid_until": "2027-06-30"},
            "agency": {"agency_name": "RACCO 1"},
        }

    def test_a_final_copys_preparer_becomes_the_social_worker_here_with_no_license(self):
        psychologist = make_user("p@t.ph", Role.PSYCHOLOGIST, "Pia", "Reyes")
        staff = [make_user("s1@t.ph", Role.STAFF, "Sam", "One"),
                 make_user("s2@t.ph", Role.STAFF, "Sue", "Two")]
        rows = [
            {"model": "children.child", "pk": 10, "fields": {"social_worker": 601}},
            {"model": "children.child", "pk": 11, "fields": {"social_worker": 601}},
            {"model": "case_study.casestudy", "pk": 20, "fields": {"child": 11}},
            {"model": "case_study.casestudyfinal", "pk": 31,
             "fields": {"case_study": 20, "finalized_by": 601, "snapshot": self.a_snapshot()}},
        ]
        rehome_people(rows, [psychologist], staff)
        snapshot = rows[3]["fields"]["snapshot"]
        # The second child went to the second social worker.
        self.assertEqual({"name": "Sue Two", "license_number": "", "license_valid_until": None},
                         snapshot["preparer"])
        self.assertEqual(staff[1].pk, rows[3]["fields"]["finalized_by"])
        # Nothing else in the copy is touched.
        self.assertEqual("RACCO 1", snapshot["agency"]["agency_name"])
        self.assertEqual("2026-10-01", snapshot["date_prepared"])

    def test_with_no_social_worker_here_the_preparer_is_left_unnamed(self):
        psychologist = make_user("p@t.ph", Role.PSYCHOLOGIST, "Pia", "Reyes")
        rows = [
            {"model": "children.child", "pk": 10, "fields": {"social_worker": 601}},
            {"model": "case_study.casestudy", "pk": 20, "fields": {"child": 10}},
            {"model": "case_study.casestudyfinal", "pk": 31,
             "fields": {"case_study": 20, "finalized_by": 601, "snapshot": self.a_snapshot()}},
        ]
        rehome_people(rows, [psychologist], [])
        self.assertEqual({"name": "", "license_number": "", "license_valid_until": None},
                         rows[2]["fields"]["snapshot"]["preparer"])
        self.assertIsNone(rows[2]["fields"]["finalized_by"])

    def test_the_whole_import_with_a_final(self):
        make_user("p@t.ph", Role.PSYCHOLOGIST, "Pia", "Reyes")
        worker = make_user("s@t.ph", Role.STAFF, "Sam", "One")
        stamp = "2026-08-01T00:00:00Z"
        rows = [
            {"model": "children.child", "pk": 900,
             "fields": {"fullname": "Demo Child", "case_type": "Adoption",
                        "assigned_psychologist": 501, "social_worker": 601,
                        "created_at": stamp, "updated_at": stamp}},
            {"model": "case_study.casestudy", "pk": 5,
             "fields": {"child": 900, "status": "final", "created_by": 601,
                        "date_prepared": "2026-10-01", "custody_over_two_years": None,
                        "created_at": stamp, "updated_at": stamp}},
            {"model": "case_study.casestudyfinal", "pk": 7,
             "fields": {"case_study": 5, "finalized_by": 601, "finalized_at": stamp,
                        "snapshot": self.a_snapshot()}},
        ]
        path = Path(tempfile.mkdtemp()) / "demo.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        try:
            call_command("import_demo_data", fixture=str(path), stdout=StringIO())
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
        final = CaseStudyFinal.objects.get()
        self.assertEqual(worker, final.finalized_by)
        snapshot = final.snapshot
        self.assertEqual({"name": "Sam One", "license_number": "", "license_valid_until": None},
                         snapshot["preparer"])
        table = snapshot["sections"]["b1_paps"]["value"]
        self.assertEqual({"full_name": "Maria Reyes", "religion": "Roman Catholic"}, table["female"])
        self.assertEqual({"full_name": "Jose Reyes"}, table["male"])
        # Free text elsewhere is left as the author wrote it; only the table
        # of adoptive parents is read for contact rows.
        self.assertIn("09171234567", snapshot["sections"]["b4_motivation"]["value"])
        # And the social worker it was dealt to can open it.
        client = APIClient()
        client.force_authenticate(worker)
        listing = client.get(f"/api/case-studies/child/{final.case_study.child_id}/")
        self.assertEqual(200, listing.status_code)
        self.assertEqual(1, len(listing.data["finals"]))
        copy = client.get(
            f"/api/case-studies/child/{final.case_study.child_id}/finals/{final.pk}/")
        self.assertEqual(200, copy.status_code)
        self.assertEqual("Sam One", copy.data["snapshot"]["preparer"]["name"])
