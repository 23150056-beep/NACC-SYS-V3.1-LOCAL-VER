"""Fill the adoption pipeline with plausible cases, for the local copy only.

The same guard as seed_demo_data, for the same reason: this invents children's
adoption records, and there is no version of "oops, on production" that is
recoverable. It refuses a hosted database and refuses DEBUG=False, and both
checks run before anything opens a write.

The spread is deliberate rather than random. A tracker screen is only worth
looking at if it shows the states a person has to tell apart — so this seeds
one case that is comfortably on track, one three-quarters through its target,
one over it, one with a blown statutory clock, and one matched family whose CEA
is about to expire. If the board looks calm, nothing has been proven.
"""
import random
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from accounts.models import Role
from adoption import pipeline
from adoption.models import AdoptionCase, AdoptionStage, ComplianceClock, PAP, Requirement
from children.models import Child
from clinical.models import PreAssessment

FAMILIES = [
    ("Ramos", "Agoo, La Union", "CEA-2025-0114", 1),
    ("Villamor", "San Fernando, La Union", "CEA-2025-0219", 0),
    ("Dela Cruz", "Bauang, La Union", "CEA-2024-0871", 2),
    ("Sison", "Naguilian, La Union", "CEA-2025-0433", 1),
]


class Command(BaseCommand):
    help = "Seed demo adoption cases (local development only)."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=9)

    def handle(self, *args, **options):
        # Both guards before any write. A hosted database is identified by its
        # engine, not by a setting somebody could have forgotten to flip.
        if not settings.DEBUG:
            raise CommandError("Refusing to run with DEBUG=False.")
        if "sqlite" not in connection.settings_dict["ENGINE"]:
            raise CommandError(
                "Refusing to run against anything but the local SQLite copy.")

        from django.core.management import call_command
        call_command("seed_adoption_stages", verbosity=0)

        rng = random.Random(20260909)
        today = timezone.localdate()

        for family, address, cea, kids in FAMILIES:
            PAP.objects.get_or_create(
                family_name=family,
                defaults={
                    "address": address, "region": "Region I", "cea_number": cea,
                    "cea_expiry": today + timedelta(days=rng.choice([40, 200, 320])),
                    "home_study_expiry": today + timedelta(days=rng.choice([90, 280])),
                    "children_in_household": kids,
                })

        # Children who already have a completed assessment are the only ones
        # eligible, which is the gate doing its job even in demo data.
        eligible = list(Child.objects.filter(
            status=Child.ACTIVE,
            pre_assessments__status=PreAssessment.COMPLETED,
        ).exclude(adoption_cases__isnull=False).distinct()[:options["count"]])

        if not eligible:
            self.stdout.write(self.style.WARNING(
                "No children with a completed pre-assessment. "
                "Run seed_demo_data first."))
            return

        owner = (Role.objects.filter(role_name=Role.STAFF).first()
                 and Role.objects.get(role_name=Role.STAFF).users.first())
        if owner is None:
            from django.contrib.auth import get_user_model
            owner = get_user_model().objects.filter(role__role_name=Role.STAFF).first()

        # (stage, how far through its target) — the spread the docstring
        # promises. The last two are the ones worth looking at.
        plan = [(1, 0.2), (2, 0.5), (3, 0.9), (4, 0.3), (5, 1.4), (6, 0.6), (7, 0.8),
                (2, 1.6), (4, 0.1)]
        made = 0

        for child, (stage_number, ratio) in zip(eligible, plan):
            case = pipeline.admit(child, owner=owner, actor=owner)
            stage = AdoptionStage.objects.get(number=stage_number)

            # Everything behind the current stage is done; the current stage is
            # part-done, which is what makes a next action and a blocker list.
            Requirement.objects.filter(
                case=case, stage__number__lt=stage_number).update(state=Requirement.VERIFIED)
            current = list(Requirement.objects.filter(case=case, stage=stage))
            for req in current[:max(0, len(current) - 2)]:
                Requirement.objects.filter(id=req.id).update(state=Requirement.VERIFIED)

            target = stage.target_days or 30
            AdoptionCase.objects.filter(id=case.id).update(
                current_stage=stage,
                stage_entered_at=timezone.now() - timedelta(days=int(target * ratio)))

            if stage_number >= 4:
                pap = PAP.objects.order_by("?").first()
                AdoptionCase.objects.filter(id=case.id).update(pap=pap)

            if stage_number == 2:
                # A publication period that is about to run out on one case and
                # has already run out on the other.
                overrun = ratio > 1
                ComplianceClock.objects.create(
                    case=case, code="publication", label="CDCLAA publication period",
                    started_at=today - timedelta(days=70 if overrun else 52),
                    duration_days=60)
            made += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {made} adoption cases and {PAP.objects.count()} families."))
