"""The facts above a pre-session brief (assistant/brief_facts.py).

They are plain queries, counted the way the screen that already shows each one
counts it. These tests hold three things: that each fact agrees with its
screen (Monitoring, the Dashboard's care gaps, the child's page), that they
need no model - the brief itself answers 503 where these answer 200 - and that
the prompt a brief is drafted from did not change.
"""
import json
from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import prompts, services, views
from assistant.models import AssistantJob, AssistantSetting
from assistant.tests.test_role_access import HOSTED
from children.models import Child
from clinical.models import (AgencyFormTemplate, OpinionnaireInvite, ProblemEntry,
                             SelfReportFlag, TreatmentPlan)
from config.clock import clock
from scheduling.models import Appointment

User = get_user_model()


class FactsFixture(APITestCase):
    """One child held by a social worker and assigned to a psychologist who
    took it over from a colleague, with that colleague's history hidden; and
    a second child that belongs to nobody in the first child's circle."""

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
        self.other_sw = User.objects.create_user(
            email="t@racco1.gov.ph", username="t", password="pass1234", role=staff_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=admin_role)
        self.child = Child.objects.create(
            fullname="Maria Santos", assigned_psychologist=self.psy,
            social_worker=self.sw, assignee_sees_history=False)
        self.other_child = Child.objects.create(
            fullname="Juan Dela Cruz", assigned_psychologist=self.previous,
            social_worker=self.other_sw)
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self._flag_count = 0

    def _facts(self, user, child=None):
        self.client.force_authenticate(user)
        return self.client.get(
            f"/api/assistant/brief/child/{(child or self.child).id}/facts/")

    def _chart(self, user):
        self.client.force_authenticate(user)
        return self.client.get(f"/api/reports/child/{self.child.id}/").data

    def _appt(self, days, status=Appointment.SCHEDULED, purpose=Appointment.SESSION,
              psychologist=None):
        return Appointment.objects.create(
            child=self.child, psychologist=psychologist or self.psy,
            start=timezone.now() + timedelta(days=days), status=status, purpose=purpose)

    def _flag(self, question=None, answer="Lagi akong umiiyak sa gabi.", reviewed=False):
        # A distinct question per flag: one row per (invite, question, source).
        self._flag_count += 1
        question = question or f"Question {self._flag_count}?"
        template = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="Self-report",
            fields=[{"label": question}])
        invite = OpinionnaireInvite.objects.create(
            child=self.child, template=template,
            status=OpinionnaireInvite.SUBMITTED, submitted_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7))
        flag = SelfReportFlag.objects.create(
            invite=invite, child=self.child, question=question, answer=answer,
            source=SelfReportFlag.LEXICON, matched="umiiyak")
        if reviewed:
            flag.reviewed_at = timezone.now()
            flag.reviewed_by = self.psy
            flag.save()
        return flag


class ScopeTest(FactsFixture):
    def test_each_role_gets_the_facts_for_a_child_it_can_see(self):
        shared = {"kind", "next_session", "last_session", "open_problems",
                  "treatment_plan", "unreviewed_self_reports", "care_gaps"}
        # The case brief's extra rows (CaseBriefTest holds what is in them).
        case_rows = {"case_referral", "psychologist", "consent", "custodian_texts",
                     "survey"}
        for user, keys in ((self.psy, shared), (self.sw, shared | case_rows),
                           (self.admin, shared | case_rows)):
            with self.subTest(user=user.email):
                res = self._facts(user)
                self.assertEqual(res.status_code, 200, res.data)
                self.assertEqual(set(res.data), keys)

    def test_a_child_out_of_reach_is_404(self):
        self.assertEqual(self._facts(self.psy, self.other_child).status_code, 404)
        # Another social worker's record: the calendar shows it to this one
        # only as a case reference, so its facts are not theirs either.
        self.assertEqual(self._facts(self.sw, self.other_child).status_code, 404)
        self.client.force_authenticate(self.admin)
        self.assertEqual(
            self.client.get("/api/assistant/brief/child/99999/facts/").status_code, 404)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        res = self.client.get(f"/api/assistant/brief/child/{self.child.id}/facts/")
        self.assertIn(res.status_code, (401, 403))

    def test_the_answer_names_nobody(self):
        # Names, not only addresses: the psychologist's own and the colleague
        # who held the child before, whose session is in the answer.
        for user, first, last in ((self.psy, "Pilar", "Quezon"),
                                  (self.previous, "Quirino", "Bautista")):
            user.first_name, user.last_name = first, last
            user.save()
        self._appt(2, psychologist=self.previous)
        self._flag()
        body = json.dumps(self._facts(self.psy).json())
        for name in ("Maria", "Santos", "Pilar", "Quezon", "Quirino", "Bautista",
                     self.previous.email, self.psy.email):
            self.assertNotIn(name, body)


