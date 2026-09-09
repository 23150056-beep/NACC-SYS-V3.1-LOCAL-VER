"""Children released before this module existed.

The handoff row is written by a signal, so it only fires for assessments
completed after the adoption app was installed. Every child whose psychologist
signed off before that has no row — and the banner is the only thing telling
staff they are waiting. Without a backfill the module would launch blind to
its entire existing backlog.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounts.models import Role
from adoption import pipeline
from adoption.models import Handoff
from children.models import Child
from clinical.models import PreAssessment

User = get_user_model()


class BackfillTests(TestCase):
    def setUp(self):
        call_command("seed_adoption_stages", verbosity=0)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))

    def _completed_without_handoff(self, name):
        """An assessment completed the way one was before this app existed."""
        child = Child.objects.create(fullname=name, assigned_psychologist=self.psy)
        pa = PreAssessment.objects.create(child=child, psychologist=self.psy)
        # bulk update -> no post_save, which is exactly the historical shape.
        PreAssessment.objects.filter(id=pa.id).update(status=PreAssessment.COMPLETED)
        return child, pa

    def test_a_child_completed_before_the_module_existed_has_no_handoff(self):
        child, _ = self._completed_without_handoff("Ana Lopez")
        self.assertFalse(Handoff.objects.filter(child=child).exists())

    def test_the_backfill_releases_them(self):
        child, pa = self._completed_without_handoff("Ana Lopez")
        call_command("backfill_adoption_handoffs", verbosity=0)

        handoff = Handoff.objects.get(child=child)
        self.assertEqual(pa, handoff.pre_assessment)
        self.assertEqual(self.psy, handoff.released_by)

    def test_the_backfill_is_idempotent(self):
        self._completed_without_handoff("Ana Lopez")
        call_command("backfill_adoption_handoffs", verbosity=0)
        call_command("backfill_adoption_handoffs", verbosity=0)
        self.assertEqual(1, Handoff.objects.count())

    def test_it_does_not_release_an_unfinished_assessment(self):
        child = Child.objects.create(fullname="Ben Cruz", assigned_psychologist=self.psy)
        PreAssessment.objects.create(child=child, psychologist=self.psy,
                                     status=PreAssessment.IN_PROGRESS)
        call_command("backfill_adoption_handoffs", verbosity=0)
        self.assertFalse(Handoff.objects.filter(child=child).exists())

    def test_a_child_already_in_the_pipeline_is_not_put_back_on_the_banner(self):
        child, _ = self._completed_without_handoff("Cara Diaz")
        call_command("backfill_adoption_handoffs", verbosity=0)
        pipeline.admit(child, owner=self.staff, actor=self.staff)

        call_command("backfill_adoption_handoffs", verbosity=0)
        self.assertEqual([], [h.child_id for h in pipeline.pending_handoffs()])

    def test_an_archived_child_is_left_alone(self):
        child, _ = self._completed_without_handoff("Dan Reyes")
        child.status = Child.INACTIVE
        child.save()

        call_command("backfill_adoption_handoffs", verbosity=0)
        self.assertFalse(Handoff.objects.filter(child=child).exists())
