"""Removing a duplicate record made by mistake (found 9 Oct 2026).

A double click on Save Record left C-0049 and C-0050 for one child, each with
its own request to the psychologist, and no way to delete either: children are
never deleted through the API. `POST /api/children/<id>/remove-duplicate/` is
the one exception, for the ISA only, and only for a record that holds nothing
but what Add Record itself makes (children/duplicates.py).
"""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage
from django.db import transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from activity.models import ActivityLog
from assistant.models import AssistantJob
from case_study.models import CaseStudy
from children import duplicates
from children.models import AssignmentRequest, Child, TerminationRecord
from clinical.models import (AgencyFormTemplate, CaseReferral, ClinicalInterviewRecord,
                             ConsentRecord, OpinionnaireInvite, PreAssessment, ProblemEntry,
                             PsychologicalReport, RemarkNote, ResultEntry, SelfReportFlag,
                             TreatmentPlan)
from scheduling.models import Appointment

User = get_user_model()
BORN = "2016-01-10"
# Fixed, never the wall clock.
NOON = timezone.make_aware(datetime(2026, 10, 12, 12, 0))


class RemoveDuplicateBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        cls.sw = make("sw@t.ph", "Editha", "Pascua", Role.STAFF)
        cls.other_sw = make("sw2@t.ph", "Rosa", "Santos", Role.STAFF)
        cls.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        cls.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        cls.psy2 = make("psy2@t.ph", "Jose", "Rizal", Role.PSYCHOLOGIST)
        child = lambda **over: Child.objects.create(  # noqa: E731
            **{"first_name": "Libai", "last_name": "Cramm", "birth_date": BORN,
               "case_type": "Adoption", "case_category": "Without Known Parents",
               "social_worker": cls.sw, **over})
        cls.original = child()
        cls.duplicate = child()
        AssignmentRequest.objects.create(child=cls.duplicate, psychologist=cls.psy,
                                         requested_by=cls.sw)
        cls.dup_ref = f"C-{cls.duplicate.pk:04d}"
        cls.orig_ref = f"C-{cls.original.pk:04d}"

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _remove(self, child=None, of=None, ref=None, user=None, **body):
        child = child or self.duplicate
        payload = {"duplicate_of": (of or self.original).pk, "case_reference": ref or
                   f"C-{child.pk:04d}", **body}
        return self._as(user or self.admin).post(
            f"/api/children/{child.pk}/remove-duplicate/", payload, format="json")

    def _refused(self, res, *needles):
        self.assertEqual(400, res.status_code, res.data)
        self.assertEqual(["detail"], list(res.data))
        for needle in needles:
            self.assertIn(needle, res.data["detail"])
        self.assertTrue(Child.objects.filter(pk=self.duplicate.pk).exists())
        return res.data["detail"]

    @contextmanager
    def rolled_back(self):
        """Whatever the block does is undone, so one test can try several
        cases against the same records."""
        sid = transaction.savepoint()
        try:
            yield
        finally:
            transaction.savepoint_rollback(sid)

    def _file_for(self, child):
        ref = CaseReferral(child=child, uploaded_by=self.sw, original_filename="referral.pdf")
        ref.file.save("referral.pdf", ContentFile(b"%PDF-1.4"), save=True)
        return ref


