"""The pre-assessment's one door: a table, a conditional completion, evidence
that stays put once completed, no deleting a completed one, and the seeder
holding to the same completion rule the endpoint enforces."""
import shutil
import tempfile
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from activity.models import ActivityLog
from children.models import Child
from clinical import care_gaps
from clinical.lifecycle import PRE_ASSESSMENT_LIFECYCLE, completion_problems
from clinical.models import ConsentRecord, InstrumentCatalog, PreAssessment
from clinical.tests.test_pre_assessment_progress import _Base
from locations.models import Barangay, Municipality, Province


class TableTest(TestCase):
    def test_the_table_matches_the_model(self):
        self.assertEqual(PRE_ASSESSMENT_LIFECYCLE.check_table(), [])

    def test_completed_is_terminal(self):
        t = PRE_ASSESSMENT_LIFECYCLE
        self.assertTrue(t.allows("in_progress", "completed"))
        self.assertTrue(t.allows("pending", "completed"))
        self.assertFalse(t.allows("completed", "in_progress"))
        self.assertFalse(t.allows("in_progress", "pending"))


class CompleteTest(_Base):
    def _ready(self):
        tool = InstrumentCatalog.objects.create(title="CBCL", owner=self.psy)
        pa = self._pa(status="in_progress", consent=self._consent())
        pa.instruments.add(tool)
        return pa

    def _count(self):
        return ActivityLog.objects.filter(entity_type="PreAssessment").count()

    def test_it_completes_once(self):
        pa = self._ready()
        self._auth()
        resp = self.client.post(f"/api/pre-assessments/{pa.id}/complete/")
        self.assertEqual(resp.status_code, 200)
        pa.refresh_from_db()
        self.assertEqual(pa.status, "completed")
        self.assertIsNotNone(pa.completed_at)

    def test_a_lost_race_is_409_and_writes_no_log_line(self):
        pa = self._ready()
        self._auth()
        before = self._count()
        real = PRE_ASSESSMENT_LIFECYCLE.move

        def another_tab_first(instance, to, **kw):
            PreAssessment.objects.filter(pk=pa.pk).update(status="completed")
            return real(instance, to, **kw)

        with patch.object(PRE_ASSESSMENT_LIFECYCLE, "move", another_tab_first):
            resp = self.client.post(f"/api/pre-assessments/{pa.id}/complete/")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["detail"],
                         "This pre-assessment was completed a moment ago. Reload to see it.")
        self.assertEqual(self._count(), before)

    def test_a_consent_unsigned_between_the_check_and_the_move_is_not_completed(self):
        pa = self._ready()
        self._auth()
        before = self._count()
        real = PRE_ASSESSMENT_LIFECYCLE.move

        def unsign_then_move(instance, to, **kw):
            ConsentRecord.objects.filter(pk=pa.consent_id).update(status="pending")
            return real(instance, to, **kw)

        with patch.object(PRE_ASSESSMENT_LIFECYCLE, "move", unsign_then_move):
            resp = self.client.post(f"/api/pre-assessments/{pa.id}/complete/")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("consent", resp.data)
        pa.refresh_from_db()
        self.assertEqual(pa.status, "in_progress")
        self.assertIsNone(pa.completed_at)
        self.assertEqual(self._count(), before)


class CompletedKeepsItsEvidenceTest(_Base):
    def setUp(self):
        super().setUp()
        self.tool = InstrumentCatalog.objects.create(title="CBCL", owner=self.psy)
        self.other_tool = InstrumentCatalog.objects.create(title="SDQ", owner=self.psy)
        self.consent = self._consent()
        self.pa = self._pa(status="completed", consent=self.consent, notes="old")
        self.pa.instruments.add(self.tool)
        self._auth()

    def _patch(self, body):
        return self.client.patch(f"/api/pre-assessments/{self.pa.id}/", body, format="json")

    def test_consent_cannot_change(self):
        resp = self._patch({"consent": self._consent().id})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Start a re-assessment", str(resp.data["consent"]))
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.consent_id, self.consent.id)

    def test_instruments_cannot_change(self):
        resp = self._patch({"instruments": [self.other_tool.id]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual([i.id for i in self.pa.instruments.all()], [self.tool.id])

    def test_resending_the_same_evidence_and_editing_notes_still_work(self):
        resp = self._patch({"consent": self.consent.id, "instruments": [self.tool.id],
                            "notes": "new"})
        self.assertEqual(resp.status_code, 200)
        self.pa.refresh_from_db()
        self.assertEqual(self.pa.notes, "new")


class DeleteTest(_Base):
    def setUp(self):
        super().setUp()
        self.consent = self._consent()
        self._auth()

    def test_a_completed_pre_assessment_cannot_be_deleted(self):
        pa = self._pa(status="completed", consent=self.consent)
        resp = self.client.delete(f"/api/pre-assessments/{pa.id}/")
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(PreAssessment.objects.filter(pk=pa.pk).exists())

    def test_an_open_one_can_be_deleted(self):
        pa = self._pa(status="in_progress")
        self.assertEqual(self.client.delete(f"/api/pre-assessments/{pa.id}/").status_code, 204)

    def test_its_consent_cannot_be_deleted_or_unsigned(self):
        self._pa(status="completed", consent=self.consent)
        resp = self.client.delete(f"/api/consents/{self.consent.id}/")
        self.assertEqual(resp.status_code, 409)
        resp = self.client.patch(f"/api/consents/{self.consent.id}/",
                                 {"status": "pending"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.consent.refresh_from_db()
        self.assertEqual(self.consent.status, "signed")

    def test_other_consents_behave_as_before(self):
        self._pa(status="in_progress", consent=self.consent)
        resp = self.client.patch(f"/api/consents/{self.consent.id}/",
                                 {"status": "pending"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/consents/{self.consent.id}/").status_code, 204)


class AbandonedRowGapTest(_Base):
    def _gaps(self):
        alerts = care_gaps.compute_alerts(Child.objects.filter(pk=self.child.pk))
        return {a["type"] for a in alerts}

    def test_an_open_row_older_than_a_completion_is_not_open(self):
        # No signed consent on file, but the child's pre-assessment is done
        # and the old open row was abandoned.
        self._pa(0, status="in_progress")
        self._pa(10, status="completed")
        self.assertNotIn("consent_missing", self._gaps())

    def test_a_live_open_row_still_raises_it(self):
        self._pa(0, status="completed")
        self._pa(10, status="in_progress")
        self.assertIn("consent_missing", self._gaps())


MEDIA = tempfile.mkdtemp()


@override_settings(DEBUG=True, MEDIA_ROOT=MEDIA)
class SeederHoldsToTheRuleTest(TestCase):
    def tearDown(self):
        shutil.rmtree(MEDIA, ignore_errors=True)

    def test_every_seeded_completed_row_would_be_accepted_by_complete(self):
        province = Province.objects.create(psgc_code="012800000", name="Ilocos Norte")
        town = Municipality.objects.create(psgc_code="012812000", name="Laoag City",
                                           province=province)
        Barangay.objects.create(psgc_code="012812001", name="Barangay 1", municipality=town)
        call_command("seed_demo_data", children=6, stdout=StringIO())
        done = list(PreAssessment.objects.filter(status="completed"))
        self.assertTrue(done)
        for pa in done:
            self.assertEqual(completion_problems(pa), [], pa.id)
            self.assertEqual(pa.current_step, "review")
