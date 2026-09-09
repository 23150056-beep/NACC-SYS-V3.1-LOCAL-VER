"""The handoff, and the gate that stands in front of the whole module.

Spec §1. A child reaches the adoption pipeline by exactly one route: the
psychologist marks the assessment complete, which returns the record to staff,
and a human then admits it. Both halves matter — the automatic half means a
finished assessment can never be quietly forgotten, and the manual half means
nobody is enrolled in a statutory process by a database trigger.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Role
from adoption.models import AdoptionCase, Handoff, Requirement, StageEvent
from adoption import pipeline
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()


def seed_stages():
    from django.core.management import call_command
    call_command("seed_adoption_stages", verbosity=0)


class HandoffTriggerTests(TestCase):
    def setUp(self):
        seed_stages()
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.child = Child.objects.create(
            fullname="Ana Lopez", case_type="Adoption", assigned_psychologist=self.psy)

    def test_completing_an_assessment_releases_the_child_to_staff(self):
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()

        handoff = Handoff.objects.get(child=self.child)
        self.assertEqual(self.psy, handoff.released_by)
        self.assertEqual(pa, handoff.pre_assessment)

    def test_an_unfinished_assessment_releases_nothing(self):
        PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status=PreAssessment.IN_PROGRESS)
        self.assertFalse(Handoff.objects.filter(child=self.child).exists())

    def test_saving_a_completed_assessment_again_does_not_duplicate_the_handoff(self):
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        pa.notes = "typo fixed"
        pa.save()

        self.assertEqual(1, Handoff.objects.filter(child=self.child).count())

    def test_a_handoff_is_pending_until_somebody_admits_it(self):
        # "The child is pending admission, not yet in the pipeline — no stage,
        # no clock." The banner is built from exactly this query.
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()

        self.assertIn(self.child.id,
                      [h.child_id for h in pipeline.pending_handoffs()])
        self.assertFalse(AdoptionCase.objects.filter(child=self.child).exists())


class AdmissionTests(TestCase):
    def setUp(self):
        seed_stages()
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.child = Child.objects.create(
            fullname="Ana Lopez", case_type="Adoption", assigned_psychologist=self.psy)

    def _complete_assessment(self):
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        return pa

    def test_admitting_opens_the_case_at_stage_one(self):
        self._complete_assessment()
        case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)

        self.assertEqual(1, case.current_stage.number)
        self.assertEqual(self.staff, case.owner)
        self.assertIsNone(case.closed_at)

    def test_admitting_seeds_the_whole_docket_not_just_stage_one(self):
        # The child view shows "every requirement across all stages", and the
        # progress bar measures the whole journey. Both need the rows to exist
        # on day one.
        self._complete_assessment()
        case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)

        from adoption.models import RequirementTemplate
        self.assertEqual(RequirementTemplate.objects.count(),
                         Requirement.objects.filter(case=case).count())
        self.assertTrue(
            all(r.state == Requirement.PENDING for r in Requirement.objects.filter(case=case)))

    def test_admitting_records_the_opening_stage_event(self):
        self._complete_assessment()
        case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)

        event = StageEvent.objects.get(case=case)
        self.assertIsNone(event.from_stage)
        self.assertEqual(1, event.to_stage.number)
        self.assertEqual(StageEvent.ADVANCE, event.direction)

    def test_admitting_clears_the_handoff_from_the_banner(self):
        self._complete_assessment()
        pipeline.admit(self.child, owner=self.staff, actor=self.staff)
        self.assertEqual([], list(pipeline.pending_handoffs()))

    def test_a_child_without_a_completed_assessment_cannot_be_admitted(self):
        # The single hard gate. "No manual override, not even for
        # administrators" — so it lives here, not in a permission class.
        PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status=PreAssessment.IN_PROGRESS)

        with self.assertRaises(pipeline.NotAdmissible) as ctx:
            pipeline.admit(self.child, owner=self.staff, actor=self.staff)
        self.assertIn("assessment", str(ctx.exception).lower())

    def test_an_archived_child_cannot_be_admitted(self):
        self._complete_assessment()
        self.child.status = Child.INACTIVE
        self.child.save()

        with self.assertRaises(pipeline.NotAdmissible):
            pipeline.admit(self.child, owner=self.staff, actor=self.staff)

    def test_a_child_cannot_be_admitted_twice(self):
        self._complete_assessment()
        pipeline.admit(self.child, owner=self.staff, actor=self.staff)

        with self.assertRaises(pipeline.NotAdmissible):
            pipeline.admit(self.child, owner=self.staff, actor=self.staff)

    def test_admitting_confirms_the_case_plan_as_adoption(self):
        # Condition 2: staff confirms the plan. Admitting IS that confirmation,
        # so the child's case type follows — a case in the adoption pipeline
        # whose record says Foster Care is a record that lies.
        self.child.case_type = "Foster Care"
        self.child.save()
        self._complete_assessment()

        pipeline.admit(self.child, owner=self.staff, actor=self.staff)
        self.child.refresh_from_db()
        self.assertEqual("Adoption", self.child.case_type)


class AssessmentReopenedTests(TestCase):
    def setUp(self):
        seed_stages()
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.child = Child.objects.create(
            fullname="Ana Lopez", case_type="Adoption", assigned_psychologist=self.psy)
        self.pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        self.pa.status = PreAssessment.COMPLETED
        self.pa.save()
        self.case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)

    def test_reopening_the_assessment_freezes_the_case_rather_than_deleting_it(self):
        self.pa.status = PreAssessment.IN_PROGRESS
        self.pa.save()

        self.case.refresh_from_db()
        self.assertTrue(self.case.on_hold)
        self.assertEqual(1, self.case.current_stage.number)
        self.assertIsNone(self.case.closed_at)

    def test_completing_it_again_lifts_the_hold(self):
        self.pa.status = PreAssessment.IN_PROGRESS
        self.pa.save()
        self.pa.status = PreAssessment.COMPLETED
        self.pa.save()

        self.case.refresh_from_db()
        self.assertFalse(self.case.on_hold)