class WhoMayRemoveTest(RemoveDuplicateBase):
    def test_only_the_isa(self):
        for user in (self.sw, self.other_sw, self.psy):
            res = self._remove(user=user)
            self.assertEqual(403, res.status_code, user.email)
            self.assertEqual("Only the ISA (Administrator) can remove a duplicate record.",
                             res.data["detail"])
        self.assertTrue(Child.objects.filter(pk=self.duplicate.pk).exists())

    def test_the_assigned_psychologist_and_the_holding_sw_are_still_refused(self):
        Child.objects.filter(pk=self.duplicate.pk).update(assigned_psychologist=self.psy)
        self.assertEqual(403, self._remove(user=self.psy).status_code)
        self.assertEqual(403, self._remove(user=self.sw).status_code)

    def test_someone_else_is_told_no_whether_or_not_the_id_exists(self):
        """403 before any lookup, so the answer says nothing about which ids
        there are."""
        res = self._as(self.sw).post("/api/children/99999/remove-duplicate/",
                                     {"duplicate_of": self.original.pk}, format="json")
        self.assertEqual(403, res.status_code)

    def test_not_signed_in(self):
        res = APIClient().post(f"/api/children/{self.duplicate.pk}/remove-duplicate/",
                               {}, format="json")
        self.assertEqual(401, res.status_code)

    def test_the_isa_gets_a_404_for_a_record_that_is_not_there(self):
        res = self._as(self.admin).post("/api/children/99999/remove-duplicate/",
                                        {"duplicate_of": self.original.pk}, format="json")
        self.assertEqual(404, res.status_code)

    def test_an_id_that_is_not_a_number_is_a_404_not_an_error(self):
        res = self._as(self.admin).post("/api/children/abc/remove-duplicate/",
                                        {"duplicate_of": self.original.pk}, format="json")
        self.assertEqual(404, res.status_code)

    def test_children_are_still_not_deletable_the_ordinary_way(self):
        res = self._as(self.admin).delete(f"/api/children/{self.duplicate.pk}/")
        self.assertEqual(405, res.status_code)


class ItGoesTest(RemoveDuplicateBase):
    def test_the_record_goes_and_the_other_stays(self):
        res = self._remove()
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual({"removed": self.dup_ref, "duplicate_of": self.orig_ref}, res.data)
        self.assertFalse(Child.objects.filter(pk=self.duplicate.pk).exists())
        self.assertTrue(Child.objects.filter(pk=self.original.pk).exists())

    def test_its_case_referral_and_the_stored_file_go_with_it(self):
        mine, theirs = self._file_for(self.duplicate), self._file_for(self.original)
        mine_name, theirs_name = mine.file.name, theirs.file.name
        self.assertTrue(default_storage.exists(mine_name))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(200, self._remove().status_code)
        self.assertFalse(CaseReferral.objects.filter(pk=mine.pk).exists())
        self.assertFalse(default_storage.exists(mine_name))
        # The other record's referral is untouched.
        self.assertTrue(CaseReferral.objects.filter(pk=theirs.pk).exists())
        self.assertTrue(default_storage.exists(theirs_name))

    def test_nothing_is_deleted_from_storage_before_the_commit(self):
        mine = self._file_for(self.duplicate)
        with self.captureOnCommitCallbacks(execute=False):
            self.assertEqual(200, self._remove().status_code)
        self.assertTrue(default_storage.exists(mine.file.name))
        default_storage.delete(mine.file.name)

    def test_a_file_that_will_not_delete_does_not_undo_the_removal(self):
        mine = self._file_for(self.duplicate)
        # The log is read after the callbacks have run, so it wraps them.
        with patch.object(FileSystemStorage, "delete", side_effect=OSError("locked")), \
                self.assertLogs("children.duplicates", level="ERROR"), \
                self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(200, self._remove().status_code)
        self.assertFalse(Child.objects.filter(pk=self.duplicate.pk).exists())
        default_storage.delete(mine.file.name)

    def test_the_open_request_goes_and_the_psychologist_is_told_without_a_name(self):
        before = ActivityLog.objects.count()
        self.assertEqual(200, self._remove().status_code)
        self.assertFalse(AssignmentRequest.objects.filter(child_id=self.duplicate.pk).exists())
        told = ActivityLog.objects.get(recipient=self.psy)
        self.assertEqual((ActivityLog.WITHDRAWN, ActivityLog.RECORD, "Assignment", self.admin),
                         (told.action, told.category, told.entity_type, told.actor))
        self.assertEqual(f"record {self.dup_ref}, which was a duplicate", told.entity_label)
        self.assertIsNone(told.entity_id)
        for part in ("Libai", "Cramm"):
            self.assertNotIn(part, told.entity_label)
        # And exactly one more besides: the audit line.
        self.assertEqual(before + 2, ActivityLog.objects.count())

    def test_the_audit_trail_says_what_was_removed_and_what_it_repeated(self):
        self.assertEqual(200, self._remove().status_code)
        line = ActivityLog.objects.get(action=ActivityLog.REMOVED)
        self.assertEqual((ActivityLog.RECORD, "Child", self.admin, None),
                         (line.category, line.entity_type, line.actor, line.entity_id))
        self.assertEqual(f"{self.dup_ref} (duplicate of {self.orig_ref})", line.entity_label)
        self.assertEqual(self.sw, line.recipient)       # the SW who held it is told

    def test_the_isa_sees_it_in_the_feed_and_the_sw_it_came_from_too(self):
        self.assertEqual(200, self._remove().status_code)
        for user in (self.admin, self.sw):
            feed = self._as(user).get("/api/activity/").data
            self.assertIn(f"{self.dup_ref} (duplicate of {self.orig_ref})",
                          [e["entity_label"] for e in feed], user.email)
        # Another worker's feed has neither.
        other = [e["entity_label"] for e in self._as(self.other_sw).get("/api/activity/").data]
        self.assertNotIn(f"{self.dup_ref} (duplicate of {self.orig_ref})", other)

    def test_the_audit_line_is_not_addressed_to_the_isa_who_did_it(self):
        Child.objects.filter(pk=self.duplicate.pk).update(social_worker=self.admin)
        self.assertEqual(200, self._remove().status_code)
        self.assertIsNone(ActivityLog.objects.get(action=ActivityLog.REMOVED).recipient)

    def test_a_record_with_no_social_worker_and_no_request_goes_too(self):
        AssignmentRequest.objects.filter(child=self.duplicate).delete()
        Child.objects.filter(pk=self.duplicate.pk).update(social_worker=None)
        self.assertEqual(200, self._remove().status_code)
        self.assertEqual(1, ActivityLog.objects.filter(action=ActivityLog.REMOVED).count())
        self.assertFalse(ActivityLog.objects.filter(action=ActivityLog.WITHDRAWN).exists())

    def test_a_withdrawn_request_goes_quietly(self):
        AssignmentRequest.objects.filter(child=self.duplicate).update(
            status=AssignmentRequest.WITHDRAWN)
        self.assertEqual(200, self._remove().status_code)
        self.assertFalse(AssignmentRequest.objects.filter(child_id=self.duplicate.pk).exists())
        self.assertFalse(ActivityLog.objects.filter(recipient=self.psy).exists())

    def test_the_case_number_may_be_typed_any_of_these_ways(self):
        n = self.duplicate.pk
        for typed in (f"c-{n:04d}", f"  C-{n:04d}  ", f"C-{n}", f"C{n}"):
            with self.subTest(typed), self.rolled_back():
                res = self._remove(ref=typed)
                self.assertEqual(200, res.status_code, res.data)
                self.assertFalse(Child.objects.filter(pk=n).exists())


