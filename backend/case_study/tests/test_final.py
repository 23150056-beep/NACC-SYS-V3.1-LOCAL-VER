"""Final and Reopen (phase P2): who may, what is refused, what a final keeps.

Design: docs/superpowers/specs/2026-10-07-scsr-parts-2-5-design.md, "Final and
Reopen". The clock is pinned by the base class, so a final made here is
"finalized" at 12:00 on 8 Oct 2026, Manila time.
"""
import json
from datetime import date, timedelta
from unittest.mock import patch

from django.utils.dateparse import parse_datetime

from accounts.models import AgencyProfile, UserProfile
from activity.models import ActivityLog
from case_study.completeness import missing_sections
from case_study.finalize import CannotFinalize, finalize
from case_study.models import CaseStudy, CaseStudyFinal, CaseStudySection
from case_study.sections import SCSR_SECTIONS, applies
from case_study.serializers import iso_datetime, record_facts
from case_study.tests.base import NOW, CaseStudyTestCase, make_user
from case_study.tests.test_completeness import good_value
from children.models import AssignmentRequest, Child

PREPARED = date(2026, 10, 1)
STALE = ("The case study was changed in another tab or by someone else since you "
         "opened it. Reload it before making it final.")


class FinalTestCase(CaseStudyTestCase):
    """A case study for the child, complete unless a test empties it."""

    def setUp(self):
        super().setUp()
        self.study = self.start(date_prepared=PREPARED)
        self.fill_everything()

    def fill_everything(self):
        child = Child.objects.get(pk=self.child.pk)
        for entry in SCSR_SECTIONS:
            if applies(entry, child, self.study):
                CaseStudySection.objects.update_or_create(
                    case_study=self.study, key=entry["key"],
                    defaults={"value": good_value(entry), "updated_by": self.sw})
        self.study.refresh_from_db()

    def later(self, minutes=5):
        """The clock moved on. It is pinned, so a save would otherwise leave
        `updated_at` exactly where it was and nothing could look stale."""
        return patch("django.utils.timezone.now", return_value=NOW + timedelta(minutes=minutes))

    def stamp(self, child=None):
        """The case study's updated_at as the screen would send it back."""
        return iso_datetime(CaseStudy.objects.get(child=child or self.child).updated_at)

    def final_url(self, child=None):
        return f"{self.url(child)}final/"

    def reopen_url(self, child=None):
        return f"{self.url(child)}reopen/"

    def finals_url(self, final_id, child=None):
        return f"{self.url(child)}finals/{final_id}/"

    def make_final(self, user=None, child=None, expected="current"):
        body = {} if expected is None else {
            "expected_updated_at": self.stamp(child) if expected == "current" else expected}
        return self.as_user(user or self.sw).post(self.final_url(child), body, format="json")

    def reopen(self, user=None, child=None):
        return self.as_user(user or self.sw).post(self.reopen_url(child), {}, format="json")

    def snapshot(self):
        return CaseStudyFinal.objects.latest("id").snapshot


