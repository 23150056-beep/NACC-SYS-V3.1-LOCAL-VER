"""The docket - submit, verify, waive. Spec sections 5.6 and 6.

This is a checklist staff run themselves, so every casework role can tick,
sign off and waive. One rule survives that opening-up, and it is the one that
cannot be expressed as a role at all:

* **the person who uploads a document may not be the person who verifies it.**

It lives in the service rather than in a permission class because "not your
own upload" depends on the ROW, not on who is asking. Two people are still two
people whether they are administrators or staff, and without this rule a
verified tick only means somebody clicked twice.

A waiver still has to say why. That is not a permission - it is the audit
trail for proceeding without a statutory document, and it is worth the same
whoever grants it.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Role
from adoption import docket, pipeline
from adoption.models import Requirement
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()


class DocketTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        staff_role = Role.objects.create(role_name=Role.STAFF)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)
        self.other_staff = User.objects.create_user(
            email="s2@racco1.gov.ph", username="s2", password="pass1234", role=staff_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR))

        self.child = Child.objects.create(fullname="Ana Lopez", assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        self.case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)
        self.req = self.case.requirements.get(stage__number=1, code="consent_on_file")

    def test_submitting_records_who_and_when(self):
        docket.submit(self.req, actor=self.staff)
        self.req.refresh_from_db()
        self.assertEqual(Requirement.SUBMITTED, self.req.state)
        self.assertEqual(self.staff, self.req.submitted_by)
        self.assertIsNotNone(self.req.submitted_at)

    def test_a_psychologist_cannot_touch_the_docket(self):
        # Spec section 6: psychologists read the timeline of children they
        # assessed and nothing else in this module.
        with self.assertRaises(docket.NotPermitted):
            docket.submit(self.req, actor=self.psy)

    def test_verifying_records_who_and_when(self):
        docket.submit(self.req, actor=self.staff)
        docket.verify(self.req, actor=self.admin)
        self.req.refresh_from_db()
        self.assertEqual(Requirement.VERIFIED, self.req.state)
        self.assertEqual(self.admin, self.req.verified_by)
        self.assertIsNotNone(self.req.verified_at)

    def test_nobody_may_verify_their_own_upload(self):
        # The separation of duty that makes verification mean anything.
        docket.submit(self.req, actor=self.admin)
        with self.assertRaises(docket.NotPermitted) as ctx:
            docket.verify(self.req, actor=self.admin)
        self.assertIn("own", str(ctx.exception).lower())

    def test_staff_verify_somebody_elses_upload(self):
        # The module is a checklist the office keeps for itself. Requiring an
        # administrator for every tick left staff unable to finish a stage.
        docket.submit(self.req, actor=self.other_staff)
        docket.verify(self.req, actor=self.staff)
        self.req.refresh_from_db()
        self.assertEqual(Requirement.VERIFIED, self.req.state)
        self.assertEqual(self.staff, self.req.verified_by)

    def test_a_staff_member_may_not_verify_their_own_upload_either(self):
        # The separation of duty is about the row, not the role, so opening
        # verification to staff must not open it to self-verification.
        docket.submit(self.req, actor=self.staff)
        with self.assertRaises(docket.NotPermitted) as ctx:
            docket.verify(self.req, actor=self.staff)
        self.assertIn("own", str(ctx.exception).lower())

    def test_nothing_can_be_verified_before_it_is_submitted(self):
        with self.assertRaises(docket.NotPermitted):
            docket.verify(self.req, actor=self.admin)

    def test_waiving_needs_a_reason(self):
        with self.assertRaises(docket.NotPermitted):
            docket.waive(self.req, actor=self.admin, reason="  ")

    def test_staff_can_waive_with_a_reason(self):
        docket.waive(self.req, actor=self.staff, reason="original lost in the 2019 fire")
        self.req.refresh_from_db()
        self.assertEqual(Requirement.WAIVED, self.req.state)
        self.assertEqual(self.staff, self.req.verified_by)

    def test_waiving_records_the_reason(self):
        docket.waive(self.req, actor=self.admin, reason="original lost in the 2019 fire")
        self.req.refresh_from_db()
        self.assertEqual(Requirement.WAIVED, self.req.state)
        self.assertEqual("original lost in the 2019 fire", self.req.waiver_reason)
        self.assertEqual(self.admin, self.req.verified_by)

    def test_a_waiver_can_be_granted_without_a_prior_submission(self):
        # That is what a waiver IS - the document is never going to arrive.
        self.assertEqual(Requirement.PENDING, self.req.state)
        docket.waive(self.req, actor=self.admin, reason="guardian deceased, no record exists")
        self.req.refresh_from_db()
        self.assertEqual(Requirement.WAIVED, self.req.state)

    def test_the_docket_cannot_be_edited_on_a_closed_case(self):
        from adoption.models import AdoptionCase
        pipeline.close(self.case, actor=self.admin, reason=AdoptionCase.AGED_OUT)
        self.req.refresh_from_db()
        with self.assertRaises(docket.NotPermitted):
            docket.submit(self.req, actor=self.staff)

    def test_next_action_is_the_earliest_due_outstanding_requirement(self):
        # The board card and the list row both show this; it is what turns a
        # status chip into something a person can act on.
        from datetime import date
        first = self.case.requirements.get(stage__number=1, code="owner_assigned")
        first.due_date = date(2026, 1, 5)
        first.save()
        self.req.due_date = date(2026, 3, 9)
        self.req.save()

        self.assertEqual(first.label, docket.next_action(self.case))

    def test_next_action_falls_back_to_docket_order_when_nothing_is_dated(self):
        self.assertEqual("Psychologist's report attached", docket.next_action(self.case))

    def test_a_case_with_nothing_outstanding_has_no_next_action(self):
        self.case.requirements.filter(stage__number=1).update(state=Requirement.VERIFIED)
        self.assertIsNone(docket.next_action(self.case))