class ItIsRefusedTest(RemoveDuplicateBase):
    def test_which_record_it_repeats_has_to_be_given(self):
        for body in ({"duplicate_of": None}, {"duplicate_of": ""}, {"duplicate_of": "abc"}):
            res = self._as(self.admin).post(
                f"/api/children/{self.duplicate.pk}/remove-duplicate/",
                {"case_reference": self.dup_ref, **body}, format="json")
            self._refused(res, "Choose which record this one duplicates.")
        res = self._as(self.admin).post(
            f"/api/children/{self.duplicate.pk}/remove-duplicate/",
            {"case_reference": self.dup_ref}, format="json")
        self._refused(res, "Choose which record this one duplicates.")

    def test_a_record_is_not_a_duplicate_of_itself(self):
        self._refused(self._remove(of=self.duplicate), "cannot be a duplicate of itself")

    def test_the_record_it_repeats_has_to_exist(self):
        res = self._as(self.admin).post(
            f"/api/children/{self.duplicate.pk}/remove-duplicate/",
            {"duplicate_of": 99999, "case_reference": self.dup_ref}, format="json")
        self._refused(res, "was not found")

    def test_the_two_have_to_be_the_same_child(self):
        for over in ({"birth_date": "2015-06-30"}, {"first_name": "Bea"}, {"last_name": "Dizon"}):
            other = Child.objects.create(**{
                "first_name": "Libai", "last_name": "Cramm", "birth_date": BORN,
                "social_worker": self.sw, **over})
            self._refused(self._remove(of=other),
                          "are not for the same child", self.dup_ref, f"C-{other.pk:04d}")
        self.assertEqual(1, AssignmentRequest.objects.filter(child=self.duplicate).count())

    def test_names_match_without_regard_to_case_or_spacing(self):
        other = Child.objects.create(first_name=" LIBAI", last_name="cramm ", birth_date=BORN,
                                     social_worker=self.sw)
        self.assertEqual(200, self._remove(of=other).status_code)

    def test_its_own_case_number_has_to_be_typed(self):
        for typed in ("", "   ", "C-0001x", self.orig_ref, "the duplicate", f"C-{self.duplicate.pk + 1:04d}"):
            res = self._as(self.admin).post(
                f"/api/children/{self.duplicate.pk}/remove-duplicate/",
                {"duplicate_of": self.original.pk, "case_reference": typed}, format="json")
            self._refused(res, f"Type this record's case number, {self.dup_ref}, to confirm.")

    def test_no_case_number_at_all(self):
        res = self._as(self.admin).post(
            f"/api/children/{self.duplicate.pk}/remove-duplicate/",
            {"duplicate_of": self.original.pk}, format="json")
        self._refused(res, "to confirm")

    def test_a_refusal_changes_nothing(self):
        mine = self._file_for(self.duplicate)
        before = ActivityLog.objects.count()
        Appointment.objects.create(child=self.duplicate, psychologist=self.psy, start=NOON)
        with self.captureOnCommitCallbacks(execute=True):
            self._refused(self._remove(), "1 appointment")
        self.assertTrue(default_storage.exists(mine.file.name))
        self.assertTrue(CaseReferral.objects.filter(pk=mine.pk).exists())
        self.assertEqual(AssignmentRequest.PENDING,
                         AssignmentRequest.objects.get(child=self.duplicate).status)
        self.assertEqual(before, ActivityLog.objects.count())
        default_storage.delete(mine.file.name)

    def test_the_sentence_lists_everything_that_keeps_it(self):
        Appointment.objects.create(child=self.duplicate, psychologist=self.psy, start=NOON)
        Appointment.objects.create(child=self.duplicate, psychologist=self.psy,
                                   start=NOON.replace(hour=14))
        RemarkNote.objects.create(child=self.duplicate, author=self.psy, text="x")
        Child.objects.filter(pk=self.duplicate.pk).update(assigned_psychologist=self.psy)
        self.assertEqual(
            f"{self.dup_ref} cannot be removed: it already has 2 appointments, 1 remark and "
            "a psychologist assigned, and only a record holding nothing but what Add Record "
            "makes can be removed.",
            self._refused(self._remove()))


