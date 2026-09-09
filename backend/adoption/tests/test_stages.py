"""The eight stages and the requirement docket that hangs off them.

Stage names, owning roles and day targets are configuration, not code — the
spec is explicit that RACCO I's issuances change and a correction must not need
a deploy. These tests pin the SHAPE of that configuration and the seeder that
installs the current values; they do not pin the values as constants in the
application, which is the whole point.
"""
from django.test import TestCase

from adoption.models import AdoptionStage, RequirementTemplate


class StageSeederTests(TestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)

    def test_seeds_the_eight_stages_in_order(self):
        stages = list(AdoptionStage.objects.order_by("number"))
        self.assertEqual(8, len(stages))
        self.assertEqual([1, 2, 3, 4, 5, 6, 7, 8], [s.number for s in stages])

    def test_stage_carries_its_owning_role_and_day_target(self):
        cdclaa = AdoptionStage.objects.get(number=2)
        self.assertEqual("CDCLAA issuance", cdclaa.name)
        self.assertEqual("Legal unit", cdclaa.owner_role)
        self.assertEqual(60, cdclaa.target_days)

    def test_supervised_trial_custody_target_is_six_months(self):
        # The spec writes this one as "6 months" where the others are in days;
        # storing it as 180 keeps every comparison a single subtraction.
        self.assertEqual(180, AdoptionStage.objects.get(number=6).target_days)

    def test_final_stage_has_no_target(self):
        # Stage 8 is post-placement follow-through with no statutory clock, so
        # it can never be "overdue" — target_days is null, not zero.
        self.assertIsNone(AdoptionStage.objects.get(number=8).target_days)

    def test_every_stage_has_at_least_one_requirement_template(self):
        for stage in AdoptionStage.objects.all():
            self.assertTrue(
                RequirementTemplate.objects.filter(stage=stage).exists(),
                f"stage {stage.number} has no exit conditions, so it could never block",
            )

    def test_requirement_codes_are_unique_within_a_stage(self):
        for stage in AdoptionStage.objects.all():
            codes = list(RequirementTemplate.objects.filter(stage=stage)
                         .values_list("code", flat=True))
            self.assertEqual(len(codes), len(set(codes)))

    def test_seeder_is_idempotent(self):
        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)
        self.assertEqual(8, AdoptionStage.objects.count())

    def test_seeder_does_not_overwrite_a_locally_corrected_target(self):
        # The reason these are rows and not constants: an office that corrects
        # a target must not have it silently reverted by the next deploy.
        stage = AdoptionStage.objects.get(number=3)
        stage.target_days = 45
        stage.save()

        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)

        stage.refresh_from_db()
        self.assertEqual(45, stage.target_days)

    def test_requirements_owned_outside_the_office_are_marked(self):
        # "Waiting" (blue) in the derived status means the blocker is somebody
        # else's to clear — the court, the national office. That has to be a
        # property of the requirement, not a guess from its wording.
        cdclaa = RequirementTemplate.objects.get(stage__number=2, code="cdclaa_issued")
        self.assertTrue(cdclaa.owned_externally)

        endorsement = RequirementTemplate.objects.get(stage__number=1, code="owner_assigned")
        self.assertFalse(endorsement.owned_externally)
