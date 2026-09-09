"""The stage machine — spec section 3.

The guard is the whole point of the module: "a tracker that lets you drag past
a statutory requirement is worse than no tracker". These tests are the guard.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from adoption import pipeline
from adoption.models import AdoptionCase, Requirement, StageEvent
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()


class StageMachineTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.child = Child.objects.create(
            fullname="Ana Lopez", case_type="Adoption", assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        self.case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)

    def _satisfy_current_stage(self, state=Requirement.VERIFIED):
        self.case.requirements.filter(stage=self.case.current_stage).update(state=state)

    def test_a_stage_will_not_advance_while_a_requirement_is_pending(self):
        with self.assertRaises(pipeline.StageBlocked):
            pipeline.advance(self.case, actor=self.staff)
        self.case.refresh_from_db()
        self.assertEqual(1, self.case.current_stage.number)

    def test_the_refusal_names_what_is_missing(self):
        # A refusal that does not say what is missing just sends somebody
        # hunting through a docket.
        with self.assertRaises(pipeline.StageBlocked) as ctx:
            pipeline.advance(self.case, actor=self.staff)
        codes = {r.code for r in ctx.exception.blockers}
        self.assertIn("owner_assigned", codes)

    def test_a_submitted_requirement_is_not_enough_to_advance(self):
        # Submitted means somebody uploaded it. Verified means somebody else
        # looked at it. Only the second lets a statutory stage close.
        self._satisfy_current_stage(state=Requirement.SUBMITTED)
        with self.assertRaises(pipeline.StageBlocked):
            pipeline.advance(self.case, actor=self.staff)

    def test_a_stage_advances_once_every_requirement_is_verified(self):
        self._satisfy_current_stage()
        pipeline.advance(self.case, actor=self.staff)
        self.case.refresh_from_db()
        self.assertEqual(2, self.case.current_stage.number)

    def test_a_waived_requirement_also_lets_the_stage_advance(self):
        self._satisfy_current_stage(state=Requirement.WAIVED)
        pipeline.advance(self.case, actor=self.staff)
        self.case.refresh_from_db()
        self.assertEqual(2, self.case.current_stage.number)

    def test_advancing_restarts_the_days_in_stage_count(self):
        old = self.case.stage_entered_at
        self._satisfy_current_stage()
        pipeline.advance(self.case, actor=self.staff)
        self.case.refresh_from_db()
        self.assertGreater(self.case.stage_entered_at, old)

    def test_advancing_appends_a_stage_event(self):
        self._satisfy_current_stage()
        pipeline.advance(self.case, actor=self.staff, note="endorsed")

        event = self.case.events.order_by("-id").first()
        self.assertEqual(1, event.from_stage.number)
        self.assertEqual(2, event.to_stage.number)
        self.assertEqual(StageEvent.ADVANCE, event.direction)
        self.assertEqual(self.staff, event.actor)

    def test_a_case_on_hold_cannot_advance(self):
        self._satisfy_current_stage()
        self.case.on_hold = True
        self.case.save()

        with self.assertRaises(pipeline.StageBlocked) as ctx:
            pipeline.advance(self.case, actor=self.staff)
        self.assertIn("hold", str(ctx.exception).lower())

    def test_a_closed_case_cannot_advance(self):
        pipeline.close(self.case, actor=self.staff, reason=AdoptionCase.AGED_OUT)
        with self.assertRaises(pipeline.StageBlocked):
            pipeline.advance(self.case, actor=self.staff)

    def test_the_final_stage_does_not_advance_into_a_ninth(self):
        for _ in range(7):
            self._satisfy_current_stage()
            pipeline.advance(self.case, actor=self.staff)
            self.case.refresh_from_db()
        self.assertEqual(8, self.case.current_stage.number)

        self._satisfy_current_stage()
        with self.assertRaises(pipeline.StageBlocked) as ctx:
            pipeline.advance(self.case, actor=self.staff)
        self.assertIn("final stage", str(ctx.exception).lower())


class RevertTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.child = Child.objects.create(fullname="Ben Cruz", assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        self.case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)
        self.case.requirements.filter(stage=self.case.current_stage).update(
            state=Requirement.VERIFIED)
        pipeline.advance(self.case, actor=self.staff)
        self.case.refresh_from_db()

    def test_reverting_steps_back_exactly_one_stage(self):
        pipeline.revert(self.case, actor=self.staff, note="filed in error")
        self.case.refresh_from_db()
        self.assertEqual(1, self.case.current_stage.number)

    def test_reverting_without_a_note_is_refused(self):
        # A correction with no explanation is indistinguishable from tampering
        # when somebody reads the history a year later.
        with self.assertRaises(pipeline.StageBlocked):
            pipeline.revert(self.case, actor=self.staff, note="   ")

    def test_reverting_is_logged_as_a_revert(self):
        pipeline.revert(self.case, actor=self.staff, note="filed in error")
        event = self.case.events.order_by("-id").first()
        self.assertEqual(StageEvent.REVERT, event.direction)
        self.assertEqual("filed in error", event.note)

    def test_the_first_stage_cannot_be_reverted_out_of(self):
        pipeline.revert(self.case, actor=self.staff, note="filed in error")
        self.case.refresh_from_db()
        with self.assertRaises(pipeline.StageBlocked):
            pipeline.revert(self.case, actor=self.staff, note="again")


class ClosureTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.child = Child.objects.create(fullname="Cara Diaz", assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        self.case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)

    def test_closing_records_the_reason_and_the_time(self):
        pipeline.close(self.case, actor=self.staff, reason=AdoptionCase.REUNIFIED,
                       note="grandmother located")
        self.case.refresh_from_db()
        self.assertIsNotNone(self.case.closed_at)
        self.assertEqual(AdoptionCase.REUNIFIED, self.case.closure_reason)

    def test_closing_leaves_the_child_record_active(self):
        # "Four honest exits, each closing the case with a reason and leaving
        # the child record active." A disrupted placement ends an adoption
        # case, not a child's care.
        pipeline.close(self.case, actor=self.staff, reason=AdoptionCase.DISRUPTED)
        self.child.refresh_from_db()
        self.assertEqual(Child.ACTIVE, self.child.status)

    def test_an_invented_closure_reason_is_refused(self):
        with self.assertRaises(pipeline.StageBlocked):
            pipeline.close(self.case, actor=self.staff, reason="gave up")

    def test_a_case_cannot_be_closed_twice(self):
        pipeline.close(self.case, actor=self.staff, reason=AdoptionCase.AGED_OUT)
        with self.assertRaises(pipeline.StageBlocked):
            pipeline.close(self.case, actor=self.staff, reason=AdoptionCase.AGED_OUT)

    def test_a_closed_case_frees_the_child_to_be_admitted_again(self):
        # Spec section 8: a disrupted placement returns the child to the
        # pipeline as a NEW case, with the disrupted one kept in history.
        pipeline.close(self.case, actor=self.staff, reason=AdoptionCase.DISRUPTED)
        fresh = pipeline.admit(self.child, owner=self.staff, actor=self.staff)

        self.assertNotEqual(self.case.id, fresh.id)
        self.assertEqual(2, AdoptionCase.objects.filter(child=self.child).count())