# One row of each kind that keeps a record. Built the cheapest way that
# satisfies the model; what matters is that it points at the child.
def _invite(test, child):
    template = AgencyFormTemplate.objects.create(
        form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="Self-report")
    return OpinionnaireInvite.objects.create(child=child, template=template, expires_at=NOON)


KEEPERS = {
    "scheduling.Appointment": lambda t, c: Appointment.objects.create(
        child=c, psychologist=t.psy, start=NOON),
    "children.TerminationRecord": lambda t, c: TerminationRecord.objects.create(
        child=c, reason_category="Other", note="x"),
    "clinical.ConsentRecord": lambda t, c: ConsentRecord.objects.create(child=c),
    "clinical.ClinicalInterviewRecord": lambda t, c: ClinicalInterviewRecord.objects.create(child=c),
    "clinical.PreAssessment": lambda t, c: PreAssessment.objects.create(
        child=c, psychologist=t.psy),
    "clinical.PsychologicalReport": lambda t, c: PsychologicalReport.objects.create(
        child=c, author=t.psy, file="reports/x.pdf"),
    "clinical.RemarkNote": lambda t, c: RemarkNote.objects.create(child=c, author=t.psy, text="x"),
    "clinical.TreatmentPlan": lambda t, c: TreatmentPlan.objects.create(
        child=c, author=t.psy, objectives="x"),
    "clinical.ResultEntry": lambda t, c: ResultEntry.objects.create(child=c, summary="x"),
    "clinical.OpinionnaireInvite": lambda t, c: _invite(t, c),
    "clinical.ProblemEntry": lambda t, c: ProblemEntry.objects.create(child=c, description="x"),
    "clinical.SelfReportFlag": lambda t, c: SelfReportFlag.objects.create(
        invite=_invite(t, c), child=c, question="q", answer="a", source=SelfReportFlag.LEXICON),
    "case_study.CaseStudy": lambda t, c: CaseStudy.objects.create(child=c),
    "assistant.AssistantJob": lambda t, c: AssistantJob.objects.create(
        job_type="brief", child=c),
    "children.AssignmentRequest": lambda t, c: AssignmentRequest.objects.create(
        child=c, psychologist=t.psy2, requested_by=t.sw, status=AssignmentRequest.DECLINED,
        reason="busy"),
}


