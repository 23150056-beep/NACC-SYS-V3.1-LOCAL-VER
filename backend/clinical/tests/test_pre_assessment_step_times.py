"""The pre-assessment tracker: when each step was started, last edited and
done. django.utils.timezone.now is pinned throughout - never the wall clock."""
import importlib
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from clinical.models import (
    ClinicalInterviewRecord, ConsentRecord, InstrumentCatalog, PreAssessment,
    PreAssessmentStepTime)

User = get_user_model()
T0 = datetime(2026, 3, 2, 9, 0, tzinfo=dt_timezone.utc)
NOW = "django.utils.timezone.now"


def at(minutes):
    return T0 + timedelta(minutes=minutes)


class _Base(APITestCase):
    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.other = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)
        self.child = Child.objects.create(
            fullname="Ana Cruz", case_type="Foster Care",
            assigned_psychologist=self.psy, social_worker=self.sw)
        self._auth()

    def _auth(self, email="p@racco1.gov.ph"):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _consent(self, signed=True):
        return ConsentRecord.objects.create(
            child=self.child, status="signed" if signed else "pending",
            recorded_by=self.psy)

    def _start(self, minute=0):
        with patch(NOW, return_value=at(minute)):
            resp = self.client.post("/api/pre-assessments/", {"child": self.child.id},
                                    format="json")
        self.assertEqual(resp.status_code, 201)
        return resp.data["id"]

    def _patch(self, pid, minute, **body):
        with patch(NOW, return_value=at(minute)):
            resp = self.client.patch(f"/api/pre-assessments/{pid}/", body, format="json")
        return resp

    def _row(self, pid, step):
        return PreAssessmentStepTime.objects.filter(pre_assessment_id=pid, step=step).first()


class StampsTest(_Base):
    def test_create_stamps_consent_started_only(self):
        pid = self._start(0)
        self.assertEqual(self._row(pid, "consent").started_at, at(0))
        self.assertEqual(PreAssessmentStepTime.objects.filter(pre_assessment_id=pid).count(), 1)

    def test_resume_does_not_restamp(self):
        pid = self._start(0)
        with patch(NOW, return_value=at(30)):
            resp = self.client.post("/api/pre-assessments/", {"child": self.child.id},
                                    format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["id"], pid)
        self.assertEqual(self._row(pid, "consent").started_at, at(0))

    def test_moving_forward_starts_next_and_finishes_previous(self):
        pid = self._start(0)
        self._patch(pid, 5, current_step="interview")
        self.assertEqual(self._row(pid, "interview").started_at, at(5))
        self.assertEqual(self._row(pid, "consent").done_at, at(5))
        self.assertIsNone(self._row(pid, "interview").done_at)

    def test_moving_back_records_no_done_and_keeps_started(self):
        pid = self._start(0)
        self._patch(pid, 5, current_step="interview")
        self._patch(pid, 9, current_step="consent")
        self.assertIsNone(self._row(pid, "interview").done_at)
        self.assertEqual(self._row(pid, "interview").started_at, at(5))
        self.assertEqual(self._row(pid, "consent").started_at, at(0))
        self.assertEqual(self._row(pid, "consent").done_at, at(5))

    def test_moving_forward_again_overwrites_done(self):
        pid = self._start(0)
        self._patch(pid, 5, current_step="interview")
        self._patch(pid, 9, current_step="consent")
        self._patch(pid, 12, current_step="interview")
        self.assertEqual(self._row(pid, "consent").done_at, at(12))
        self.assertEqual(self._row(pid, "interview").started_at, at(5))

    def test_skipping_ahead_finishes_the_step_left(self):
        pid = self._start(0)
        self._patch(pid, 4, current_step="problems")
        self.assertEqual(self._row(pid, "consent").done_at, at(4))
        self.assertEqual(self._row(pid, "problems").started_at, at(4))

    def test_leaving_a_step_with_no_row_invents_no_start(self):
        from clinical import step_times
        pid = self._start(0)
        pa = PreAssessment.objects.get(pk=pid)
        step_times.moved_past(pa, "interview", "problems", at(7))
        row = self._row(pid, "interview")
        self.assertIsNone(row.started_at)
        self.assertEqual(row.done_at, at(7))
        shown = {e["step"]: e for e in step_times.entries(pa.step_times.all())}
        self.assertIsNone(shown["interview"]["seconds"])

    def test_same_step_again_stamps_nothing_new(self):
        pid = self._start(0)
        self._patch(pid, 5, current_step="consent")
        self.assertIsNone(self._row(pid, "consent").done_at)

    def test_linking_consent_stamps_consent_edited(self):
        pid = self._start(0)
        self._patch(pid, 3, consent=self._consent().id)
        row = self._row(pid, "consent")
        self.assertEqual(row.last_edited_at, at(3))
        self.assertEqual(row.started_at, at(0))

    def test_linking_interview_stamps_interview_edited(self):
        pid = self._start(0)
        iv = ClinicalInterviewRecord.objects.create(
            child=self.child, interviewer=self.psy, respondent="Guardian")
        self._patch(pid, 6, interview=iv.id)
        row = self._row(pid, "interview")
        self.assertEqual(row.last_edited_at, at(6))
        self.assertEqual(row.started_at, at(6))

    def test_instruments_patch_stamps_instruments_edited(self):
        pid = self._start(0)
        inst = InstrumentCatalog.objects.create(title="BASC")
        self._patch(pid, 7, instruments=[inst.id])
        self.assertEqual(self._row(pid, "instruments").last_edited_at, at(7))

    def test_resending_what_is_already_held_is_not_an_edit(self):
        pid = self._start(0)
        inst = InstrumentCatalog.objects.create(title="BASC")
        cid = self._consent().id
        self._patch(pid, 7, instruments=[inst.id], consent=cid, notes="")
        self._patch(pid, 9, instruments=[inst.id], consent=cid, notes="")
        self.assertEqual(self._row(pid, "instruments").last_edited_at, at(7))
        self.assertEqual(self._row(pid, "consent").last_edited_at, at(7))
        self.assertIsNone(self._row(pid, "review"))

    def test_notes_stamp_review_edited(self):
        pid = self._start(0)
        self._patch(pid, 8, notes="Observed well")
        self.assertEqual(self._row(pid, "review").last_edited_at, at(8))

    def test_one_patch_with_several_fields_stamps_each(self):
        pid = self._start(0)
        self._patch(pid, 9, notes="x", consent=self._consent().id)
        self.assertEqual(self._row(pid, "review").last_edited_at, at(9))
        self.assertEqual(self._row(pid, "consent").last_edited_at, at(9))

    def test_last_edited_moves_forward(self):
        pid = self._start(0)
        self._patch(pid, 8, notes="a")
        self._patch(pid, 11, notes="b")
        self.assertEqual(self._row(pid, "review").last_edited_at, at(11))