class NoModelTest(FactsFixture):
    def _brief_post(self, user):
        self.client.force_authenticate(user)
        return self.client.post(f"/api/assistant/brief/child/{self.child.id}/")

    def test_facts_come_back_with_the_assistant_switched_off(self):
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.assertEqual(self._facts(self.psy).status_code, 200)
        self.assertEqual(self._brief_post(self.psy).status_code, 503)

    @override_settings(**HOSTED)
    def test_facts_come_back_on_a_hosted_deployment(self):
        self.assertEqual(self._facts(self.psy).status_code, 200)
        # The contrast: drafting is refused here, so the facts are all there is.
        self.assertEqual(self._brief_post(self.psy).status_code, 503)

    def test_facts_never_reach_a_model_or_the_audit_log(self):
        self._appt(2)
        self._flag()
        with patch.object(services.OllamaClient, "generate") as local, \
                patch.object(services.OpenAICompatibleClient, "generate") as hosted:
            res = self._facts(self.psy)
        self.assertEqual(res.status_code, 200)
        local.assert_not_called()
        hosted.assert_not_called()
        self.assertFalse(AssistantJob.objects.exists())
        # Not throttled: no model is spent, so there is nothing to ration.
        self.assertIsNone(getattr(views.BriefFactsView, "throttle_scope", None))

    def test_the_brief_prompt_is_unchanged_by_the_facts(self):
        # Putting the facts into the prompt is a prompt change and needs its
        # own ai_eval run; until then the prompt is built from what it always was.
        ProblemEntry.objects.create(child=self.child, logged_by=self.psy,
                                    description="Sleep disturbance")
        TreatmentPlan.objects.create(child=self.child, author=self.psy,
                                     objectives="PLAN OBJECTIVE")
        flag = self._flag(answer="A DISTINCT ANSWER TEXT")
        self._appt(2)

        captured = []

        def capture(prompt, system=None):
            captured.append(prompt)
            return "Brief."

        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate", side_effect=capture):
            res = self.client.post(f"/api/assistant/brief/child/{self.child.id}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(captured), 1)
        self.assertEqual(
            captured[0], prompts.build_brief_prompt(self.child, only_author=self.psy))
        for text in ("Sleep disturbance", "PLAN OBJECTIVE", flag.answer):
            self.assertNotIn(text, captured[0])


class SessionsTest(FactsFixture):
    def test_next_session_is_the_soonest_scheduled_one(self):
        self._appt(1, Appointment.CANCELLED)
        soonest = self._appt(2, Appointment.SCHEDULED, Appointment.FOLLOW_UP)
        self._appt(5, Appointment.SCHEDULED)
        nxt = self._facts(self.psy).data["next_session"]
        self.assertEqual(nxt["purpose"], "Follow-up")
        self.assertEqual(datetime.fromisoformat(nxt["start"]), soonest.start)

    def test_last_session_counts_only_completed_ones(self):
        self._appt(-10, Appointment.COMPLETED, Appointment.SESSION)
        self._appt(-2, Appointment.NO_SHOW)
        last = self._facts(self.psy).data["last_session"]
        self.assertEqual(last["days_ago"], 10)
        self.assertEqual(last["purpose"], "Session")

    def test_nothing_booked_or_held_is_null(self):
        facts = self._facts(self.psy).data
        self.assertIsNone(facts["next_session"])
        self.assertIsNone(facts["last_session"])

    def test_next_session_agrees_with_monitoring(self):
        # Any psychologist's booking counts, as on Monitoring: it is the
        # child's next session, not the viewer's.
        self._appt(3, psychologist=self.previous)
        facts = self._facts(self.admin).data
        local = timezone.localtime(datetime.fromisoformat(facts["next_session"]["start"]))
        self.client.force_authenticate(self.admin)
        row = next(r for r in self.client.get("/api/reports/monitoring/").data
                   if r["child_id"] == self.child.id)
        self.assertEqual(row["next_session"], f"{local:%Y-%m-%d} {clock(local)}")
        self.assertIsNotNone(self._facts(self.psy).data["next_session"])


class RecordFactsTest(FactsFixture):
    def test_open_problems_whoever_logged_them(self):
        ProblemEntry.objects.create(child=self.child, logged_by=self.previous,
                                    description="Sleep disturbance", category="Sleep")
        ProblemEntry.objects.create(child=self.child, logged_by=self.previous,
                                    description="Withdrawal", resolved=True)
        rows = self._facts(self.psy).data["open_problems"]
        self.assertEqual([p["description"] for p in rows], ["Sleep disturbance"])
        self.assertEqual(rows[0]["category"], "Sleep")
        # The same list the child's page shows this psychologist.
        chart = self._chart(self.psy)
        self.assertEqual([p["description"] for p in rows],
                         [p["description"] for p in chart["problems"] if not p["resolved"]])

    def test_only_an_active_plan_counts(self):
        TreatmentPlan.objects.create(child=self.child, author=self.psy,
                                     objectives="Done.", status=TreatmentPlan.COMPLETED)
        self.assertIsNone(self._facts(self.psy).data["treatment_plan"])
        TreatmentPlan.objects.create(child=self.child, author=self.psy,
                                     objectives="Sleep through the night.",
                                     review_date="2026-12-01")
        plan = self._facts(self.psy).data["treatment_plan"]
        self.assertEqual(plan, {"objectives": "Sleep through the night.",
                                "review_date": "2026-12-01"})

    def test_self_reports_are_counted_never_quoted(self):
        self._flag(answer="ANSWER ONE")
        self._flag(answer="ANSWER TWO")
        self._flag(answer="ANSWER THREE", reviewed=True)
        res = self._facts(self.psy)
        self.assertEqual(res.data["unreviewed_self_reports"], 2)
        # The count the page's badge shows: the child's own words are exempt
        # from the carry-history control, whoever drafted the plan or notes.
        chart = self._chart(self.psy)
        self.assertEqual(
            res.data["unreviewed_self_reports"],
            len([f for f in chart["self_report_flags"] if not f["is_reviewed"]]))
        body = json.dumps(res.json())
        for flag in SelfReportFlag.objects.all():
            self.assertNotIn(flag.answer, body)
            self.assertNotIn(flag.question, body)

    def test_care_gaps_are_the_dashboards_less_the_self_report_line(self):
        self._flag()
        facts = self._facts(self.admin).data
        self.client.force_authenticate(self.admin)
        dashboard = self.client.get("/api/reports/dashboard/").data["care_gaps"]
        mine = [a for a in dashboard if a["child_id"] == self.child.id]
        # The control: the dashboard does carry the line this one leaves out.
        self.assertIn("self_report_concern", {a["type"] for a in mine})
        self.assertEqual([g["message"] for g in facts["care_gaps"]],
                         [a["message"] for a in mine if a["type"] != "self_report_concern"])
        types = {g["type"] for g in facts["care_gaps"]}
        self.assertIn("no_upcoming_appointment", types)
        self.assertNotIn("self_report_concern", types)

    def test_a_social_workers_care_gaps_are_their_own_rules(self):
        # The same child, thirty days in: the psychologist's list calls the
        # pre-assessment stalled, which is not the social worker's to start.
        Child.objects.filter(pk=self.child.pk).update(
            created_at=timezone.now() - timedelta(days=30))
        self._flag()
        mine = self._facts(self.sw).data
        theirs = self._facts(self.psy).data
        # The referral and consent gaps are the case brief's own rows now, so
        # only the booking gap is left to list.
        self.assertEqual({"no_upcoming_appointment"},
                         {g["type"] for g in mine["care_gaps"]})
        self.assertIn("pre_assessment_overdue", {g["type"] for g in theirs["care_gaps"]})
        self.assertNotIn("no_case_referral", {g["type"] for g in theirs["care_gaps"]})
        # And it agrees with their own Dashboard, less the lines said elsewhere
        # - the control being that the Dashboard does carry them.
        self.client.force_authenticate(self.sw)
        dashboard = [a for a in self.client.get("/api/reports/dashboard/").data["care_gaps"]
                     if a["child_id"] == self.child.id]
        self.assertTrue({"self_report_concern", "no_case_referral", "no_signed_consent"}
                        <= {a["type"] for a in dashboard})
        said = {"self_report_concern", "no_case_referral", "no_psychologist",
                "no_signed_consent"}
        self.assertEqual(
            [(g["type"], g["message"]) for g in mine["care_gaps"]],
            [(a["type"], a["message"]) for a in dashboard if a["type"] not in said])
