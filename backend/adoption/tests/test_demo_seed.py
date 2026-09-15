"""The adoption demo seeder, held to the rules the screens enforce.

This command had no test and appeared in no runbook, no batch file and no
entrypoint - the only management command in the repo that nothing at all
referenced. That is precisely the state CLAUDE.md records costing three
separate repairs elsewhere: a rule and a seeder maintained apart drift, and
the suite does not notice, because seeded rows that the endpoint would refuse
still look like rows.

So what is asserted here is not that rows appear. Rows appear trivially. It is
that every case goes in through `pipeline.admit` - the real gate, which refuses
a child with no completed pre-assessment - and that the board it leaves behind
shows a SPREAD of statuses. The command's own docstring is the standard:
"If the board looks calm, nothing has been proven."
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import Role
from adoption import status as case_status
from adoption.models import AdoptionCase, ComplianceClock, PAP
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()


def _eligible_child(name, psychologist):
    """A child the pipeline will actually accept: assessment COMPLETED."""
    child = Child.objects.create(fullname=name, assigned_psychologist=psychologist)
    pa = PreAssessment.objects.create(child=child, psychologist=psychologist)
    pa.status = PreAssessment.COMPLETED
    pa.save()
    return child


class DemoSeedBase(TestCase):
    def setUp(self):
        call_command("seed_adoption_stages", verbosity=0)
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)


class TheGuardRunsFirstTest(DemoSeedBase):
    """DEBUG is False under the test runner, which is the production shape."""

    def test_it_refuses_when_debug_is_off(self):
        _eligible_child("Ana Lopez", self.psy)
        with self.assertRaises(CommandError):
            call_command("seed_adoption_demo", verbosity=0)
        self.assertEqual(0, AdoptionCase.objects.count(),
                         "the guard has to refuse before anything is written")

    @override_settings(DEBUG=True, DATABASES={
        "default": {"ENGINE": "django.db.backends.postgresql",
                    "HOST": "ep-quiet-salad-12345.ap-southeast-1.aws.neon.tech",
                    "NAME": "demo"}})
    def test_it_refuses_a_hosted_host(self):
        with self.assertRaises(CommandError):
            call_command("seed_adoption_demo", verbosity=0)


@override_settings(DEBUG=True)
class WhatItSeedsTest(DemoSeedBase):
    def test_it_stops_rather_than_inventing_a_way_in(self):
        """No child has a completed assessment, so there is nothing to admit.

        The failure this guards against is a seeder that reaches past the gate
        to fill a screen - which is how demo data stops being a sample of the
        system and becomes a second system sharing its database."""
        Child.objects.create(fullname="Not Assessed", assigned_psychologist=self.psy)
        call_command("seed_adoption_demo", verbosity=0)
        self.assertEqual(0, AdoptionCase.objects.count())

    def test_it_admits_only_children_the_real_gate_would_admit(self):
        eligible = [_eligible_child(f"Eligible {i}", self.psy) for i in range(4)]
        for i in range(3):
            Child.objects.create(fullname=f"Unassessed {i}",
                                 assigned_psychologist=self.psy)
        call_command("seed_adoption_demo", verbosity=0)

        admitted = set(AdoptionCase.objects.values_list("child_id", flat=True))
        self.assertTrue(admitted, "nothing was seeded at all")
        self.assertTrue(admitted <= {c.id for c in eligible},
                        "a child with no completed pre-assessment reached the "
                        "pipeline - pipeline.admit is the only door and it was "
                        "gone around")

    def test_it_seeds_the_families_and_matches_the_later_stages(self):
        for i in range(9):
            _eligible_child(f"Child {i}", self.psy)
        call_command("seed_adoption_demo", verbosity=0)

        self.assertEqual(4, PAP.objects.count())
        matched = AdoptionCase.objects.filter(pap__isnull=False)
        self.assertTrue(matched.exists(),
                        "no case reached a stage with a family attached, so the "
                        "match half of the board is untested")

    def test_the_board_it_leaves_is_not_all_green(self):
        """The whole value of the spread, asserted rather than assumed."""
        for i in range(9):
            _eligible_child(f"Child {i}", self.psy)
        call_command("seed_adoption_demo", verbosity=0)

        cases = list(AdoptionCase.objects.select_related("current_stage")
                     .prefetch_related("clocks", "requirements"))
        self.assertEqual(9, len(cases))
        states = {case_status.of(c) for c in cases}

        self.assertIn(case_status.OVERDUE, states,
                      "nothing on the seeded board has overrun its stage target, "
                      "so the state a person most needs to spot is not shown")
        self.assertGreaterEqual(
            len(states), 3,
            f"the board shows only {sorted(states)}. The spread is the point: "
            f"a tracker where everything reads the same proves nothing.")

    def test_it_blows_one_statutory_clock_and_leaves_another_running(self):
        for i in range(9):
            _eligible_child(f"Child {i}", self.psy)
        call_command("seed_adoption_demo", verbosity=0)

        clocks = list(ComplianceClock.objects.all())
        self.assertTrue(clocks, "no compliance clock was seeded")
        remaining = [case_status.clock_days_left(c) for c in clocks]
        self.assertTrue(any(d < 0 for d in remaining),
                        f"no clock has run out (days left: {remaining}) - the "
                        f"expired-window case is the one the module exists for")
        self.assertTrue(any(d >= 0 for d in remaining),
                        f"every clock has run out (days left: {remaining}) - a "
                        f"board where nothing is still running is not a spread")

    def test_running_it_twice_does_not_double_the_board(self):
        for i in range(9):
            _eligible_child(f"Child {i}", self.psy)
        call_command("seed_adoption_demo", verbosity=0)
        first = AdoptionCase.objects.count()
        call_command("seed_adoption_demo", verbosity=0)
        self.assertEqual(first, AdoptionCase.objects.count(),
                         "a second run admitted the same children again")
        self.assertEqual(4, PAP.objects.count(), "families were duplicated")