class StaleStepTest(_Base):
    def test_a_step_change_from_a_stale_read_is_a_409(self):
        pid = self._start(0)
        # Another tab already moved it on; this request still believes "consent".
        from clinical.views import PreAssessmentViewSet
        real = PreAssessment.objects.filter(pk=pid)
        self._patch(pid, 3, current_step="interview")
        stale = PreAssessment.objects.get(pk=pid)
        stale.current_step = "consent"
        with patch.object(PreAssessmentViewSet, "get_object", return_value=stale):
            resp = self._patch(pid, 5, current_step="problems")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data["detail"],
                         "This pre-assessment was moved to another step in another tab. "
                         "Reload to continue.")
        self.assertEqual(real.get().current_step, "interview")
        self.assertIsNone(self._row(pid, "problems"))

    def test_a_completed_row_is_still_a_400(self):
        pid = self._start(0)
        PreAssessment.objects.filter(pk=pid).update(status="completed", completed_at=at(3))
        self.assertEqual(self._patch(pid, 9, current_step="review").status_code, 400)


class ProblemsTest(_Base):
    def _problem(self, minute):
        with patch(NOW, return_value=at(minute)):
            return self.client.post("/api/problems/", {
                "child": self.child.id, "description": "Sleep disturbance",
                "category": "Sleep"}, format="json")

    def test_problem_at_problems_step_stamps_problems(self):
        pid = self._start(0)
        self._patch(pid, 2, current_step="problems")
        resp = self._problem(5)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._row(pid, "problems").last_edited_at, at(5))
        with patch(NOW, return_value=at(8)):
            self.client.patch(f"/api/problems/{resp.data['id']}/",
                              {"resolved": True}, format="json")
        self.assertEqual(self._row(pid, "problems").last_edited_at, at(8))
        with patch(NOW, return_value=at(10)):
            self.client.delete(f"/api/problems/{resp.data['id']}/")
        self.assertEqual(self._row(pid, "problems").last_edited_at, at(10))

    def test_not_stamped_at_another_step(self):
        pid = self._start(0)
        self._patch(pid, 2, current_step="instruments")
        self.assertEqual(self._problem(5).status_code, 201)
        self.assertIsNone(self._row(pid, "problems"))

    def test_not_stamped_once_completed(self):
        pid = self._start(0)
        PreAssessment.objects.filter(pk=pid).update(
            current_step="problems", status="completed", completed_at=at(3))
        self.assertEqual(self._problem(5).status_code, 201)
        self.assertIsNone(self._row(pid, "problems"))

    def test_not_stamped_for_another_psychologists_pre_assessment(self):
        pa = PreAssessment.objects.create(
            child=self.child, psychologist=self.other, status="in_progress",
            current_step="problems")
        self.assertEqual(self._problem(5).status_code, 201)
        self.assertIsNone(self._row(pa.id, "problems"))


