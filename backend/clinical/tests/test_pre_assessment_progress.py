"""Pre-Assessment as a status on the child's page: Not started, Started,
In progress, Completed - driven by the wizard step the psychologist is on.

No test reads the wall clock: ordering between pre-assessments uses explicit
created_at stamps.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import PA_STEPS, Child
from clinical.models import ConsentRecord, PreAssessment

User = get_user_model()

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=dt_timezone.utc)


class _Base(APITestCase):
    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.previous = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)
        self.child = Child.objects.create(
            fullname="Ana Cruz", case_type="Foster Care",
            assigned_psychologist=self.psy, social_worker=self.sw)

    def _auth(self, email="p@racco1.gov.ph"):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _consent(self, child=None):
        return ConsentRecord.objects.create(
            child=child or self.child, status="signed", recorded_by=self.psy)

    def _pa(self, minutes=0, child=None, by=None, **kw):
        pa = PreAssessment.objects.create(
            child=child or self.child, psychologist=by or self.psy, **kw)
        PreAssessment.objects.filter(pk=pa.pk).update(created_at=T0 + timedelta(minutes=minutes))
        pa.refresh_from_db()
        return pa

    def _progress(self, child=None):
        child = Child.objects.get(pk=(child or self.child).pk)
        return child.pre_assessment_progress()


class StepFieldTest(_Base):
    def test_default_step_is_consent(self):
        self._auth()
        resp = self.client.post("/api/pre-assessments/", {"child": self.child.id}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["current_step"], "consent")
        self.assertIn("updated_at", resp.data)

    def test_patch_step_is_saved_and_returned(self):
        self._auth()
        pid = self.client.post("/api/pre-assessments/", {"child": self.child.id},
                               format="json").data["id"]
        resp = self.client.patch(f"/api/pre-assessments/{pid}/",
                                 {"current_step": "instruments"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["current_step"], "instruments")
        self.assertEqual(PreAssessment.objects.get(pk=pid).current_step, "instruments")

    def test_invalid_step_is_refused(self):
        self._auth()
        pid = self.client.post("/api/pre-assessments/", {"child": self.child.id},
                               format="json").data["id"]
        resp = self.client.patch(f"/api/pre-assessments/{pid}/",
                                 {"current_step": "banana"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("current_step", resp.data)

    def test_completed_refuses_a_step_change(self):
        pa = self._pa(status="completed", current_step="review")
        self._auth()
        resp = self.client.patch(f"/api/pre-assessments/{pa.id}/",
                                 {"current_step": "consent"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("current_step", resp.data)
        self.assertEqual(PreAssessment.objects.get(pk=pa.pk).current_step, "review")

    def test_step_tuple_in_children_matches_clinical(self):
        self.assertEqual(list(PA_STEPS), list(PreAssessment.STEP_CHOICES))


class ProgressStatesTest(_Base):
    def test_not_started(self):
        p = self._progress()
        self.assertEqual(p["state"], "not_started")
        self.assertEqual(p["label"], "Not started")
        self.assertIsNone(p["step"])
        self.assertFalse(p["reassessment"])

    def test_started_when_fresh_without_consent(self):
        self._pa(status="in_progress")
        p = self._progress()
        self.assertEqual(p["state"], "started")
        self.assertEqual(p["label"], "Started · Consent")
        self.assertEqual(p["step_number"], 2)
        self.assertEqual(p["total_steps"], 6)

    def test_pending_counts_as_open(self):
        self._pa(status="pending")
        self.assertEqual(self._progress()["state"], "started")

    def test_in_progress_with_step_label_and_number(self):
        self._pa(status="in_progress", consent=self._consent(), current_step="interview")
        p = self._progress()
        self.assertEqual(p["state"], "in_progress")
        self.assertEqual(p["step_label"], "Clinical interview")
        self.assertEqual(p["step_number"], 3)
        self.assertEqual(p["label"], "In progress · Clinical interview")
        self.assertIsNotNone(p["updated_at"])

    def test_consent_step_with_consent_linked_is_in_progress(self):
        self._pa(status="in_progress", consent=self._consent())
        self.assertEqual(self._progress()["state"], "in_progress")

    def test_review_is_step_six(self):
        self._pa(status="in_progress", consent=self._consent(), current_step="review")
        self.assertEqual(self._progress()["step_number"], 6)

    def test_completed(self):
        self._pa(status="completed", current_step="review", completed_at=T0)
        p = self._progress()
        self.assertEqual(p["state"], "completed")
        self.assertEqual(p["label"], "Completed")
        self.assertIsNone(p["step"])
        self.assertEqual(p["completed_at"], T0.isoformat())

    def test_completed_then_newer_open_is_a_reassessment(self):
        self._pa(0, status="completed", completed_at=T0)
        self._pa(10, status="in_progress", consent=self._consent(), current_step="problems")
        p = self._progress()
        self.assertEqual(p["state"], "in_progress")
        self.assertTrue(p["reassessment"])
        self.assertEqual(p["step_number"], 5)

    def test_older_abandoned_open_row_does_not_override_completed(self):
        self._pa(10, status="completed", completed_at=T0 + timedelta(minutes=10))
        self._pa(0, status="in_progress", consent=self._consent(), current_step="problems")
        p = self._progress()
        self.assertEqual(p["state"], "completed")
        self.assertFalse(p["reassessment"])

    def test_same_keys_in_every_state(self):
        empty = set(self._progress())
        self._pa(status="completed", completed_at=T0)
        self.assertEqual(set(self._progress()), empty)


class ExposedTest(_Base):
    def test_child_detail_and_list_carry_the_field(self):
        self._pa(status="in_progress", consent=self._consent(), current_step="interview")
        self._auth()
        detail = self.client.get(f"/api/children/{self.child.id}/")
        self.assertEqual(detail.data["pre_assessment_progress"]["state"], "in_progress")
        listing = self.client.get("/api/children/")
        rows = listing.data["results"] if isinstance(listing.data, dict) else listing.data
        self.assertEqual(rows[0]["pre_assessment_progress"]["step"], "interview")

    def test_child_report_carries_the_field(self):
        self._pa(status="in_progress", current_step="consent")
        self._auth()
        resp = self.client.get(f"/api/reports/child/{self.child.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["child"]["pre_assessment_progress"]["state"], "started")

    def test_monitoring_carries_the_field(self):
        self._pa(status="in_progress", consent=self._consent(), current_step="instruments")
        self._auth()
        resp = self.client.get("/api/reports/monitoring/")
        self.assertEqual(resp.status_code, 200)
        row = resp.data[0]
        self.assertEqual(row["pre_assessment_progress"]["step"], "instruments")
        self.assertIn("pre_assessment_status", row)

    def test_no_history_hides_the_earlier_psychologists_open_pre_assessment(self):
        self.child.assignee_sees_history = False
        self.child.save()
        self._pa(status="in_progress", by=self.previous, current_step="problems",
                 consent=self._consent())
        self._auth()
        detail = self.client.get(f"/api/children/{self.child.id}/")
        self.assertEqual(detail.data["pre_assessment_progress"]["state"], "not_started")
        row = self.client.get("/api/reports/monitoring/").data[0]
        self.assertEqual(row["pre_assessment_progress"]["state"], "not_started")
        report = self.client.get(f"/api/reports/child/{self.child.id}/")
        self.assertEqual(report.data["child"]["pre_assessment_progress"]["state"], "not_started")

    def test_with_history_the_open_pre_assessment_is_seen(self):
        self._pa(status="in_progress", by=self.previous, current_step="problems",
                 consent=self._consent())
        self._auth()
        detail = self.client.get(f"/api/children/{self.child.id}/")
        self.assertEqual(detail.data["pre_assessment_progress"]["step"], "problems")


class QueryCountTest(_Base):
    def _count(self):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get("/api/children/")
        self.assertEqual(resp.status_code, 200)
        return len(ctx)

    def _add_child(self, i):
        c = Child.objects.create(fullname=f"Child {i}", case_type="Foster Care",
                                 assigned_psychologist=self.psy, social_worker=self.sw)
        self._pa(i, child=c, status="in_progress", current_step="interview",
                 consent=self._consent(c))
        self._pa(i + 100, child=c, status="completed", completed_at=T0)

    def test_list_does_not_grow_with_children(self):
        self._auth()
        self._add_child(1)
        one = self._count()
        self._add_child(2)
        self._add_child(3)
        self.assertLessEqual(self._count(), one)
