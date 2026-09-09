"""Needs attention, and the KPI counts - spec sections 5.2 and 5.8.

"Not a notification feed - a ranked worklist, max five items, ordered by
severity then age." The cap is the design: a list of forty things needing
attention is a list nobody reads, and this module's whole value is that
somebody acts on the top of it before a statutory window closes.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from adoption import pipeline, worklist
from adoption.models import AdoptionCase, AdoptionStage, ComplianceClock, PAP, Requirement
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()


class WorklistTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))

    def _case(self, name, stage=1, age_days=0):
        child = Child.objects.create(fullname=name, assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        case = pipeline.admit(child, owner=self.staff, actor=self.staff)
        AdoptionCase.objects.filter(id=case.id).update(
            current_stage=AdoptionStage.objects.get(number=stage),
            stage_entered_at=timezone.now() - timedelta(days=age_days))
        case.refresh_from_db()
        return case

    def test_an_office_with_nothing_overdue_gets_an_empty_list(self):
        self._case("Calm Case")
        self.assertEqual([], worklist.needs_attention())

    def test_a_blown_clock_is_reported(self):
        case = self._case("Ana Lopez", stage=2)
        ComplianceClock.objects.create(
            case=case, code="publication", label="Publication period",
            started_at=timezone.localdate() - timedelta(days=90), duration_days=60)

        items = worklist.needs_attention()
        self.assertEqual(1, len(items))
        self.assertEqual("clock_expired", items[0]["kind"])
        self.assertEqual(case.id, items[0]["case_id"])

    def test_a_stage_over_its_target_is_reported(self):
        self._case("Ben Cruz", stage=1, age_days=30)
        kinds = [i["kind"] for i in worklist.needs_attention()]
        self.assertIn("stage_over_target", kinds)

    def test_a_blown_clock_outranks_a_late_stage(self):
        # Severity first: a missed statutory window is not the same kind of
        # problem as a slow month.
        self._case("Slow Case", stage=1, age_days=30)
        late = self._case("Legal Case", stage=2)
        ComplianceClock.objects.create(
            case=late, code="publication", label="Publication period",
            started_at=timezone.localdate() - timedelta(days=90), duration_days=60)

        items = worklist.needs_attention()
        self.assertEqual("clock_expired", items[0]["kind"])

    def test_trial_custody_beyond_the_window_is_reported(self):
        self._case("Trial Case", stage=6, age_days=200)
        kinds = [i["kind"] for i in worklist.needs_attention()]
        self.assertIn("trial_custody_exceeded", kinds)

    def test_expiring_family_credentials_are_reported(self):
        case = self._case("Matched Case", stage=5)
        case.pap = PAP.objects.create(
            family_name="Ramos", status=PAP.MATCHED,
            cea_expiry=timezone.localdate() + timedelta(days=20))
        case.save()

        kinds = [i["kind"] for i in worklist.needs_attention()]
        self.assertIn("credentials_expiring", kinds)

    def test_the_list_is_capped_at_five(self):
        for i in range(9):
            self._case(f"Late Case {i}", stage=1, age_days=30)
        self.assertEqual(5, len(worklist.needs_attention()))

    def test_a_case_on_hold_is_not_nagged_about(self):
        # It is frozen on purpose. Ranking it as overdue would drown the list
        # in problems nobody is allowed to fix yet.
        case = self._case("Frozen Case", stage=1, age_days=30)
        case.on_hold = True
        case.save()
        self.assertEqual([], worklist.needs_attention())

    def test_a_closed_case_is_not_nagged_about(self):
        case = self._case("Done Case", stage=1, age_days=30)
        pipeline.close(case, actor=self.staff, reason=AdoptionCase.REUNIFIED)
        self.assertEqual([], worklist.needs_attention())

    def test_every_item_points_at_the_child_it_is_about(self):
        case = self._case("Ana Lopez", stage=1, age_days=30)
        item = worklist.needs_attention()[0]
        self.assertEqual(case.child_id, item["child_id"])
        self.assertEqual("Ana Lopez", item["child_name"])


class KpiTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))

    def _case(self, name, stage=1, age_days=0):
        child = Child.objects.create(fullname=name, assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=child, psychologist=self.psy)
        pa.status = PreAssessment.COMPLETED
        pa.save()
        case = pipeline.admit(child, owner=self.staff, actor=self.staff)
        AdoptionCase.objects.filter(id=case.id).update(
            current_stage=AdoptionStage.objects.get(number=stage),
            stage_entered_at=timezone.now() - timedelta(days=age_days))
        case.refresh_from_db()
        return case

    def test_counts_open_cases(self):
        self._case("A")
        self._case("B", stage=4)
        self.assertEqual(2, worklist.kpis()["open_cases"])

    def test_a_closed_case_is_not_open(self):
        case = self._case("A")
        pipeline.close(case, actor=self.staff, reason=AdoptionCase.AGED_OUT)
        self.assertEqual(0, worklist.kpis()["open_cases"])

    def test_counts_cases_awaiting_cdclaa(self):
        self._case("A", stage=2)
        self._case("B", stage=2)
        self._case("C", stage=3)
        self.assertEqual(2, worklist.kpis()["at_cdclaa"])

    def test_counts_trial_custody_and_how_many_overran(self):
        self._case("A", stage=6, age_days=10)
        self._case("B", stage=6, age_days=200)
        k = worklist.kpis()
        self.assertEqual(2, k["in_trial_custody"])
        self.assertEqual(1, k["trial_custody_overrun"])

    def test_counts_finalizations_this_calendar_year(self):
        case = self._case("A")
        pipeline.close(case, actor=self.staff, reason=AdoptionCase.FINALIZED)
        other = self._case("B")
        pipeline.close(other, actor=self.staff, reason=AdoptionCase.REUNIFIED)

        self.assertEqual(1, worklist.kpis()["finalized_this_year"])
