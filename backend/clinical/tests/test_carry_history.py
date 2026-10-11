"""The carry-history control, wherever a clinical record leaves the server.

`Child.assignee_sees_history = False` spares a newly assigned psychologist a
colleague's prior opinions: they see only what they wrote themselves. The
child's page applied it, and until 27 Sep 2026 it was the only reader that
did. `GET /api/remarks/?child=<id>` returned the previous psychologist's notes
the page had just hidden, and the report file, interview, treatment plan,
result entry and pre-assessment endpoints did the same through the same base
class; Monitoring printed each child's latest remark and classification
whoever wrote them. The screens never asked for those rows, so nobody saw them
by accident - which is also why nobody noticed.

Problems and consents are exempt, as they always were on the child's page: a
problem list and a guardian's signature are the case's facts, not a
colleague's opinion, and the next psychologist needs both.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from clinical.models import (ClinicalInterviewRecord, ConsentRecord, PreAssessment,
                             ProblemEntry, PsychologicalReport, RemarkNote,
                             ResultEntry, TreatmentPlan)

User = get_user_model()


class CarryHistoryBase(APITestCase):
    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        staff_role = Role.objects.create(role_name=Role.STAFF)
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.previous = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=staff_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=admin_role)
        self.child = Child.objects.create(
            fullname="Maria Santos", assigned_psychologist=self.psy,
            social_worker=self.sw, assignee_sees_history=False)

        # One record of each kind by the previous psychologist, and one by the
        # current one. `mine`/`theirs` map an endpoint to the two rows.
        self.rows = {}
        for path, make in (
                ("remarks", lambda by, tag: RemarkNote.objects.create(
                    child=self.child, author=by, text=tag)),
                ("treatment-plans", lambda by, tag: TreatmentPlan.objects.create(
                    child=self.child, author=by, objectives=tag)),
                ("result-entries", lambda by, tag: ResultEntry.objects.create(
                    child=self.child, entered_by=by, summary=tag, classification=tag)),
                ("interviews", lambda by, tag: ClinicalInterviewRecord.objects.create(
                    child=self.child, interviewer=by)),
                ("pre-assessments", lambda by, tag: PreAssessment.objects.create(
                    child=self.child, psychologist=by)),
                ("report-files", lambda by, tag: PsychologicalReport.objects.create(
                    child=self.child, author=by,
                    file=SimpleUploadedFile(f"{tag}.pdf", b"%PDF-1.4 " + tag.encode()),
                    original_filename=f"{tag}.pdf", extracted_text=tag)),
        ):
            self.rows[path] = (make(self.psy, "MINE"), make(self.previous, "THEIRS"))

    def _ids(self, user, path):
        self.client.force_authenticate(user)
        res = self.client.get(f"/api/{path}/?child={self.child.id}")
        self.assertEqual(res.status_code, 200, path)
        return {row["id"] for row in res.data}


class RecordEndpointsTest(CarryHistoryBase):
    def test_a_psychologist_without_history_sees_only_their_own(self):
        for path, (mine, theirs) in self.rows.items():
            with self.subTest(path=path):
                self.assertEqual({mine.id}, self._ids(self.psy, path))
                res = self.client.get(f"/api/{path}/{theirs.id}/")
                self.assertEqual(res.status_code, 404)

    def test_nor_can_they_edit_what_they_cannot_see(self):
        # The write check lets the child's psychologist edit any of the
        # child's records - including the previous psychologist's note, which
        # the child's page does not show them.
        _, theirs = self.rows["remarks"]
        self.client.force_authenticate(self.psy)
        res = self.client.patch(f"/api/remarks/{theirs.id}/", {"text": "Rewritten."},
                                format="json")
        self.assertEqual(res.status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.text, "THEIRS")

    def test_nor_download_or_read_the_previous_report(self):
        _, theirs = self.rows["report-files"]
        for action in ("download", "text"):
            with self.subTest(action=action):
                # The control: the file is there, so a 404 is a refusal and
                # not a missing file.
                self.client.force_authenticate(self.admin)
                self.assertEqual(
                    self.client.get(f"/api/report-files/{theirs.id}/{action}/").status_code, 200)
                self.client.force_authenticate(self.psy)
                res = self.client.get(f"/api/report-files/{theirs.id}/{action}/")
                self.assertEqual(res.status_code, 404)

    def test_with_history_carried_they_see_everything(self):
        self.child.assignee_sees_history = True
        self.child.save()
        for path, (mine, theirs) in self.rows.items():
            with self.subTest(path=path):
                self.assertEqual({mine.id, theirs.id}, self._ids(self.psy, path))

    def test_the_control_is_about_psychologists_only(self):
        for user in (self.sw, self.admin):
            for path, (mine, theirs) in self.rows.items():
                with self.subTest(user=user.email, path=path):
                    self.assertEqual({mine.id, theirs.id}, self._ids(user, path))

    def test_problems_and_consents_stay_visible(self):
        problem = ProblemEntry.objects.create(child=self.child, logged_by=self.previous,
                                              description="Sleep disturbance")
        consent = ConsentRecord.objects.create(child=self.child, recorded_by=self.previous)
        self.assertEqual({problem.id}, self._ids(self.psy, "problems"))
        self.assertEqual({consent.id}, self._ids(self.psy, "consents"))


class ScreensAgreeTest(CarryHistoryBase):
    def test_the_childs_page_shows_what_the_endpoints_show(self):
        self.client.force_authenticate(self.psy)
        chart = self.client.get(f"/api/reports/child/{self.child.id}/").data
        for key, path in (("remarks", "remarks"), ("treatment_plans", "treatment-plans"),
                          ("result_entries", "result-entries"), ("interviews", "interviews"),
                          ("pre_assessments", "pre-assessments"), ("reports", "report-files")):
            with self.subTest(key=key):
                self.assertEqual({row["id"] for row in chart[key]}, self._ids(self.psy, path))

    def test_the_brief_facts_follow_the_page(self):
        # Theirs is made the newer plan, so a reader ignoring the control
        # would put it first.
        TreatmentPlan.objects.filter(objectives="THEIRS").update(
            created_at=timezone.now() + timedelta(minutes=1))

        def page_and_facts(user):
            self.client.force_authenticate(user)
            chart = self.client.get(f"/api/reports/child/{self.child.id}/").data
            on_page = next(p["objectives"] for p in chart["treatment_plans"]
                           if p["status"] == "active")
            facts = self.client.get(
                f"/api/assistant/brief/child/{self.child.id}/facts/").data
            return on_page, facts["treatment_plan"]["objectives"]

        self.assertEqual(page_and_facts(self.psy), ("MINE", "MINE"))
        self.assertEqual(page_and_facts(self.admin), ("THEIRS", "THEIRS"))

    def test_monitoring_does_not_quote_the_previous_psychologist(self):
        # Theirs is the most recent of each, so a reader ignoring the
        # control would print it.
        RemarkNote.objects.filter(text="THEIRS").update(date="2099-01-01")
        ResultEntry.objects.filter(summary="THEIRS").update(date="2099-01-01")
        self.client.force_authenticate(self.psy)
        row = self.client.get("/api/reports/monitoring/").data[0]
        self.assertEqual(row["latest_remark"], "MINE")
        self.assertEqual(row["latest_classification"], "MINE")
        self.assertEqual(row["report_count"], 1)

        self.client.force_authenticate(self.admin)
        row = self.client.get("/api/reports/monitoring/").data[0]
        self.assertEqual(row["latest_remark"], "THEIRS")
        self.assertEqual(row["report_count"], 2)

    def test_what_is_worked_out_from_pre_assessments_follows_them(self):
        """Review of the first version: the rows were hidden, but the child's
        pre-assessment status, the instruments used, and Monitoring's count and
        last activity were still worked out from every pre-assessment - so
        "Answered" and the previous psychologist's test titles sat beside an
        empty pre-assessment list."""
        from clinical.models import InstrumentCatalog
        PreAssessment.objects.filter(psychologist=self.previous).update(
            status=PreAssessment.COMPLETED, date="2099-01-01")
        theirs = PreAssessment.objects.get(psychologist=self.previous)
        theirs.instruments.add(InstrumentCatalog.objects.create(title="PREVIOUS-TEST-TITLE"))

        def seen_by(user):
            self.client.force_authenticate(user)
            chart = self.client.get(f"/api/reports/child/{self.child.id}/").data["child"]
            record = self.client.get(f"/api/children/{self.child.id}/").data
            listed = next(c for c in self.client.get("/api/children/").data
                          if c["id"] == self.child.id)
            row = self.client.get("/api/reports/monitoring/").data[0]
            return chart, record, listed, row

        for chart_or_record in seen_by(self.psy)[:3]:
            self.assertEqual(chart_or_record["pre_assessment_status"], Child.PA_IN_PROGRESS)
            self.assertEqual(chart_or_record["instruments_used"], [])
        row = seen_by(self.psy)[3]
        self.assertEqual(row["pre_assessment_status"], Child.PA_IN_PROGRESS)
        self.assertEqual(row["pre_assessment_count"], 0)
        self.assertNotEqual(row["last_activity"], "2099-01-01")

        # The control: the record really has a completed one, and the ISA
        # is shown it everywhere.
        chart, record, listed, row = seen_by(self.admin)
        for seen in (chart, record, listed, row):
            self.assertEqual(seen["pre_assessment_status"], Child.PA_ANSWERED)
        self.assertEqual(chart["instruments_used"], ["PREVIOUS-TEST-TITLE"])
        self.assertEqual(row["pre_assessment_count"], 1)
        self.assertEqual(row["last_activity"], "2099-01-01")
