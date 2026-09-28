"""A psychologist closes a case for a clinical reason the record bears out.

Owner's request, 28 Sep 2026: the psychologist's dropdown is about the
counseling - done, never needed, a good pre-assessment - not about where the
child went, which stays the ISA's list. children/termination.py holds the
rule; the dialog reads it from /closure-reasons/ and the terminate endpoint
refuses by it, so the offer and the refusal are computed once.
"""
import re
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from children import termination as t
from children.models import Child, TerminationRecord
from clinical.models import PreAssessment
from scheduling.models import Appointment

User = get_user_model()

CASE_DATA = (Path(__file__).resolve().parents[3]
             / "frontend" / "src" / "config" / "caseData.js")


class ClosureBase(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        self.admin = User.objects.create_user(
            email="a@t.ph", username="a", password="x", role=roles[Role.ADMINISTRATOR])
        self.psy = User.objects.create_user(
            email="p@t.ph", username="p", password="x", role=roles[Role.PSYCHOLOGIST])
        self.staff = User.objects.create_user(
            email="s@t.ph", username="s", password="x", role=roles[Role.STAFF])
        self.child = Child.objects.create(fullname="Ana Cruz", assigned_psychologist=self.psy,
                                          social_worker=self.staff)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _offer(self, user=None):
        res = self._as(user or self.psy).get(f"/api/children/{self.child.id}/closure-reasons/")
        self.assertEqual(200, res.status_code, res.data)
        return res.data

    def _open(self, user=None):
        return {r["value"] for r in self._offer(user)["reasons"] if r["available"]}

    def _close(self, reason, user=None):
        return self._as(user or self.psy).post(
            f"/api/children/{self.child.id}/terminate/",
            {"reason_category": reason, "note": "Closing summary."}, format="json")

    def _pre_assessment(self, status=PreAssessment.COMPLETED):
        return PreAssessment.objects.create(child=self.child, psychologist=self.psy, status=status)

    def _session(self, status=Appointment.COMPLETED, purpose=Appointment.SESSION, days=-3):
        return Appointment.objects.create(
            child=self.child, psychologist=self.psy, purpose=purpose, status=status,
            start=timezone.now() + timedelta(days=days))


class WhatThePsychologistIsOfferedTest(ClosureBase):
    def test_a_psychologist_chooses_from_the_clinical_list_only(self):
        values = [r["value"] for r in self._offer()["reasons"]]
        self.assertEqual(TerminationRecord.CLINICAL, values)
        self.assertNotIn("Reunified with family", values)

    def test_the_isa_keeps_the_case_outcome_list(self):
        data = self._offer(self.admin)
        self.assertEqual(TerminationRecord.CASE_OUTCOMES, [r["value"] for r in data["reasons"]])
        self.assertTrue(all(r["available"] for r in data["reasons"]))
        self.assertIsNone(data["facts"])

    def test_with_nothing_recorded_only_the_reasons_that_need_nothing_are_open(self):
        self.assertEqual({t.REFERRED, t.OTHER}, self._open())

    def test_a_completed_pre_assessment_and_no_counseling_opens_the_pre_assessment_reasons(self):
        self._pre_assessment()
        self.assertEqual({t.FAVORABLE_PRE_ASSESSMENT, t.PRE_ASSESSMENT_ONLY, t.REFERRED, t.OTHER},
                         self._open())

    def test_an_unfinished_pre_assessment_does_not(self):
        self._pre_assessment(status=PreAssessment.IN_PROGRESS)
        offer = {r["value"]: r for r in self._offer()["reasons"]}
        self.assertFalse(offer[t.FAVORABLE_PRE_ASSESSMENT]["available"])
        self.assertIn("not completed", offer[t.FAVORABLE_PRE_ASSESSMENT]["why"])

    def test_a_completed_session_opens_counseling_completed_and_closes_the_pre_assessment_ones(self):
        self._pre_assessment()
        self._session()
        self.assertEqual({t.COUNSELING_COMPLETED, t.DISCONTINUED, t.REFERRED, t.OTHER},
                         self._open())

    def test_a_follow_up_counts_as_counseling_but_a_pre_assessment_appointment_does_not(self):
        self._session(purpose=Appointment.PRE_ASSESSMENT)
        self.assertNotIn(t.COUNSELING_COMPLETED, self._open())
        self._session(purpose=Appointment.FOLLOW_UP)
        self.assertIn(t.COUNSELING_COMPLETED, self._open())

    def test_a_session_only_booked_is_counseling_started_not_completed(self):
        self._session(status=Appointment.SCHEDULED, days=3)
        opened = self._open()
        self.assertIn(t.DISCONTINUED, opened)
        self.assertNotIn(t.COUNSELING_COMPLETED, opened)

    def test_a_cancelled_session_is_not_counseling_started(self):
        self._session(status=Appointment.CANCELLED)
        self.assertNotIn(t.DISCONTINUED, self._open())

    def test_moving_the_case_to_counseling_closes_the_pre_assessment_reasons(self):
        self._pre_assessment()
        self.child.case_status = Child.STAGE_COUNSELING
        self.child.save()
        offer = {r["value"]: r for r in self._offer()["reasons"]}
        self.assertFalse(offer[t.PRE_ASSESSMENT_ONLY]["available"])
        self.assertIn("Counseling has started", offer[t.PRE_ASSESSMENT_ONLY]["why"])
        self.assertTrue(offer[t.DISCONTINUED]["available"])
        self.assertFalse(offer[t.COUNSELING_COMPLETED]["available"],
                         "the stage alone is not a session held")

    def test_the_facts_come_with_the_offer(self):
        self._pre_assessment()
        self._session()
        self._session()
        self.assertEqual({"pre_assessment_completed": True, "in_counseling": False,
                          "sessions_held": 2, "counseling_started": True},
                         self._offer()["facts"])

    def test_only_those_who_may_close_the_case_are_offered_reasons(self):
        self.assertEqual(403, self._as(self.staff).get(
            f"/api/children/{self.child.id}/closure-reasons/").status_code)
        other = User.objects.create_user(email="o@t.ph", username="o", password="x",
                                         role=self.psy.role)
        self.assertEqual(404, self._as(other).get(
            f"/api/children/{self.child.id}/closure-reasons/").status_code)


class TheEndpointRefusesByTheSameRuleTest(ClosureBase):
    def test_counseling_completed_is_refused_without_a_session_held(self):
        res = self._close(t.COUNSELING_COMPLETED)
        self.assertEqual(400, res.status_code)
        self.assertIn("No counseling session", res.data["reason_category"])
        self.assertFalse(TerminationRecord.objects.exists())

    def test_and_accepted_with_one(self):
        self._session()
        res = self._close(t.COUNSELING_COMPLETED)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(t.COUNSELING_COMPLETED, TerminationRecord.objects.get().reason_category)

    def test_a_favorable_pre_assessment_closes_a_case_with_no_counseling(self):
        self._pre_assessment()
        self.assertEqual(200, self._close(t.FAVORABLE_PRE_ASSESSMENT).status_code)

    def test_a_psychologist_cannot_use_the_isas_case_outcomes(self):
        res = self._close("Reunified with family")
        self.assertEqual(400, res.status_code)
        self.assertFalse(TerminationRecord.objects.exists())

    def test_the_isa_cannot_use_the_clinical_reasons(self):
        self._session()
        self.assertEqual(400, self._close(t.COUNSELING_COMPLETED, user=self.admin).status_code)
        self.assertEqual(200, self._close("Reunified with family", user=self.admin).status_code)

    def test_every_offer_marked_open_is_accepted(self):
        """The dialog and the endpoint must agree: nothing offered is refused."""
        self._pre_assessment()
        for reason in self._open():
            res = self._close(reason)
            self.assertEqual(200, res.status_code, (reason, res.data))
            self._as(self.admin).post(f"/api/children/{self.child.id}/reopen/")
            self.child.refresh_from_db()
            self.child.assigned_psychologist = self.psy
            self.child.save()


class TheBrowserListsAgreeTest(SimpleTestCase):
    """caseData.js keeps both lists for the Records filter; they must be the
    server's, in the server's order."""

    def _js(self, name):
        match = re.search(r"export\s+const\s+" + re.escape(name) + r"\s*=\s*\[(.*?)\];",
                          CASE_DATA.read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(match, name)
        return re.findall(r"'([^']*)'", match.group(1))

    def test_case_outcomes(self):
        self.assertEqual(TerminationRecord.CASE_OUTCOMES, self._js("TERMINATION_REASONS"))

    def test_clinical_reasons(self):
        self.assertEqual(TerminationRecord.CLINICAL, self._js("CLINICAL_CLOSURE_REASONS"))