class CompleteTest(_Base):
    def test_complete_stamps_review_done(self):
        pid = self._start(0)
        inst = InstrumentCatalog.objects.create(title="BASC")
        self._patch(pid, 2, consent=self._consent().id, instruments=[inst.id],
                    current_step="review")
        with patch(NOW, return_value=at(20)):
            resp = self.client.post(f"/api/pre-assessments/{pid}/complete/")
        self.assertEqual(resp.status_code, 200)
        review = self._row(pid, "review")
        self.assertEqual(review.done_at, at(20))
        self.assertEqual(review.started_at, at(2))
        shown = {e["step"]: e for e in resp.data["step_times"]}
        self.assertEqual(shown["review"]["done_at"], at(20).isoformat())
        self.assertEqual(shown["review"]["seconds"], 18 * 60)

    def test_complete_creates_a_missing_review_row(self):
        pid = self._start(0)
        inst = InstrumentCatalog.objects.create(title="BASC")
        pa = PreAssessment.objects.get(pk=pid)
        pa.consent = self._consent()
        pa.save()
        pa.instruments.set([inst])
        with patch(NOW, return_value=at(20)):
            self.client.post(f"/api/pre-assessments/{pid}/complete/")
        review = self._row(pid, "review")
        self.assertIsNone(review.started_at)
        self.assertEqual(review.done_at, at(20))

    def test_a_patch_on_a_completed_row_writes_no_stamps(self):
        pid = self._start(0)
        PreAssessment.objects.filter(pk=pid).update(status="completed", completed_at=at(3))
        before = list(PreAssessmentStepTime.objects.values_list("step", "started_at",
                                                                 "last_edited_at", "done_at"))
        self._patch(pid, 9, notes="late")  # notes still save; the tracker does not move
        self.assertEqual(self._patch(pid, 9, current_step="review").status_code, 400)
        after = list(PreAssessmentStepTime.objects.values_list("step", "started_at",
                                                                "last_edited_at", "done_at"))
        self.assertEqual(before, after)

    def test_a_racing_patch_after_completion_writes_no_stamps(self):
        """The row was read as open, then completed before the write."""
        from clinical import step_times
        pid = self._start(0)
        pa = PreAssessment.objects.get(pk=pid)
        PreAssessment.objects.filter(pk=pid).update(status="completed", completed_at=at(3))
        step_times.edited(pa, "review", at(9))
        step_times.entered(pa, "problems", at(9))
        step_times.moved_past(pa, "consent", "interview", at(9))
        self.assertIsNone(self._row(pid, "review"))
        self.assertIsNone(self._row(pid, "problems"))
        self.assertIsNone(self._row(pid, "consent").done_at)


