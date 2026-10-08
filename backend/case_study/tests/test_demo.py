"""Demo case studies: the seeder's drafts, and their way to a hosted branch.

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
from case_study.models import CaseStudy, CaseStudyFinal, CaseStudySection
from case_study.sections import DVC_NOTARIZED, DVC_SIGNED, PAP_ROWS, applies, entry_for
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

    def test_every_third_adoption_child_gets_a_draft(self):
        kids = self.children(7)
        Child.objects.create(first_name="Fos", last_name="Ter", case_type="Foster Care",
                             social_worker=self.sw, birth_date=date(2015, 1, 1))
        made = demo_case_studies.install_case_studies(
            list(Child.objects.order_by("pk")), today=TODAY)
        # The setUpTestData child is the first adoption child, then the seven.
        adoption = [self.child] + kids
        self.assertEqual(3, made)
        self.assertEqual({c.pk for c in adoption[::3]},
                         set(CaseStudy.objects.values_list("child_id", flat=True)))

    def test_running_it_again_adds_nothing(self):
        self.children(7)
        everyone = list(Child.objects.order_by("pk"))
        demo_case_studies.install_case_studies(everyone, today=TODAY)
        sections = CaseStudySection.objects.count()
        self.assertEqual(0, demo_case_studies.install_case_studies(everyone, today=TODAY))
        self.assertEqual(sections, CaseStudySection.objects.count())

    def test_a_child_with_no_social_worker_gets_none(self):
        Child.objects.filter(pk=self.child.pk).update(social_worker=None)
        self.assertEqual(0, demo_case_studies.install_case_studies(
            [Child.objects.get(pk=self.child.pk)], today=TODAY))

    def test_a_draft_is_its_social_workers_own(self):
        self.children(7)
        demo_case_studies.install_case_studies(list(Child.objects.order_by("pk")), today=TODAY)
        for study in CaseStudy.objects.select_related("child"):
            self.assertEqual(study.child.social_worker, study.created_by)
            self.assertEqual(CaseStudy.DRAFT, study.status)
            for section in study.sections.all():
                self.assertEqual(study.child.social_worker, section.updated_by)
                self.assertEqual(1, section.version)

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
        studies = list(CaseStudy.objects.all())
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
        depths = {s.sections.count() for s in CaseStudy.objects.all()}
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


@override_settings(DEBUG=True)
class SeederTest(TestCase):
    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def test_the_seeder_gives_adoption_children_a_draft(self):
        province = Province.objects.create(psgc_code="012800000", name="Ilocos Norte")
        town = Municipality.objects.create(psgc_code="012812000", name="Laoag City",
                                           province=province)
        Barangay.objects.create(psgc_code="012812001", name="Barangay 1", municipality=town)
        out = StringIO()
        call_command("seed_demo_data", children=30, stdout=out)
        adoption = list(Child.objects.filter(case_type="Adoption").order_by("pk"))
        self.assertGreaterEqual(len(adoption), 3, "the seed should draw some adoptions")
        made = CaseStudy.objects.count()
        self.assertEqual(len(adoption[::3]), made)
        self.assertIn(f"{made} case study drafts written", out.getvalue())
        for section in CaseStudySection.objects.select_related("case_study__child"):
            entry = entry_for(section.key)
            self.assertTrue(applies(entry, section.case_study.child, section.case_study))
            if not section.not_applicable:
                self.assertEqual(section.value, clean_value(entry, section.value, today=TODAY))
        self.assertEqual(0, CaseStudyFinal.objects.count())


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
