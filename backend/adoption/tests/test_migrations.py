"""The reference data arrives with the schema, not with a shell script.

`seed_psgc` and `backfill_psgc` run from backend/entrypoint.sh because Render's
Shell tab is paid-only and nothing may depend on somebody running a command by
hand. The adoption seeders need the same guarantee — but entrypoint.sh is a
file this project keeps closed, so they ride in on `migrate` instead, which
every deploy already runs.

That buys the same thing for free and takes one difference with it: a data
migration runs ONCE per database, where a line in entrypoint.sh runs on every
deploy. So a ninth stage added to stage_config.py later needs its own small
migration calling install_stages again. These tests are where that is written
down.
"""
import importlib
from pathlib import Path

from django.apps import apps as live_apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import migrations
from django.test import TestCase

from accounts.models import Role
from adoption.models import AdoptionStage, Handoff, RequirementTemplate
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()

STAGE_MIGRATION = "adoption.migrations.0003_seed_stages"
HANDOFF_MIGRATION = "adoption.migrations.0004_backfill_handoffs"


def _forward(module_path):
    """The RunPython forward callable out of a migration module."""
    module = importlib.import_module(module_path)
    op = module.Migration.operations[0]
    return op, module


class StagesArriveWithTheSchemaTests(TestCase):
    """No setUp, and deliberately no call to the seeder.

    Nothing in this class installs anything. If the eight stages are here, the
    only thing that could have put them here is `migrate` — which is precisely
    what a deploy runs and what this move is for.
    """

    def test_the_test_database_already_carries_the_eight_stages(self):
        self.assertEqual(8, AdoptionStage.objects.count())

    def test_the_test_database_already_carries_the_requirement_docket(self):
        # Count first: iterating an empty stage table would assert nothing.
        self.assertGreater(RequirementTemplate.objects.count(), 0)
        for stage in AdoptionStage.objects.all():
            self.assertTrue(
                RequirementTemplate.objects.filter(stage=stage).exists(),
                f"stage {stage.number} arrived with no exit conditions",
            )


class StageMigrationTests(TestCase):
    def setUp(self):
        self.op, self.module = _forward(STAGE_MIGRATION)

    def test_running_it_again_adds_nothing(self):
        self.op.code(live_apps, None)
        self.assertEqual(8, AdoptionStage.objects.count())

    def test_it_does_not_overwrite_a_locally_corrected_target(self):
        # Same promise the management command makes: an office that fixes a
        # target must not have it reverted by the next deploy.
        stage = AdoptionStage.objects.get(number=3)
        stage.target_days = 45
        stage.save()

        self.op.code(live_apps, None)

        stage.refresh_from_db()
        self.assertEqual(45, stage.target_days)

    def test_reversing_it_does_not_delete_the_stages(self):
        # A stage carries every case, event and requirement in the module on a
        # cascade. A reverse that deleted them would take the case data with
        # it, so the reverse has to be a no-op rather than a tidy-up.
        self.assertIs(migrations.RunPython.noop, self.op.reverse_code)

    def test_it_installs_exactly_what_the_management_command_installs(self):
        # One implementation, two callers. If these ever drift, a deploy and a
        # developer's machine disagree about the statutory process.
        def snapshot():
            return (
                sorted(AdoptionStage.objects.values_list(
                    "number", "name", "owner_role", "target_days")),
                sorted(RequirementTemplate.objects.values_list(
                    "stage__number", "code", "label", "owned_externally", "position")),
            )

        from_migration = snapshot()

        RequirementTemplate.objects.all().delete()
        AdoptionStage.objects.all().delete()
        call_command("seed_adoption_stages", verbosity=0)

        self.assertEqual(from_migration, snapshot())


class HandoffMigrationTests(TestCase):
    def setUp(self):
        self.op, self.module = _forward(HANDOFF_MIGRATION)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))

    def _completed_without_handoff(self, name):
        """An assessment completed the way one was before this app existed."""
        child = Child.objects.create(fullname=name, assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=child, psychologist=self.psy)
        # bulk update -> no post_save, which is exactly the historical shape.
        PreAssessment.objects.filter(id=pa.id).update(status=PreAssessment.COMPLETED)
        return child, pa

    def test_it_releases_a_child_completed_before_the_module_existed(self):
        child, pa = self._completed_without_handoff("Ana Lopez")

        self.op.code(live_apps, None)

        handoff = Handoff.objects.get(child=child)
        self.assertEqual(pa, handoff.pre_assessment)
        self.assertEqual(self.psy, handoff.released_by)

    def test_running_it_again_adds_nothing(self):
        self._completed_without_handoff("Ana Lopez")
        self.op.code(live_apps, None)
        self.op.code(live_apps, None)
        self.assertEqual(1, Handoff.objects.count())

    def test_reversing_it_does_not_delete_the_handoffs(self):
        self.assertIs(migrations.RunPython.noop, self.op.reverse_code)

    def test_the_status_values_it_froze_still_match_the_models(self):
        # A data migration must not import model constants — a historical model
        # has none. So it carries its own copies, and a rename would leave it
        # quietly matching nothing. This is the alarm for that.
        self.assertEqual(PreAssessment.COMPLETED, self.module.COMPLETED)
        self.assertEqual(Child.ACTIVE, self.module.ACTIVE)


class EntrypointStaysClosedTests(TestCase):
    """entrypoint.sh is off-limits by project instruction.

    The seeders had no business being added to it, and this is the guard that
    keeps them out — including from a future session that reads "PSGC runs from
    entrypoint" and reasonably concludes adoption should too.
    """

    def test_the_adoption_seeders_do_not_run_from_entrypoint(self):
        entrypoint = Path(__file__).resolve().parents[2] / "entrypoint.sh"
        text = entrypoint.read_text(encoding="utf-8")
        for command in ("seed_adoption_stages", "backfill_adoption_handoffs"):
            self.assertNotIn(
                command, text,
                f"{command} belongs in a data migration, not in entrypoint.sh",
            )
