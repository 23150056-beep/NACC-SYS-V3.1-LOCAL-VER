"""Pre-assessment write races, resume rule and the current_step backfill.

No test reads the wall clock: ordering uses explicit created_at stamps.
"""
from importlib import import_module
from unittest.mock import patch

from django.apps import apps

from clinical.models import (
    ClinicalInterviewRecord, InstrumentCatalog, PreAssessment,
)
from clinical.serializers import PreAssessmentSerializer
from clinical.tests.test_pre_assessment_progress import _Base


class CompletionIsNotUndoneTest(_Base):
    def _race(self, pa, payload):
        """Complete the row in the database AFTER the PATCH has loaded it."""
        real_validate = PreAssessmentSerializer.validate

        def validate_then_complete(serializer, attrs):
            out = real_validate(serializer, attrs)
            PreAssessment.objects.filter(pk=pa.pk).update(
                status="completed", completed_at=self.completed_at)
            return out

        self._auth()
        with patch.object(PreAssessmentSerializer, "validate", validate_then_complete):
            return self.client.patch(f"/api/pre-assessments/{pa.id}/", payload, format="json")

    def setUp(self):
        super().setUp()
        from datetime import datetime, timezone as dt
        self.completed_at = datetime(2026, 3, 3, 9, 0, tzinfo=dt.utc)

    def test_a_step_save_that_lost_the_race_leaves_it_completed(self):
        pa = self._pa(status="in_progress", current_step="review")
        resp = self._race(pa, {"current_step": "problems"})
        self.assertEqual(resp.status_code, 400)
        pa.refresh_from_db()
        self.assertEqual(pa.status, "completed")
        self.assertEqual(pa.completed_at, self.completed_at)
        self.assertEqual(pa.current_step, "review")

    def test_a_notes_save_that_lost_the_race_leaves_it_completed(self):
        pa = self._pa(status="in_progress", current_step="review", notes="old")
        resp = self._race(pa, {"notes": "late"})
        self.assertEqual(resp.status_code, 400)
        pa.refresh_from_db()
        self.assertEqual((pa.status, pa.notes), ("completed", "old"))

    def test_an_ordinary_patch_writes_only_what_it_sent(self):
        tool = InstrumentCatalog.objects.create(title="CBCL", owner=self.psy)
        pa = self._pa(status="in_progress", notes="keep me")
        self._auth()
        resp = self.client.patch(f"/api/pre-assessments/{pa.id}/", {
            "current_step": "instruments", "instruments": [tool.id]}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["instrument_titles"], ["CBCL"])
        pa.refresh_from_db()
        self.assertEqual((pa.current_step, pa.notes, pa.status),
                         ("instruments", "keep me", "in_progress"))


class ResumeTest(_Base):
    def _start(self):
        self._auth()
        return self.client.post("/api/pre-assessments/", {"child": self.child.id}, format="json")

    def test_an_own_open_row_older_than_a_completion_is_not_resumed(self):
        old = self._pa(0, status="in_progress")
        self._pa(10, status="completed")
        resp = self._start()
        self.assertEqual(resp.status_code, 201)
        self.assertNotEqual(resp.data["id"], old.id)

    def test_a_pending_own_row_newer_than_any_completion_is_resumed(self):
        self._pa(0, status="completed")
        newer = self._pa(10, status="pending")
        resp = self._start()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], newer.id)

    def test_the_newest_open_row_is_the_one_resumed(self):
        self._pa(0, status="in_progress")
        newest = self._pa(5, status="in_progress")
        self.assertEqual(self._start().data["id"], newest.id)


class BackfillCurrentStepTest(_Base):
    def _run(self):
        mod = import_module("clinical.migrations.0013_backfill_current_step")
        mod.backfill_current_step(apps, None)

    def _step(self, pa):
        return PreAssessment.objects.get(pk=pa.pk).current_step

    def test_steps_are_worked_out_from_the_row(self):
        tool = InstrumentCatalog.objects.create(title="CBCL", owner=self.psy)
        interview = ClinicalInterviewRecord.objects.create(
            child=self.child, interviewer=self.psy)
        none = self._pa(0, status="in_progress")
        consent_only = self._pa(1, status="in_progress", consent=self._consent())
        interviewed = self._pa(2, status="pending", consent=self._consent(), interview=interview)
        with_tools = self._pa(3, status="in_progress", consent=self._consent(), interview=interview)
        with_tools.instruments.add(tool)
        tools_no_interview = self._pa(4, status="in_progress", consent=self._consent())
        tools_no_interview.instruments.add(tool)
        done = self._pa(5, status="completed", consent=self._consent())
        past_consent = self._pa(6, status="in_progress", current_step="review",
                                consent=self._consent())
        self._run()
        self.assertEqual(self._step(none), "consent")
        self.assertEqual(self._step(consent_only), "interview")
        self.assertEqual(self._step(interviewed), "instruments")
        self.assertEqual(self._step(with_tools), "problems")
        self.assertEqual(self._step(tools_no_interview), "problems")
        self.assertEqual(self._step(done), "consent")
        self.assertEqual(self._step(past_consent), "review")