class MakingItFinalTest(FinalTestCase):
    def test_it_is_refused_with_what_is_still_to_complete(self):
        CaseStudySection.objects.filter(
            case_study=self.study, key__in=["a2_sources", "b4_motivation", "c6_recommendation"]
        ).delete()
        CaseStudy.objects.filter(pk=self.study.pk).update(date_prepared=None)
        res = self.make_final()
        self.assertEqual(400, res.status_code)
        self.assertEqual("Complete these before making it final.", res.data["detail"])
        self.assertEqual(
            ["Date prepared", "Sources of information", "Motivation to adopt", "Recommendation"],
            res.data["missing"])
        self.assertEqual(0, CaseStudyFinal.objects.count())
        self.assertEqual(CaseStudy.DRAFT, CaseStudy.objects.get(pk=self.study.pk).status)

    def test_the_list_is_the_one_the_screen_already_has(self):
        CaseStudySection.objects.filter(case_study=self.study, key="b9_family_attitude").delete()
        shown = self.as_user(self.sw).get(self.url()).data["missing"]
        self.assertEqual(shown, self.make_final().data["missing"])
        self.assertEqual(shown, missing_sections(CaseStudy.objects.get(pk=self.study.pk)))

    def test_a_complete_one_becomes_final_and_writes_one_copy(self):
        res = self.make_final()
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("final", res.data["status"])
        self.assertFalse(res.data["can_finalize"])
        self.assertEqual(1, CaseStudyFinal.objects.count())
        final = CaseStudyFinal.objects.get()
        self.assertEqual(self.sw, final.finalized_by)
        self.assertEqual(CaseStudy.FINAL, CaseStudy.objects.get(pk=self.study.pk).status)
        self.assertEqual([{"id": final.pk, "finalized_at": iso_datetime(final.finalized_at),
                           "finalized_by_name": "Editha Pascua"}], res.data["finals"])

    def test_the_answer_is_the_social_workers_reading_of_it(self):
        res = self.make_final()
        again = self.as_user(self.sw).get(self.url()).data
        self.assertEqual(again, res.data)
        self.assertTrue(res.data["read_only"])
        self.assertIn("final", res.data["read_only_reason"])

    def test_the_copy_holds_what_the_printed_report_shows(self):
        UserProfile.objects.create(
            user=self.sw, license_number="0012345", license_valid_until=date(2027, 6, 30))
        AgencyProfile.objects.create(
            agency_name="RACCO 1", office_address="Rizal St., Laoag City",
            contact_details="Office hours only", head_of_office_name="Dir. Ben Cruz",
            head_of_office_title="Regional Director")
        self.make_final()
        snap = self.snapshot()
        self.assertEqual(
            {"date_prepared", "custody_over_two_years", "child", "part_one", "sections",
             "preparer", "agency"}, set(snap))
        self.assertEqual("2026-10-01", snap["date_prepared"])
        self.assertIsNone(snap["custody_over_two_years"])
        self.assertEqual({"id": self.child.pk, "fullname": "Ana Cruz", "case_type": "Adoption",
                          "case_category": "Surrendered", "type_of_adoption": "Regular"},
                         snap["child"])
        # Part I as the record holds it, the age as of the date prepared (the
        # child was born 2 Mar 2019, so 7 on 1 Oct 2026).
        self.assertEqual(json.loads(json.dumps(record_facts(self.child, PREPARED))),
                         snap["part_one"])
        self.assertEqual(7, snap["part_one"]["age"])
        self.assertEqual("2026-10-01", snap["part_one"]["age_as_of"])
        self.assertEqual({"name": "Editha Pascua", "license_number": "0012345",
                          "license_valid_until": "2027-06-30"}, snap["preparer"])
        self.assertEqual({"agency_name": "RACCO 1", "office_address": "Rizal St., Laoag City",
                          "contact_details": "Office hours only",
                          "head_of_office_name": "Dir. Ben Cruz",
                          "head_of_office_title": "Regional Director"}, snap["agency"])

    def test_the_copy_holds_every_box_that_applied_and_only_those(self):
        # A Surrendered child: the abandonment boxes did not apply, and text
        # kept in one is not part of the report.
        CaseStudySection.objects.create(
            case_study=self.study, key="a5_abandonment", value="Kept from an earlier answer.")
        self.make_final()
        sections = self.snapshot()["sections"]
        child = Child.objects.get(pk=self.child.pk)
        wanted = [e["key"] for e in SCSR_SECTIONS if applies(e, child, self.study)]
        self.assertEqual(wanted, list(sections))
        self.assertNotIn("a5_abandonment", sections)
        self.assertNotIn("a5_search_efforts", sections)
        for key, box in sections.items():
            self.assertEqual({"value", "not_applicable"}, set(box), key)
        self.assertEqual("Written text.", sections["a2_circumstances"]["value"])

    def test_a_box_ticked_not_applicable_keeps_no_text_in_the_copy(self):
        CaseStudySection.objects.filter(case_study=self.study, key="b7_children_in_family").update(
            value="Typed before ticking Not applicable.", not_applicable=True)
        self.make_final()
        box = self.snapshot()["sections"]["b7_children_in_family"]
        self.assertEqual({"value": None, "not_applicable": True}, box)
        # The live row still has the text: unticking after a reopen brings it back.
        live = CaseStudySection.objects.get(case_study=self.study, key="b7_children_in_family")
        self.assertEqual("Typed before ticking Not applicable.", live.value)

    def test_no_license_on_the_profile_leaves_it_blank(self):
        self.make_final()
        self.assertEqual({"name": "Editha Pascua", "license_number": "", "license_valid_until": None},
                         self.snapshot()["preparer"])

    def test_the_agency_blank_is_kept_blank(self):
        self.make_final()
        self.assertEqual({"agency_name": "", "office_address": "", "contact_details": "",
                          "head_of_office_name": "", "head_of_office_title": ""},
                         self.snapshot()["agency"])

    def test_a_license_changed_afterwards_does_not_change_the_copy(self):
        UserProfile.objects.create(user=self.sw, license_number="0012345",
                                   license_valid_until=date(2027, 6, 30))
        self.make_final()
        before = self.snapshot()
        UserProfile.objects.filter(user=self.sw).update(
            license_number="0099999", license_valid_until=date(2031, 1, 1))
        self.assertEqual(before, CaseStudyFinal.objects.get().snapshot)
        self.assertEqual("0012345", CaseStudyFinal.objects.get().snapshot["preparer"]["license_number"])
        got = self.as_user(self.sw).get(self.finals_url(CaseStudyFinal.objects.get().pk)).data
        self.assertEqual("0012345", got["snapshot"]["preparer"]["license_number"])
        self.assertEqual("2027-06-30", got["snapshot"]["preparer"]["license_valid_until"])

    def test_a_head_of_office_changed_afterwards_does_not_change_the_copy(self):
        AgencyProfile.objects.create(agency_name="RACCO 1", head_of_office_name="Dir. Ben Cruz",
                                     head_of_office_title="Regional Director")
        self.make_final()
        AgencyProfile.objects.filter(pk=1).update(
            agency_name="Another Office", head_of_office_name="Dir. Someone Else")
        agency = CaseStudyFinal.objects.get().snapshot["agency"]
        self.assertEqual("RACCO 1", agency["agency_name"])
        self.assertEqual("Dir. Ben Cruz", agency["head_of_office_name"])

    def test_the_record_edited_afterwards_does_not_change_the_copy(self):
        self.make_final()
        Child.objects.filter(pk=self.child.pk).update(
            first_name="Renamed", fullname="Renamed Cruz", place_of_birth_or_found="Elsewhere")
        snap = CaseStudyFinal.objects.get().snapshot
        self.assertEqual("Ana Cruz", snap["child"]["fullname"])
        self.assertEqual("Ana Cruz", snap["part_one"]["fullname"])
        self.assertNotEqual("Elsewhere", snap["part_one"]["place_of_birth_or_found"])

    def test_a_second_call_is_a_conflict_and_writes_nothing(self):
        self.assertEqual(200, self.make_final().status_code)
        again = self.make_final()
        self.assertEqual(409, again.status_code)
        self.assertEqual("This case study is already final.", again.data["detail"])
        self.assertEqual(1, CaseStudyFinal.objects.count())

    def test_a_request_that_read_a_draft_after_another_made_it_final_loses(self):
        # Two clicks, or two tabs: both read the case study as a draft and
        # both pass every check; the first one's update lands, and the second
        # finds nothing left to move. This is the call the second one makes.
        slower = CaseStudy.objects.select_related("child").get(pk=self.study.pk)
        self.assertEqual(200, self.make_final().status_code)
        with self.assertRaises(CannotFinalize) as caught:
            finalize(slower, self.sw)
        self.assertEqual(409, caught.exception.status)
        self.assertEqual(STALE, caught.exception.message)
        self.assertEqual(1, CaseStudyFinal.objects.count())

    def test_a_case_study_changed_since_it_was_opened_is_a_conflict(self):
        stale = self.stamp()
        # Somebody saves a box in another tab.
        with self.later():
            res = self.save_section("a2_sources", ["The child", "A neighbor"], version=1)
        self.assertEqual(200, res.status_code, res.data)
        self.assertNotEqual(stale, self.stamp())
        res = self.make_final(expected=stale)
        self.assertEqual(409, res.status_code)
        self.assertEqual(STALE, res.data["detail"])
        self.assertEqual(0, CaseStudyFinal.objects.count())
        self.assertEqual(CaseStudy.DRAFT, CaseStudy.objects.get(pk=self.study.pk).status)
        # With the version it now has, it goes through.
        self.assertEqual(200, self.make_final().status_code)

    def test_a_changed_date_prepared_is_a_change_too(self):
        stale = self.stamp()
        with self.later():
            self.assertEqual(200, self.as_user(self.sw).patch(
                self.url(), {"date_prepared": "2026-10-02"}, format="json").status_code)
        self.assertEqual(409, self.make_final(expected=stale).status_code)

    def test_the_version_is_compared_exactly(self):
        stamp = self.stamp()
        # One microsecond out is a different version.
        later = (parse_datetime(stamp) + timedelta(microseconds=1)).isoformat()
        self.assertEqual(409, self.make_final(expected=later).status_code)
        self.assertEqual(200, self.make_final(expected=stamp).status_code)

    def test_the_version_is_required(self):
        for bad in (None, "", "yesterday", 5, "2026-10-08T12:00:00"):
            res = self.make_final(expected=bad)
            self.assertEqual(400, res.status_code, bad)
            self.assertIn("expected_updated_at", res.data["detail"])
        self.assertEqual(0, CaseStudyFinal.objects.count())

    def test_the_other_refusals_are_the_ones_every_write_gets(self):
        Child.objects.filter(pk=self.child.pk).update(case_status="terminated", status="inactive")
        res = self.make_final()
        self.assertEqual(400, res.status_code)
        self.assertIn("closed", res.data["detail"])
        Child.objects.filter(pk=self.child.pk).update(
            case_status="counseling", status="active", case_type="Foster Care")
        res = self.make_final()
        self.assertEqual(400, res.status_code)
        self.assertIn("adoption records only", res.data["detail"])
        self.assertEqual(0, CaseStudyFinal.objects.count())

    def test_no_case_study_yet_is_not_found(self):
        CaseStudy.objects.all().delete()
        self.assertEqual(404, self.make_final(expected="2026-10-08T12:00:00+08:00").status_code)

    def test_a_copy_can_never_be_changed(self):
        self.make_final()
        final = CaseStudyFinal.objects.get()
        final.snapshot = {}
        with self.assertRaises(ValueError):
            final.save()


