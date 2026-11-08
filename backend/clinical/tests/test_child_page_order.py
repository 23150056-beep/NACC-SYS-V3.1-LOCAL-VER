"""The child's page lists every record newest-edited first (last in, first out).

Stamps are written with queryset .update(), which skips auto_now, so no test
here reads the wall clock.
"""
from datetime import datetime, timedelta, timezone as dt_tz

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from clinical.models import (AgencyFormTemplate, CaseReferral, ClinicalInterviewRecord,
                             OpinionnaireInvite, PreAssessment, ProblemEntry,
                             PsychologicalReport, RemarkNote, ResultEntry,
                             SelfReportFlag, TreatmentPlan)

User = get_user_model()


def stamp(n):
    return datetime(2026, 3, n, 9, 0, tzinfo=dt_tz.utc)


class ChildPageOrderTests(APITestCase):
    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.previous = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.child = Child.objects.create(
            fullname="Maria Santos", assigned_psychologist=self.psy)
        self.template = AgencyFormTemplate.objects.create(
            title="Self-report", fields=[{"label": "Q"}])

    def _get(self):
        self.client.force_authenticate(self.psy)
        res = self.client.get(f"/api/reports/child/{self.child.id}/")
        self.assertEqual(res.status_code, 200)
        return res.data

    def _make(self, key, by):
        c = self.child
        if key == "pre_assessments":
            return PreAssessment.objects.create(child=c, psychologist=by)
        if key == "interviews":
            return ClinicalInterviewRecord.objects.create(child=c, interviewer=by)
        if key == "result_entries":
            return ResultEntry.objects.create(child=c, entered_by=by)
        if key == "remarks":
            return RemarkNote.objects.create(child=c, author=by, text="n")
        if key == "treatment_plans":
            return TreatmentPlan.objects.create(child=c, author=by)
        if key == "reports":
            return PsychologicalReport.objects.create(
                child=c, author=by, file=SimpleUploadedFile("r.pdf", b"%PDF-1.4 x"))
        if key == "problems":
            return ProblemEntry.objects.create(child=c, description="p")
        if key == "case_referrals":
            return CaseReferral.objects.create(
                child=c, uploaded_by=by, file=SimpleUploadedFile("c.pdf", b"%PDF-1.4 x"))
        if key == "opinionnaires":
            return OpinionnaireInvite.objects.create(
                child=c, template=self.template, expires_at=stamp(28))
        raise KeyError(key)

    def _stamp(self, obj, n):
        field = "updated_at" if hasattr(obj, "updated_at") else "created_at"
        type(obj).objects.filter(pk=obj.pk).update(**{field: stamp(n)})

    def test_every_list_is_newest_first(self):
        keys = ("pre_assessments", "interviews", "result_entries", "remarks",
                "treatment_plans", "reports", "problems", "case_referrals",
                "opinionnaires")
        expected = {}
        for key in keys:
            # Created in one order, stamped in another, so neither insertion
            # order nor id order can pass for "newest edited".
            rows = [self._make(key, self.psy) for _ in range(3)]
            for row, n in zip(rows, (5, 20, 10)):
                self._stamp(row, n)
            expected[key] = [rows[1].id, rows[2].id, rows[0].id]
        data = self._get()
        for key in keys:
            with self.subTest(key=key):
                self.assertEqual([r["id"] for r in data[key]], expected[key])

    def test_self_report_flags_newest_first(self):
        invite = self._make("opinionnaires", self.psy)
        flags = [SelfReportFlag.objects.create(
            invite=invite, child=self.child, question=f"q{i}", answer="a",
            source=SelfReportFlag.LEXICON) for i in range(3)]
        for flag, n in zip(flags, (5, 20, 10)):
            SelfReportFlag.objects.filter(pk=flag.pk).update(created_at=stamp(n))
        got = [f["id"] for f in self._get()["self_report_flags"]]
        self.assertEqual(got, [flags[1].id, flags[2].id, flags[0].id])

    def test_open_problems_stay_above_resolved_ones(self):
        rows = [self._make("problems", self.psy) for _ in range(3)]
        ProblemEntry.objects.filter(pk=rows[1].pk).update(resolved=True)
        for row, n in zip(rows, (5, 20, 10)):
            self._stamp(row, n)
        got = [r["id"] for r in self._get()["problems"]]
        # The resolved one is the newest edit, yet sits below both open ones.
        self.assertEqual(got, [rows[2].id, rows[0].id, rows[1].id])

    def test_ties_fall_back_to_newest_id(self):
        rows = [self._make("remarks", self.psy) for _ in range(3)]
        for row in rows:
            self._stamp(row, 7)
        got = [r["id"] for r in self._get()["remarks"]]
        self.assertEqual(got, [r.id for r in reversed(rows)])

    def test_hidden_earlier_history_stays_hidden(self):
        Child.objects.filter(pk=self.child.pk).update(assignee_sees_history=False)
        mine = [self._make("remarks", self.psy) for _ in range(2)]
        theirs = self._make("remarks", self.previous)
        self._stamp(theirs, 28)  # the newest of all, yet not theirs to see
        self._stamp(mine[0], 3)
        self._stamp(mine[1], 4)
        pre_theirs = self._make("pre_assessments", self.previous)
        self._stamp(pre_theirs, 28)
        data = self._get()
        self.assertEqual([r["id"] for r in data["remarks"]], [mine[1].id, mine[0].id])
        self.assertNotIn(theirs.id, [r["id"] for r in data["remarks"]])
        self.assertEqual(data["pre_assessments"], [])
