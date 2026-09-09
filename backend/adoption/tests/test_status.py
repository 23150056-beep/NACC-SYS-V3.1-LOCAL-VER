"""Derived status - spec section 4.

"Every coloured chip on every screen comes from one computed value. Store
nothing; compute on read so a chip can never disagree with the data."

The order of the branches is the specification, and it is load-bearing: a case
that is BOTH on hold and overdue reads as on hold, because the hold is the
thing a reader has to act on first.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from adoption import pipeline, status as case_status
from adoption.models import AdoptionCase, AdoptionStage, ComplianceClock, Requirement
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()


class DerivedStatusTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.child = Child.objects.create(fullname="Ana Lopez", assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=self.child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        self.case = pipeline.admit(self.child, owner=self.staff, actor=self.staff)

    def _age_the_stage(self, days):
        AdoptionCase.objects.filter(id=self.case.id).update(
            stage_entered_at=timezone.now() - timedelta(days=days))
        self.case.refresh_from_db()

    def test_a_fresh_case_is_on_track(self):
        self.assertEqual(case_status.ON_TRACK, case_status.of(self.case))

    def test_days_in_stage_counts_from_when_the_stage_was_entered(self):
        self._age_the_stage(9)
        self.assertEqual(9, case_status.days_in_stage(self.case))

    def test_past_the_stage_target_is_overdue(self):
        self._age_the_stage(15)   # stage 1 targets 14 days
        self.assertEqual(case_status.OVERDUE, case_status.of(self.case))

    def test_three_quarters_through_the_target_is_at_risk(self):
        self._age_the_stage(12)   # 12 > 14 * 0.75
        self.assertEqual(case_status.AT_RISK, case_status.of(self.case))

    def test_a_clock_with_under_a_fortnight_left_is_at_risk(self):
        # Even on a stage that has barely started: the statutory window is the
        # deadline that actually matters.
        ComplianceClock.objects.create(
            case=self.case, code="publication", label="Publication period",
            started_at=timezone.localdate() - timedelta(days=52), duration_days=60)
        self.assertEqual(case_status.AT_RISK, case_status.of(self.case))

    def test_a_satisfied_clock_stops_counting(self):
        ComplianceClock.objects.create(
            case=self.case, code="publication", label="Publication period",
            started_at=timezone.localdate() - timedelta(days=52), duration_days=60,
            satisfied_at=timezone.localdate())
        self.assertEqual(case_status.ON_TRACK, case_status.of(self.case))

    def test_an_extension_pushes_the_clock_out_rather_than_resetting_it(self):
        ComplianceClock.objects.create(
            case=self.case, code="publication", label="Publication period",
            started_at=timezone.localdate() - timedelta(days=52), duration_days=60,
            extension_days=30)
        self.assertEqual(case_status.ON_TRACK, case_status.of(self.case))

    def test_waiting_when_the_only_blocker_belongs_to_somebody_else(self):
        # Blue, not amber: chasing our own staff will not move this one.
        self.case.requirements.filter(stage=self.case.current_stage).update(
            state=Requirement.VERIFIED)
        stage2 = AdoptionStage.objects.get(number=2)
        self.case.current_stage = stage2
        self.case.save()
        self.case.requirements.filter(stage=stage2, owned_externally=False).update(
            state=Requirement.VERIFIED)

        self.assertEqual(case_status.WAITING, case_status.of(self.case))

    def test_our_own_outstanding_work_is_not_waiting(self):
        stage2 = AdoptionStage.objects.get(number=2)
        self.case.current_stage = stage2
        self.case.save()
        # petition_filed is ours and still pending.
        self.assertEqual(case_status.ON_TRACK, case_status.of(self.case))

    def test_a_closed_case_is_complete(self):
        pipeline.close(self.case, actor=self.staff, reason=AdoptionCase.FINALIZED)
        self.case.refresh_from_db()
        self.assertEqual(case_status.COMPLETE, case_status.of(self.case))

    def test_a_hold_outranks_being_overdue(self):
        self._age_the_stage(40)
        self.case.on_hold = True
        self.case.save()
        self.assertEqual(case_status.ON_HOLD, case_status.of(self.case))

    def test_the_final_stage_can_never_be_overdue(self):
        # Stage 8 has no statutory window, so an old post-placement case must
        # not sit on the board glowing red for something nobody can fix.
        self.case.current_stage = AdoptionStage.objects.get(number=8)
        self.case.save()
        self._age_the_stage(400)
        self.assertEqual(case_status.ON_TRACK, case_status.of(self.case))


class ProgressTests(TestCase):
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

    def test_a_new_case_has_made_no_progress(self):
        self.assertEqual(0, case_status.progress_percent(self.case))

    def test_progress_measures_the_whole_journey_not_the_current_stage(self):
        # Finishing stage 1 of 8 must not read as 100%.
        self.case.requirements.filter(stage__number=1).update(state=Requirement.VERIFIED)
        percent = case_status.progress_percent(self.case)
        self.assertGreater(percent, 0)
        self.assertLess(percent, 50)

    def test_a_waived_requirement_counts_as_done(self):
        # Otherwise a waiver — which is a decision that the step is finished
        # with — would leave the bar permanently short of 100%.
        total = self.case.requirements.count()
        self.case.requirements.all().update(state=Requirement.WAIVED)
        self.assertEqual(100, case_status.progress_percent(self.case))
        self.assertEqual(total, self.case.requirements.count())

    def test_a_submitted_requirement_is_not_progress_yet(self):
        self.case.requirements.filter(stage__number=1).update(state=Requirement.SUBMITTED)
        self.assertEqual(0, case_status.progress_percent(self.case))
