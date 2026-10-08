"""Who sees what of a child's case study (owner's decisions, 8 Oct 2026).

The record's social worker reads and writes everything; the assigned
psychologist reads block A only and never writes; the ISA sees a status and no
text at all; anyone else is told there is no such child. The rules live in
case_study/access.py and nowhere else.
"""
import json

from django.contrib import admin

from accounts.models import User
from case_study.models import CaseStudy, CaseStudyFinal, CaseStudySection
from case_study.sections import BLOCK_A_KEYS, SCSR_SECTIONS
from case_study.tests.base import CaseStudyTestCase
from children import assignment
from children.models import AssignmentRequest, Child

SECRET = "SECRET-TEXT-ABOUT-THE-FAMILY"


class AccessBase(CaseStudyTestCase):
    def setUp(self):
        super().setUp()
        self.study = self.start(date_prepared=None)
        for key, value in (("a2_circumstances", f"Circumstances {SECRET}"),
                           ("a3_psych_highlights", f"Findings {SECRET}"),
                           ("b4_motivation", f"Motivation {SECRET}"),
                           ("c5_assessment", f"Assessment {SECRET}")):
            CaseStudySection.objects.create(
                case_study=self.study, key=key, value=value, updated_by=self.sw)


class AnonymousAndStrangersTest(AccessBase):
    def test_anonymous_is_refused_everywhere(self):
        client = self.as_user(None)
        url, section = self.url(), self.section_url("a2_circumstances")
        self.assertEqual(401, client.get(url).status_code)
        self.assertEqual(401, client.post(url, {}, format="json").status_code)
        self.assertEqual(401, client.patch(url, {}, format="json").status_code)
        self.assertEqual(401, client.put(section, {}, format="json").status_code)

    def test_another_social_worker_finds_nothing(self):
        client = self.as_user(self.sw2)
        self.assertEqual(404, client.get(self.url()).status_code)
        self.assertEqual(404, client.post(self.url(), {}, format="json").status_code)
        self.assertEqual(404, client.patch(self.url(), {}, format="json").status_code)
        self.assertEqual(404, self.save_section("a2_circumstances", "x", user=self.sw2).status_code)

    def test_a_psychologist_who_was_only_asked_finds_nothing(self):
        # A pending request is not an assignment.
        AssignmentRequest.objects.create(
            child=self.child, psychologist=self.psy2, requested_by=self.sw)
        client = self.as_user(self.psy2)
        self.assertEqual(404, client.get(self.url()).status_code)
        self.assertEqual(404, self.save_section("a2_circumstances", "x", user=self.psy2).status_code)

    def test_a_psychologist_who_never_had_the_child_finds_nothing(self):
        self.assertEqual(404, self.as_user(self.psy2).get(self.url()).status_code)

    def test_the_previous_psychologist_after_a_transfer_finds_nothing(self):
        request = AssignmentRequest.objects.create(
            child=self.child, psychologist=self.psy2, requested_by=self.sw,
            previous_psychologist=self.psy)
        assignment.accept(request, by=self.psy2)
        self.assertEqual(404, self.as_user(self.psy).get(self.url()).status_code)
        self.assertEqual(200, self.as_user(self.psy2).get(self.url()).status_code)

    def test_a_child_that_does_not_exist(self):
        self.assertEqual(404, self.as_user(self.sw).get("/api/case-studies/child/999999/").status_code)
        self.assertEqual(404, self.as_user(self.isa).get("/api/case-studies/child/999999/").status_code)

    def test_nothing_is_written_by_a_refused_request(self):
        self.as_user(self.sw2).put(self.section_url("a2_sources"), {"value": ["x"]}, format="json")
        self.as_user(self.psy).put(self.section_url("a2_sources"), {"value": ["x"]}, format="json")
        self.as_user(self.isa).put(self.section_url("a2_sources"), {"value": ["x"]}, format="json")
        self.assertFalse(CaseStudySection.objects.filter(key="a2_sources").exists())


class SocialWorkerTest(AccessBase):
    def test_the_record_holder_reads_everything_including_a_draft(self):
        body = self.as_user(self.sw).get(self.url()).data
        self.assertTrue(body["exists"])
        self.assertEqual("draft", body["status"])
        self.assertFalse(body["read_only"])
        self.assertEqual([e["key"] for e in SCSR_SECTIONS], [s["key"] for s in body["sections"]])
        saved = {s["key"]: s for s in body["sections"]}
        self.assertIn(SECRET, saved["b4_motivation"]["value"])
        self.assertIn(SECRET, saved["c5_assessment"]["value"])

    def test_the_record_holder_writes(self):
        self.assertEqual(200, self.save_section("a2_sources", ["The child"], version=0).status_code)