class WhatKeepsARecordTest(RemoveDuplicateBase):
    def test_each_kind_of_attachment_refuses_the_removal(self):
        self.assertEqual(set(KEEPERS), set(duplicates.KEEPS_IT))
        for label, make in KEEPERS.items():
            with self.subTest(label), self.rolled_back():
                make(self, self.duplicate)
                one = duplicates.KEEPS_IT[label][0]
                self._refused(self._remove(), "it already has ", f"1 {one}")

    def test_an_assigned_psychologist_keeps_it(self):
        Child.objects.filter(pk=self.duplicate.pk).update(assigned_psychologist=self.psy)
        self._refused(self._remove(), "a psychologist assigned")

    def test_an_accepted_request_keeps_it(self):
        AssignmentRequest.objects.filter(child=self.duplicate).update(
            status=AssignmentRequest.ACCEPTED)
        self._refused(self._remove(), "1 answered assignment request")

    def test_a_declined_request_keeps_it(self):
        AssignmentRequest.objects.filter(child=self.duplicate).update(
            status=AssignmentRequest.DECLINED, reason="busy")
        self._refused(self._remove(), "1 answered assignment request")

    def test_a_model_nobody_has_named_keeps_it_rather_than_going_with_it(self):
        """Fail safe: a new relation to a child is a blocker until somebody
        decides it can go. Simulated by forgetting one."""
        RemarkNote.objects.create(child=self.duplicate, author=self.psy, text="x")
        forgotten = {k: v for k, v in duplicates.KEEPS_IT.items() if k != "clinical.RemarkNote"}
        with patch.object(duplicates, "KEEPS_IT", forgotten):
            self._refused(self._remove(), "1 remark note")


class EveryRelationToAChildIsConsideredTest(TestCase):
    def test_every_model_that_points_at_a_child_is_named(self):
        """Fails when a model with a foreign key to Child is added and nobody
        has said whether a duplicate record may be removed with it on. Add it
        to KEEPS_IT (it blocks the removal, with the words the refusal uses) or
        to TAKEN_ALONG (it is deleted with the record, and say why)."""
        related = {rel.related_model._meta.label for rel in Child._meta.related_objects}
        named = set(duplicates.KEEPS_IT) | set(duplicates.TAKEN_ALONG)
        self.assertEqual(
            set(), related - named,
            "A model points at Child and children/duplicates.py does not say what removing "
            "a duplicate record should do about it.")
        self.assertEqual(set(), named - related,
                         "children/duplicates.py names a model that no longer points at Child.")

    def test_only_the_assignment_request_is_in_both(self):
        """A request still waiting goes with the record; one answered keeps it."""
        self.assertEqual({"children.AssignmentRequest"},
                         set(duplicates.KEEPS_IT) & set(duplicates.TAKEN_ALONG))

    def test_what_goes_along_is_only_what_add_record_makes(self):
        self.assertEqual({"children.AssignmentRequest", "clinical.CaseReferral"},
                         set(duplicates.TAKEN_ALONG))