class WhileItIsFinalTest(FinalTestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(200, self.make_final().status_code)

    def test_a_box_cannot_be_saved(self):
        res = self.save_section("a2_sources", ["x"], version=1)
        self.assertEqual(400, res.status_code)
        self.assertEqual("This case study is final. Reopen it to change it.", res.data["detail"])
        self.assertEqual(["One line"], CaseStudySection.objects.get(key="a2_sources").value)

    def test_the_date_and_the_custody_answer_cannot_be_changed(self):
        res = self.as_user(self.sw).patch(self.url(), {"date_prepared": "2026-10-02"}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertEqual("This case study is final. Reopen it to change it.", res.data["detail"])
        self.assertEqual(PREPARED, CaseStudy.objects.get(pk=self.study.pk).date_prepared)

    def test_a_save_that_loses_the_race_to_final_changes_nothing(self):
        # The save passed its own check while the case study was still a
        # draft; Final lands before it writes. Standing in for that: the
        # check says yes although the case study is already final.
        with patch("case_study.views.writes_refused", return_value=None):
            res = self.save_section("a2_sources", ["x"], version=1)
            patched = self.as_user(self.sw).patch(
                self.url(), {"date_prepared": "2026-10-02"}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertEqual(400, patched.status_code)
        row = CaseStudySection.objects.get(key="a2_sources")
        self.assertEqual(["One line"], row.value)
        self.assertEqual(1, row.version)
        self.assertEqual(PREPARED, CaseStudy.objects.get(pk=self.study.pk).date_prepared)

    def test_a_final_one_is_not_started_again(self):
        self.assertEqual(409, self.as_user(self.sw).post(self.url(), {}, format="json").status_code)


class ReopeningTest(FinalTestCase):
    def test_it_goes_back_to_a_draft_and_the_final_stays(self):
        self.make_final()
        res = self.reopen()
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("draft", res.data["status"])
        self.assertFalse(res.data["read_only"])
        self.assertEqual(1, len(res.data["finals"]))
        self.assertEqual(1, CaseStudyFinal.objects.count())
        self.assertEqual(CaseStudy.DRAFT, CaseStudy.objects.get(pk=self.study.pk).status)

    def test_a_draft_cannot_be_reopened(self):
        res = self.reopen()
        self.assertEqual(409, res.status_code)
        self.assertIn("not final", res.data["detail"])

    def test_reopening_twice_is_a_conflict(self):
        self.make_final()
        self.assertEqual(200, self.reopen().status_code)
        self.assertEqual(409, self.reopen().status_code)

    def test_writes_are_allowed_again(self):
        self.make_final()
        self.reopen()
        res = self.save_section("a2_sources", ["The child", "Barangay"], version=1)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(200, self.as_user(self.sw).patch(
            self.url(), {"date_prepared": "2026-10-02"}, format="json").status_code)

    def test_finalizing_again_writes_a_second_copy_and_leaves_the_first(self):
        self.make_final()
        first = self.snapshot()
        self.reopen()
        self.save_section("a2_sources", ["The child", "Barangay"], version=1)
        UserProfile.objects.create(user=self.sw, license_number="0055555")
        res = self.make_final()
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(2, CaseStudyFinal.objects.count())
        newest, oldest = res.data["finals"]
        self.assertGreater(newest["id"], oldest["id"])
        self.assertEqual(first, CaseStudyFinal.objects.get(pk=oldest["id"]).snapshot)
        self.assertEqual(["The child", "Barangay"],
                         CaseStudyFinal.objects.get(pk=newest["id"]).snapshot["sections"]["a2_sources"]["value"])
        self.assertEqual("0055555",
                         CaseStudyFinal.objects.get(pk=newest["id"]).snapshot["preparer"]["license_number"])
        self.assertEqual("", first["preparer"]["license_number"])

    def test_finalizing_straight_after_a_reopen_needs_no_new_edit(self):
        self.make_final()
        self.reopen()
        self.assertEqual(200, self.make_final().status_code)
        self.assertEqual(2, CaseStudyFinal.objects.count())

    def test_a_closed_case_keeps_its_case_study_as_it_was_signed(self):
        self.make_final()
        Child.objects.filter(pk=self.child.pk).update(case_status="terminated", status="inactive")
        res = self.reopen()
        self.assertEqual(400, res.status_code)
        self.assertIn("closed", res.data["detail"])
        self.assertEqual(CaseStudy.FINAL, CaseStudy.objects.get(pk=self.study.pk).status)
        # It can still be read, and its final copy printed.
        self.assertEqual(200, self.as_user(self.sw).get(self.url()).status_code)
        final = CaseStudyFinal.objects.get()
        self.assertEqual(200, self.as_user(self.sw).get(self.finals_url(final.pk)).status_code)


class WhatEachRoleGetsTest(FinalTestCase):
    def test_the_social_worker_can_finalize_when_it_is_a_complete_draft(self):
        body = self.as_user(self.sw).get(self.url()).data
        self.assertTrue(body["can_finalize"])
        self.assertEqual([], body["finals"])

    def test_not_while_something_is_missing_or_already_final_or_closed(self):
        CaseStudySection.objects.filter(case_study=self.study, key="b9_family_attitude").delete()
        self.assertFalse(self.as_user(self.sw).get(self.url()).data["can_finalize"])
        self.fill_everything()
        self.assertTrue(self.as_user(self.sw).get(self.url()).data["can_finalize"])
        Child.objects.filter(pk=self.child.pk).update(case_status="terminated", status="inactive")
        self.assertFalse(self.as_user(self.sw).get(self.url()).data["can_finalize"])
        Child.objects.filter(pk=self.child.pk).update(case_status="counseling", status="active")
        self.make_final()
        self.assertFalse(self.as_user(self.sw).get(self.url()).data["can_finalize"])

    def test_before_one_is_started_there_is_nothing_to_finalize(self):
        CaseStudy.objects.all().delete()
        body = self.as_user(self.sw).get(self.url()).data
        self.assertFalse(body["can_finalize"])
        self.assertEqual([], body["finals"])

    def test_the_list_is_newest_first_and_carries_no_text(self):
        self.make_final()
        self.reopen()
        self.make_final()
        finals = self.as_user(self.sw).get(self.url()).data["finals"]
        self.assertEqual(2, len(finals))
        self.assertGreater(finals[0]["id"], finals[1]["id"])
        for item in finals:
            self.assertEqual({"id", "finalized_at", "finalized_by_name"}, set(item))
            self.assertEqual("Editha Pascua", item["finalized_by_name"])

    def test_a_removed_account_leaves_the_name_blank(self):
        self.make_final()
        CaseStudyFinal.objects.update(finalized_by=None)
        self.assertIsNone(self.as_user(self.sw).get(self.url()).data["finals"][0]["finalized_by_name"])

    def test_the_psychologist_is_told_when_it_was_last_final_and_nothing_more(self):
        before = self.as_user(self.psy).get(self.url()).data
        self.assertIsNone(before["last_finalized_at"])
        self.assertEqual("draft", before["status"])
        self.make_final()
        after = self.as_user(self.psy).get(self.url()).data
        self.assertEqual("final", after["status"])
        self.assertEqual(iso_datetime(CaseStudyFinal.objects.get().finalized_at),
                         after["last_finalized_at"])
        self.assertNotIn("finals", after)
        self.assertNotIn("can_finalize", after)
        self.assertNotIn("missing", after)
        # Reopened: a draft again, and the time of the last final stays.
        self.reopen()
        again = self.as_user(self.psy).get(self.url()).data
        self.assertEqual("draft", again["status"])
        self.assertEqual(after["last_finalized_at"], again["last_finalized_at"])

    def test_the_isa_sees_status_and_the_real_time_it_was_made_final_and_no_text(self):
        self.assertIsNone(self.as_user(self.isa).get(self.url()).data["last_finalized_at"])
        self.make_final()
        body = self.as_user(self.isa).get(self.url()).data
        self.assertEqual("final", body["status"])
        self.assertEqual(iso_datetime(CaseStudyFinal.objects.get().finalized_at),
                         body["last_finalized_at"])
        self.assertEqual(
            {"exists", "status", "holder_name", "holder_active", "updated_at",
             "last_finalized_at", "missing_count"}, set(body))
        self.assertNotIn("Written text", json.dumps(body))
        self.assertEqual(0, body["missing_count"])

    def test_the_isa_reads_the_time_of_the_newest_after_two(self):
        self.make_final()
        self.reopen()
        self.make_final()
        newest = CaseStudyFinal.objects.order_by("-id").first()
        body = self.as_user(self.isa).get(self.url()).data
        self.assertEqual(iso_datetime(newest.finalized_at), body["last_finalized_at"])


class WhoMayDoWhatTest(FinalTestCase):
    def test_the_psychologist_and_the_isa_may_not_finalize_or_reopen(self):
        for user in (self.psy, self.isa):
            res = self.make_final(user=user)
            self.assertEqual(403, res.status_code, user)
            self.assertEqual(403, self.reopen(user=user).status_code, user)
        self.assertEqual(0, CaseStudyFinal.objects.count())
        self.assertEqual(CaseStudy.DRAFT, CaseStudy.objects.get(pk=self.study.pk).status)

    def test_nor_open_a_final_copy(self):
        self.make_final()
        final = CaseStudyFinal.objects.get()
        for user in (self.psy, self.isa):
            res = self.as_user(user).get(self.finals_url(final.pk))
            self.assertEqual(403, res.status_code, user)
            self.assertNotIn("Written text", json.dumps(res.data))

    def test_anybody_else_is_told_there_is_nothing_there(self):
        self.make_final()
        final = CaseStudyFinal.objects.get()
        stranger = make_user("psy3@t.ph", "Psychologist", "Pia", "Reyes")
        for user in (self.sw2, self.psy2, stranger):
            self.assertEqual(404, self.make_final(user=user).status_code, user)
            self.assertEqual(404, self.reopen(user=user).status_code, user)
            self.assertEqual(404, self.as_user(user).get(self.finals_url(final.pk)).status_code, user)
        self.assertEqual(CaseStudy.FINAL, CaseStudy.objects.get(pk=self.study.pk).status)

    def test_nobody_signed_out_gets_in(self):
        self.assertIn(self.as_user(None).post(self.final_url(), {}, format="json").status_code,
                      (401, 403))
        self.assertIn(self.as_user(None).post(self.reopen_url(), {}, format="json").status_code,
                      (401, 403))

    def test_an_unknown_child_is_not_found(self):
        res = self.as_user(self.sw).post("/api/case-studies/child/999999/final/", {}, format="json")
        self.assertEqual(404, res.status_code)

    def test_a_psychologist_asked_but_not_yet_assigned_is_nobody(self):
        pending = Child.objects.create(
            first_name="Ben", last_name="Reyes", gender="Male", birth_date=date(2018, 1, 5),
            case_type="Adoption", case_category="Surrendered", type_of_adoption="Regular",
            social_worker=self.sw)
        AssignmentRequest.objects.create(child=pending, psychologist=self.psy2, requested_by=self.sw)
        self.start(child=pending)
        for user in (self.psy2, self.psy):
            self.assertEqual(404, self.as_user(user).get(self.url(pending)).status_code)
            self.assertEqual(404, self.make_final(user=user, child=pending,
                                                  expected="2026-10-08T12:00:00+08:00").status_code)


class TheFinalCopyEndpointTest(FinalTestCase):
    def test_the_social_worker_gets_the_whole_copy(self):
        self.make_final()
        final = CaseStudyFinal.objects.get()
        res = self.as_user(self.sw).get(self.finals_url(final.pk))
        self.assertEqual(200, res.status_code)
        self.assertEqual({"id", "finalized_at", "finalized_by_name", "snapshot"}, set(res.data))
        self.assertEqual(final.pk, res.data["id"])
        self.assertEqual("Editha Pascua", res.data["finalized_by_name"])
        self.assertEqual(iso_datetime(final.finalized_at), res.data["finalized_at"])
        self.assertEqual(final.snapshot, res.data["snapshot"])
        self.assertEqual("Written text.", res.data["snapshot"]["sections"]["a2_circumstances"]["value"])

    def test_it_never_returns_another_childs_copy(self):
        other = Child.objects.create(
            first_name="Ben", last_name="Reyes", gender="Male", birth_date=date(2018, 1, 5),
            case_type="Adoption", case_category="Surrendered", type_of_adoption="Regular",
            social_worker=self.sw, assigned_psychologist=self.psy)
        other_study = self.start(child=other, date_prepared=PREPARED)
        for entry in SCSR_SECTIONS:
            if applies(entry, other, other_study):
                CaseStudySection.objects.create(
                    case_study=other_study, key=entry["key"], value=good_value(entry))
        self.assertEqual(200, self.make_final(child=other).status_code)
        self.assertEqual(200, self.make_final().status_code)
        theirs = CaseStudyFinal.objects.get(case_study=other_study)
        mine = CaseStudyFinal.objects.get(case_study=self.study)
        self.assertEqual(404, self.as_user(self.sw).get(self.finals_url(theirs.pk)).status_code)
        self.assertEqual(404, self.as_user(self.sw).get(self.finals_url(mine.pk, other)).status_code)
        self.assertEqual(200, self.as_user(self.sw).get(self.finals_url(mine.pk)).status_code)
        self.assertEqual(200, self.as_user(self.sw).get(self.finals_url(theirs.pk, other)).status_code)

    def test_an_unknown_copy_is_not_found(self):
        self.assertEqual(404, self.as_user(self.sw).get(self.finals_url(999999)).status_code)


class TheActivityFeedTest(FinalTestCase):
    def feed(self, user):
        return [e for e in self.as_user(user).get("/api/activity/").data
                if e["entity_type"] == "CaseStudy"]

    def test_final_is_addressed_to_the_assigned_psychologist(self):
        self.make_final()
        event = ActivityLog.objects.get(action=ActivityLog.FINALIZED)
        self.assertEqual("CaseStudy", event.entity_type)
        self.assertEqual(self.child.pk, event.entity_id)
        self.assertEqual("Ana Cruz", event.entity_label)
        self.assertEqual(self.sw, event.actor)
        self.assertEqual(self.psy, event.recipient)
        self.assertEqual(ActivityLog.RECORD, event.category)

    def test_reopen_is_addressed_to_nobody(self):
        self.make_final()
        self.reopen()
        event = ActivityLog.objects.get(action=ActivityLog.REOPENED)
        self.assertIsNone(event.recipient)
        self.assertEqual("Ana Cruz", event.entity_label)
        self.assertEqual(self.child.pk, event.entity_id)

    def test_the_social_worker_sees_both_and_so_does_the_assigned_psychologist_the_first(self):
        self.make_final()
        self.reopen()
        mine = self.feed(self.sw)
        self.assertEqual({"finalized", "reopened"}, {e["action"] for e in mine})
        self.assertEqual(["finalized"], [e["action"] for e in self.feed(self.psy)])
        self.assertEqual({"finalized", "reopened"}, {e["action"] for e in self.feed(self.isa)})
        self.assertEqual([], self.feed(self.sw2))
        self.assertEqual([], self.feed(self.psy2))

    def test_no_event_holds_any_of_the_text(self):
        self.make_final()
        self.reopen()
        for event in ActivityLog.objects.all():
            blob = json.dumps([event.entity_label, event.actor_label, event.entity_type, event.action])
            self.assertNotIn("Written text", blob)
            self.assertNotIn("One line", blob)
        for user in (self.sw, self.psy, self.isa):
            self.assertNotIn("Written text", json.dumps(self.feed(user)))

    def test_a_refused_final_writes_no_event(self):
        CaseStudy.objects.filter(pk=self.study.pk).update(date_prepared=None)
        self.make_final()
        self.assertEqual(0, ActivityLog.objects.filter(action=ActivityLog.FINALIZED).count())
        self.reopen()
        self.assertEqual(0, ActivityLog.objects.filter(action=ActivityLog.REOPENED).count())

    def test_a_pending_psychologist_is_told_nothing(self):
        pending = Child.objects.create(
            first_name="Ben", last_name="Reyes", gender="Male", birth_date=date(2018, 1, 5),
            case_type="Adoption", case_category="Surrendered", type_of_adoption="Regular",
            social_worker=self.sw)
        AssignmentRequest.objects.create(child=pending, psychologist=self.psy2, requested_by=self.sw)
        other = self.start(child=pending, date_prepared=PREPARED)
        for entry in SCSR_SECTIONS:
            if applies(entry, pending, other):
                CaseStudySection.objects.create(
                    case_study=other, key=entry["key"], value=good_value(entry))
        self.assertEqual(200, self.make_final(child=pending).status_code)
        event = ActivityLog.objects.get(action=ActivityLog.FINALIZED, entity_id=pending.pk)
        self.assertIsNone(event.recipient)
        self.assertEqual([], self.feed(self.psy2))
        self.assertEqual([], self.feed(self.psy))
        self.assertEqual(1, len(self.feed(self.sw)))

    def test_the_actions_are_ones_the_log_accepts(self):
        values = {value for value, _ in ActivityLog.ACTION_CHOICES}
        self.assertTrue({"finalized", "reopened"} <= values)
        for value in ("finalized", "reopened"):
            self.assertLessEqual(len(value), ActivityLog._meta.get_field("action").max_length)