class PsychologistTest(AccessBase):
    def get(self, user=None):
        return self.as_user(user or self.psy).get(self.url())

    def test_block_a_only_and_drafts_included(self):
        res = self.get()
        self.assertEqual(200, res.status_code)
        self.assertEqual("draft", res.data["status"])
        keys = [s["key"] for s in res.data["sections"]]
        self.assertEqual(list(BLOCK_A_KEYS), keys)
        self.assertTrue(all(k.startswith("a") for k in keys))
        text = json.dumps(res.data)
        self.assertIn("Circumstances", text)
        self.assertNotIn("Motivation", text)
        self.assertNotIn("Assessment", text)

    def test_it_is_read_only_and_says_so(self):
        res = self.get()
        self.assertIs(True, res.data["read_only"])
        self.assertIn("social worker", res.data["read_only_reason"])

    def test_no_seeds_no_missing_list_and_no_custody(self):
        res = self.get()
        for absent in ("seeds", "missing", "custody_over_two_years", "custody_pre_answer"):
            self.assertNotIn(absent, res.data)
        self.assertIn("record_facts", res.data)

    def test_the_psychological_highlights_are_hidden_where_history_is_not_carried(self):
        self.assertIn("a3_psych_highlights", [s["key"] for s in self.get().data["sections"]])
        Child.objects.filter(pk=self.child.pk).update(assignee_sees_history=False)
        res = self.get()
        self.assertNotIn("a3_psych_highlights", [s["key"] for s in res.data["sections"]])
        self.assertNotIn("Findings", json.dumps(res.data))
        self.assertIn("a2_circumstances", [s["key"] for s in res.data["sections"]])

    def test_a_psychologist_cannot_write_anything(self):
        client = self.as_user(self.psy)
        self.assertEqual(403, self.save_section("a2_sources", ["x"], user=self.psy).status_code)
        self.assertEqual(403, client.post(self.url(), {}, format="json").status_code)
        self.assertEqual(403, client.patch(self.url(), {"date_prepared": "2026-10-01"},
                                           format="json").status_code)
        self.assertFalse(CaseStudySection.objects.filter(key="a2_sources").exists())
        self.assertIsNone(CaseStudy.objects.get(pk=self.study.pk).date_prepared)

    def test_a_psychologist_sees_that_none_has_been_started(self):
        CaseStudy.objects.all().delete()
        res = self.get()
        self.assertFalse(res.data["exists"])
        self.assertEqual([], res.data["sections"])


class AdministratorTest(AccessBase):
    def get(self):
        return self.as_user(self.isa).get(self.url())

    def test_a_status_and_no_text_anywhere(self):
        res = self.get()
        self.assertEqual(200, res.status_code)
        self.assertEqual(
            {"exists", "status", "holder_name", "holder_active", "updated_at",
             "last_finalized_at", "missing_count"}, set(res.data))
        self.assertNotIn(SECRET, json.dumps(res.data))
        self.assertNotIn("sections", res.data)
        self.assertNotIn("record_facts", res.data)
        self.assertNotIn("seeds", res.data)

    def test_what_it_does_say(self):
        res = self.get()
        self.assertTrue(res.data["exists"])
        self.assertEqual("draft", res.data["status"])
        self.assertEqual("Editha Pascua", res.data["holder_name"])
        self.assertIs(True, res.data["holder_active"])
        self.assertIsNone(res.data["last_finalized_at"])
        self.assertIsNotNone(res.data["updated_at"])
        # Date prepared plus every applicable box still empty (three of the four
        # saved ones are in block A or C and count as filled).
        self.assertGreater(res.data["missing_count"], 20)

    def test_the_count_is_the_one_the_social_worker_sees(self):
        sw_missing = self.as_user(self.sw).get(self.url()).data["missing"]
        self.assertEqual(len(sw_missing), self.get().data["missing_count"])

    def test_an_archived_holder_is_flagged_so_a_stranded_draft_can_be_moved(self):
        self.sw.status = User.ARCHIVED
        self.sw.save()
        res = self.get()
        self.assertEqual("Editha Pascua", res.data["holder_name"])
        self.assertIs(False, res.data["holder_active"])

    def test_a_record_nobody_holds(self):
        Child.objects.filter(pk=self.child.pk).update(social_worker=None)
        res = self.get()
        self.assertIsNone(res.data["holder_name"])
        self.assertIs(False, res.data["holder_active"])

    def test_when_it_was_last_finalized(self):
        CaseStudyFinal.objects.create(case_study=self.study, snapshot={}, finalized_by=self.sw)
        self.assertIsNotNone(self.get().data["last_finalized_at"])

    def test_before_one_is_started(self):
        CaseStudy.objects.all().delete()
        res = self.get()
        self.assertFalse(res.data["exists"])
        self.assertIsNone(res.data["status"])
        self.assertIsNone(res.data["missing_count"])

    def test_the_isa_cannot_write(self):
        client = self.as_user(self.isa)
        self.assertEqual(403, self.save_section("a2_sources", ["x"], user=self.isa).status_code)
        self.assertEqual(403, client.post(self.url(), {}, format="json").status_code)
        self.assertEqual(403, client.patch(self.url(), {"date_prepared": "2026-10-01"},
                                           format="json").status_code)


class NotInTheAdminTest(CaseStudyTestCase):
    def test_the_seeded_isa_is_a_superuser_and_the_models_are_still_not_there(self):
        # IT support must not be able to read a social worker's case study.
        for model in (CaseStudy, CaseStudySection, CaseStudyFinal):
            self.assertFalse(admin.site.is_registered(model), model.__name__)