class ApiShapeTest(_Base):
    def test_step_times_in_the_pre_assessment_api(self):
        pid = self._start(0)
        self._patch(pid, 65, current_step="interview")
        data = self.client.get(f"/api/pre-assessments/{pid}/").data
        steps = data["step_times"]
        self.assertEqual([s["step"] for s in steps],
                         ["consent", "interview", "instruments", "problems", "review"])
        self.assertEqual(steps[0]["label"], "Consent")
        self.assertEqual(steps[0]["seconds"], 65 * 60)
        self.assertEqual(steps[1]["started_at"], at(65).isoformat())
        self.assertIsNone(steps[1]["seconds"])
        self.assertEqual(steps[4], {"step": "review", "label": "Review", "started_at": None,
                                    "last_edited_at": None, "done_at": None, "seconds": None})

    def test_progress_steps_and_step_edited_at_on_the_child_api(self):
        pid = self._start(0)
        self._patch(pid, 5, current_step="interview")
        progress = self.client.get(f"/api/children/{self.child.id}/").data[
            "pre_assessment_progress"]
        self.assertEqual(progress["step"], "interview")
        self.assertEqual(progress["steps"][1]["started_at"], at(5).isoformat())
        self.assertEqual(progress["step_edited_at"], at(5).isoformat())
        self._patch(pid, 7, interview=ClinicalInterviewRecord.objects.create(
            child=self.child, interviewer=self.psy, respondent="G").id)
        progress = self.client.get(f"/api/children/{self.child.id}/").data[
            "pre_assessment_progress"]
        self.assertEqual(progress["step_edited_at"], at(7).isoformat())

    def test_not_started_gives_five_empty_entries(self):
        progress = self.client.get(f"/api/children/{self.child.id}/").data[
            "pre_assessment_progress"]
        self.assertEqual(progress["state"], "not_started")
        self.assertEqual(len(progress["steps"]), 5)
        self.assertTrue(all(s["started_at"] is None for s in progress["steps"]))
        self.assertIsNone(progress["step_edited_at"])

    def test_completed_shows_the_completed_ones_steps_and_no_edited_at(self):
        pid = self._start(0)
        PreAssessment.objects.filter(pk=pid).update(status="completed", completed_at=at(30))
        progress = self.client.get(f"/api/children/{self.child.id}/").data[
            "pre_assessment_progress"]
        self.assertEqual(progress["state"], "completed")
        self.assertEqual(progress["steps"][0]["started_at"], at(0).isoformat())
        self.assertIsNone(progress["step_edited_at"])

    def test_monitoring_and_child_report_carry_the_steps(self):
        pid = self._start(0)
        self._patch(pid, 5, current_step="interview")
        row = self.client.get("/api/reports/monitoring/").data[0]["pre_assessment_progress"]
        self.assertEqual(row["step_edited_at"], at(5).isoformat())
        self.assertEqual(len(row["steps"]), 5)
        report = self.client.get(f"/api/reports/child/{self.child.id}/").data
        self.assertEqual(report["child"]["pre_assessment_progress"]["steps"][0]["done_at"],
                         at(5).isoformat())
        self.assertEqual(report["pre_assessments"][0]["step_times"][0]["done_at"],
                         at(5).isoformat())

    def test_carry_history_hides_the_earlier_psychologists_steps(self):
        self.child.assignee_sees_history = False
        self.child.save()
        pa = PreAssessment.objects.create(
            child=self.child, psychologist=self.other, status="in_progress",
            current_step="problems", consent=self._consent())
        PreAssessmentStepTime.objects.create(
            pre_assessment=pa, step="consent", started_at=at(0), done_at=at(4))
        progress = self.client.get(f"/api/children/{self.child.id}/").data[
            "pre_assessment_progress"]
        self.assertEqual(progress["state"], "not_started")
        self.assertTrue(all(s["started_at"] is None for s in progress["steps"]))
        row = self.client.get("/api/reports/monitoring/").data[0]["pre_assessment_progress"]
        self.assertTrue(all(s["started_at"] is None for s in row["steps"]))
        report = self.client.get(f"/api/reports/child/{self.child.id}/").data
        self.assertEqual(report["pre_assessments"], [])
        self.assertTrue(all(s["started_at"] is None
                            for s in report["child"]["pre_assessment_progress"]["steps"]))


class MigrationBackfillTest(_Base):
    def test_backfill(self):
        mod = importlib.import_module("clinical.migrations.0015_backfill_step_times")
        open_pa = PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status="in_progress")
        done_pa = PreAssessment.objects.create(
            child=self.child, psychologist=self.psy, status="completed", completed_at=at(50))
        PreAssessmentStepTime.objects.all().delete()
        mod.backfill_step_times(apps, None)
        self.assertEqual(self._row(open_pa.id, "consent").started_at, open_pa.created_at)
        self.assertEqual(PreAssessmentStepTime.objects.filter(pre_assessment=open_pa).count(), 1)
        review = self._row(done_pa.id, "review")
        self.assertEqual((review.started_at, review.done_at), (None, at(50)))
        self.assertEqual(PreAssessmentStepTime.objects.filter(pre_assessment=done_pa).count(), 2)
        mod.backfill_step_times(apps, None)  # idempotent
        self.assertEqual(PreAssessmentStepTime.objects.count(), 3)


class QueryCountTest(_Base):
    def _grow(self, n):
        for i in range(n):
            c = Child.objects.create(
                fullname=f"Kid {i}", case_type="Foster Care",
                assigned_psychologist=self.psy, social_worker=self.sw)
            pa = PreAssessment.objects.create(
                child=c, psychologist=self.psy, status="in_progress", current_step="interview")
            for step in ("consent", "interview"):
                PreAssessmentStepTime.objects.create(
                    pre_assessment=pa, step=step, started_at=at(i))

    def _count(self, url):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_queries_do_not_grow_with_children(self):
        self._grow(2)
        small = (self._count("/api/children/"), self._count("/api/reports/monitoring/"))
        self._grow(8)
        big = (self._count("/api/children/"), self._count("/api/reports/monitoring/"))
        self.assertEqual(small, big)

    def test_child_report_queries_do_not_grow_with_pre_assessments(self):
        url = f"/api/reports/child/{self.child.id}/"
        for i in range(2):
            PreAssessmentStepTime.objects.create(
                pre_assessment=PreAssessment.objects.create(
                    child=self.child, psychologist=self.psy, status="completed"),
                step="consent", started_at=at(i))
        small = self._count(url)
        for i in range(6):
            PreAssessmentStepTime.objects.create(
                pre_assessment=PreAssessment.objects.create(
                    child=self.child, psychologist=self.psy, status="completed"),
                step="consent", started_at=at(i))
        self.assertEqual(small, self._count(url))
