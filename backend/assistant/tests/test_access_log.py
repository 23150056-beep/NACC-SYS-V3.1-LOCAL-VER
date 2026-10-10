"""The ISA's access log: who had the model read this child's record.

Rows are made through the real endpoints wherever one exists. The point is
that each feature attributes its read to the child, and a hand-made row would
prove only that the view reads rows.
"""
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import services, views
from assistant.models import AssistantJob, AssistantSetting
from assistant.tests.test_role_access import HOSTED
from children.models import Child
from clinical.models import (
    AgencyFormTemplate, CaseReferral, OpinionnaireInvite, PsychologicalReport)
from clinical.self_report_model_check import run_model_check

User = get_user_model()
URL = "/api/assistant/access-log/child/{}/"


class AccessLogBase(APITestCase):
    """Shared fixtures only - no tests, so a subclass does not run them twice."""

    def setUp(self):
        roles = {name: Role.objects.create(role_name=name)
                 for name in (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)}
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=roles[Role.ADMINISTRATOR], first_name="Ada", last_name="Lim")
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=roles[Role.PSYCHOLOGIST], first_name="Pia", last_name="Reyes")
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=roles[Role.STAFF], first_name="Sara", last_name="Cruz")
        self.child = Child.objects.create(
            fullname="Maria Santos", assigned_psychologist=self.psy,
            social_worker=self.sw)
        self.other = Child.objects.create(
            fullname="Juan Dela Cruz", assigned_psychologist=self.psy,
            social_worker=self.sw)
        self.report = PsychologicalReport.objects.create(
            child=self.child, author=self.psy, original_filename="initial.pdf",
            extracted_text="Report text.")
        self.referral = CaseReferral.objects.create(
            child=self.child, uploaded_by=self.sw, original_filename="referral.pdf",
            extracted_text="Referral text.")
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.addCleanup(self._clear_in_flight)

    def _clear_in_flight(self):
        with views._IN_FLIGHT_LOCK:
            views._IN_FLIGHT.clear()

    def _drafted(self, user, url, reply="Draft."):
        self.client.force_authenticate(user)
        with patch.object(services.OllamaClient, "generate", return_value=reply):
            return self.client.post(url, format="json")

    def _brief(self, user, child=None, reply="Draft."):
        res = self._drafted(
            user, f"/api/assistant/brief/child/{(child or self.child).id}/", reply)
        self.assertEqual(200, res.status_code)
        return res

    def _case_brief(self, user=None, child=None, reply="A written brief."):
        res = self._drafted(
            user or self.sw, f"/api/assistant/case-brief/child/{(child or self.child).id}/",
            reply)
        self.assertEqual(200, res.status_code)
        return res

    def _log(self, child=None):
        self.client.force_authenticate(self.admin)
        res = self.client.get(URL.format((child or self.child).id))
        self.assertEqual(200, res.status_code)
        return res.data


class WhoMayReadItTest(AccessLogBase):
    def test_the_isa_reads_it(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(200, self.client.get(URL.format(self.child.id)).status_code)

    def test_a_social_worker_is_refused_on_their_own_record(self):
        self.client.force_authenticate(self.sw)
        self.assertEqual(403, self.client.get(URL.format(self.child.id)).status_code)

    def test_the_childs_own_psychologist_is_refused(self):
        self.client.force_authenticate(self.psy)
        self.assertEqual(403, self.client.get(URL.format(self.child.id)).status_code)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(401, self.client.get(URL.format(self.child.id)).status_code)

    def test_an_unknown_child_is_404(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(404, self.client.get(URL.format(999999)).status_code)


class WhatIsListedTest(AccessLogBase):
    def test_a_brief_names_who_drafted_it(self):
        self._brief(self.psy)
        data = self._log()
        self.assertEqual(1, len(data["entries"]))
        entry = data["entries"][0]
        self.assertEqual("brief", entry["kind"])
        self.assertEqual({"name": "Pia Reyes", "role": "Psychologist"}, entry["by"])
        self.assertEqual("pending", entry["status"])
        self.assertIsNone(entry["document"])
        self.assertFalse(entry["document_deleted"])

    def test_a_case_brief_is_listed_as_its_own_kind_with_the_social_worker_named(self):
        self._case_brief()
        data = self._log()
        self.assertEqual(1, len(data["entries"]))
        entry = data["entries"][0]
        self.assertEqual("case_brief", entry["kind"])
        self.assertEqual({"name": "Sara Cruz", "role": "Staff"}, entry["by"])
        self.assertEqual("pending", entry["status"])
        self.assertIsNone(entry["document"])
        self.assertFalse(entry["document_deleted"])
        self.assertEqual(1, data["total"])

    def test_a_case_brief_and_a_psychologists_brief_are_told_apart(self):
        self._brief(self.psy)
        self._case_brief()
        kinds = sorted(e["kind"] for e in self._log()["entries"])
        self.assertEqual(["brief", "case_brief"], kinds)

    def test_a_case_brief_that_did_not_complete_is_listed_as_failed(self):
        self.client.force_authenticate(self.sw)
        with patch.object(services.OllamaClient, "generate",
                          side_effect=services.AIUnavailable("down")):
            res = self.client.post(f"/api/assistant/case-brief/child/{self.child.id}/")
        self.assertEqual(503, res.status_code)
        entries = self._log()["entries"]
        self.assertEqual(["case_brief"], [e["kind"] for e in entries])
        self.assertEqual(["failed"], [e["status"] for e in entries])

    def test_a_case_brief_that_was_refused_is_not_a_read(self):
        # The ISA and a psychologist are refused before anything is sent; a
        # hosted deployment and a switched-off assistant likewise.
        for user in (self.admin, self.psy):
            self.client.force_authenticate(user)
            self.assertEqual(403, self.client.post(
                f"/api/assistant/case-brief/child/{self.child.id}/").status_code)
        cfg = AssistantSetting.load()
        cfg.enabled = False
        cfg.save()
        self.client.force_authenticate(self.sw)
        self.assertEqual(503, self.client.post(
            f"/api/assistant/case-brief/child/{self.child.id}/").status_code)
        data = self._log()
        self.assertEqual([], data["entries"])
        self.assertEqual(0, data["total"])
        self.assertFalse(AssistantJob.objects.exists())

    def test_a_prefetched_brief_is_listed_too(self):
        with patch.object(services.OllamaClient, "generate", return_value="Draft."):
            views._generate_briefs_now([self.child.id], self.psy)
        entries = self._log()["entries"]
        self.assertEqual(["brief"], [e["kind"] for e in entries])
        self.assertEqual("Pia Reyes", entries[0]["by"]["name"])

    def test_summaries_are_attributed_to_the_child(self):
        self.assertEqual(200, self._drafted(
            self.psy, f"/api/assistant/summarize-report/{self.report.id}/").status_code)
        self.assertEqual(200, self._drafted(
            self.sw,
            f"/api/assistant/summarize-case-referral/{self.referral.id}/").status_code)
        entries = self._log()["entries"]
        self.assertEqual({"report_summary": "initial.pdf",
                          "referral_summary": "referral.pdf"},
                         {e["kind"]: e["document"] for e in entries})
        referral = next(e for e in entries if e["kind"] == "referral_summary")
        self.assertEqual("Staff", referral["by"]["role"])

    def test_a_summary_stays_listed_after_its_document_is_deleted(self):
        """Why the column exists: replacing a referral deletes the old one, and
        a log worked out from input_ref would lose the read with it."""
        self._drafted(
            self.sw, f"/api/assistant/summarize-case-referral/{self.referral.id}/")
        self.client.force_authenticate(self.sw)
        res = self.client.delete(f"/api/case-referrals/{self.referral.id}/")
        self.assertEqual(204, res.status_code)

        entries = self._log()["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual("referral_summary", entries[0]["kind"])
        self.assertIsNone(entries[0]["document"])
        self.assertTrue(entries[0]["document_deleted"])

    def test_confirming_a_summary_shows_in_its_status(self):
        self._drafted(
            self.psy, f"/api/assistant/summarize-report/{self.report.id}/", "Draft.")
        self.client.force_authenticate(self.psy)
        res = self.client.post(
            f"/api/assistant/confirm-summary/{self.report.id}/",
            {"text": "Draft."}, format="json")
        self.assertEqual(200, res.status_code)
        self.assertEqual("accepted", self._log()["entries"][0]["status"])

    def test_a_read_that_did_not_complete_is_listed_as_failed(self):
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "generate",
                          side_effect=services.AIUnavailable("down")):
            res = self.client.post(f"/api/assistant/brief/child/{self.child.id}/")
        self.assertEqual(503, res.status_code)
        entries = self._log()["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual("failed", entries[0]["status"])
        self.assertEqual("Pia Reyes", entries[0]["by"]["name"])

    def test_the_self_report_check_is_one_entry_per_survey(self):
        template = AgencyFormTemplate.objects.create(
            title="Self-report", fields=[{"label": "Q1"}, {"label": "Q2"}])
        invite = OpinionnaireInvite.objects.create(
            child=self.child, template=template,
            status=OpinionnaireInvite.SUBMITTED, submitted_at=timezone.now(),
            answers={"Q1": "Okay lang.", "Q2": "Masaya ako."},
            expires_at=timezone.now() + timedelta(days=7))
        with patch("assistant.services.OllamaClient.generate",
                   return_value="NO - settled"):
            run_model_check(invite.pk)

        entries = self._log()["entries"]
        self.assertEqual(1, len(entries))
        entry = entries[0]
        self.assertEqual("survey_check", entry["kind"])
        self.assertEqual(2, entry["reads"])
        self.assertIsNone(entry["by"])
        self.assertEqual("Self-report", entry["document"])
        self.assertEqual("read", entry["status"])

    def _survey(self, answers):
        template = AgencyFormTemplate.objects.create(
            title="Self-report", fields=[{"label": q} for q in answers])
        return OpinionnaireInvite.objects.create(
            child=self.child, template=template,
            status=OpinionnaireInvite.SUBMITTED, submitted_at=timezone.now(),
            answers=answers, expires_at=timezone.now() + timedelta(days=7))

    def test_a_check_that_stopped_part_way_is_partly_read(self):
        invite = self._survey({"Q1": "Okay lang.", "Q2": "Masaya ako."})
        with patch("assistant.services.OllamaClient.generate",
                   side_effect=["NO - settled", services.AIUnavailable("dropped")]):
            run_model_check(invite.pk)
        entries = self._log()["entries"]
        self.assertEqual(1, len(entries))
        self.assertEqual("survey_check", entries[0]["kind"])
        self.assertEqual("partly_read", entries[0]["status"])
        self.assertEqual(1, entries[0]["reads"])

    def test_a_check_that_read_nothing_is_failed(self):
        invite = self._survey({"Q1": "Okay lang."})
        with patch("assistant.services.OllamaClient.generate",
                   side_effect=services.AIUnavailable("down")):
            run_model_check(invite.pk)
        entries = self._log()["entries"]
        self.assertEqual(["failed"], [e["status"] for e in entries])

    def test_a_failed_check_is_still_attributed_to_the_child(self):
        """Where a read was attempted, the row must carry the child: remove
        child= from the failure path and this is the test that notices."""
        invite = self._survey({"Q1": "Okay lang."})
        with patch("assistant.services.OllamaClient.generate",
                   side_effect=services.AIUnavailable("down")):
            run_model_check(invite.pk)
        job = AssistantJob.objects.get(job_type="self_report")
        self.assertFalse(job.ok)
        self.assertEqual(self.child.id, job.child_id)
        self.assertEqual(1, len(self._log()["entries"]))

    @override_settings(**HOSTED)
    def test_a_hosted_deployment_logs_no_read_that_never_happened(self):
        """Drafting from a child's answers is refused before anything is sent,
        so the child's log must not list it - the row stays for the figures."""
        invite = self._survey({"Q1": "Okay lang."})
        with patch.object(services.OllamaClient, "generate") as local, \
                patch.object(services.OpenAICompatibleClient, "generate") as hosted:
            run_model_check(invite.pk)
        local.assert_not_called()
        hosted.assert_not_called()
        job = AssistantJob.objects.get(job_type="self_report")
        self.assertFalse(job.ok)
        self.assertIsNone(job.child_id)
        data = self._log()
        self.assertEqual([], data["entries"])
        self.assertEqual(0, data["total"])

    def test_another_childs_reads_are_not_listed(self):
        self._brief(self.psy, child=self.other)
        data = self._log()
        self.assertEqual([], data["entries"])
        self.assertEqual(0, data["total"])

    def test_chat_polish_and_census_are_not_listed(self):
        """None of the three reads a child's record, so none carries a child."""
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_child_summary", {"name": "Maria"})):
            res = self.client.post("/api/assistant/ask/",
                                   {"question": "tell me about Maria"}, format="json")
        self.assertEqual(200, res.status_code)
        with patch.object(services.OllamaClient, "generate",
                          return_value="Maria slept well and ate breakfast."):
            res = self.client.post("/api/assistant/polish-remark/",
                                   {"text": "Maria slept well and ate breakfast."},
                                   format="json")
        self.assertEqual(200, res.status_code)
        self.client.force_authenticate(self.admin)
        with patch.object(services.OllamaClient, "generate", return_value="Narrative."):
            res = self.client.post("/api/assistant/census-narrative/",
                                   {"figures": {"active": 2}}, format="json")
        self.assertEqual(200, res.status_code)

        data = self._log()
        self.assertEqual([], data["entries"])
        self.assertEqual(0, data["total"])
        # The rows exist; they are just not attributed to anyone's record.
        self.assertEqual(3, AssistantJob.objects.count())

    def test_it_never_shows_what_was_drafted(self):
        self._brief(self.psy, reply="SECRET DRAFT TEXT")
        self.assertNotIn("SECRET DRAFT TEXT", str(self._log()))
        self._case_brief(reply="SECRET CASE BRIEF TEXT")
        self.assertNotIn("SECRET CASE BRIEF TEXT", str(self._log()))


# The job types that do NOT read one child's record, and so are not in the log -
# each for a reason that is checked below, not only asserted here.
NOT_A_CHILD_READ = {
    # The chatbot's model is handed the typed question and picks a lookup; the
    # results come from the database and never pass through it.
    "chat",
    # Polishing reads only the words being typed into the remark box.
    "remark_polish",
    # Narrates agency-wide figures the caller already computed.
    "census_narrative",
}


class TheLogCoversEveryJobTypeTest(AccessLogBase):
    """A new kind of job that reads a child's record must reach the ISA's log,
    or the log says "nothing was read" about a child it was read about. The case
    brief shipped as a new job type and would have been invisible without this."""

    def test_every_job_type_is_listed_or_exempt_on_purpose(self):
        types = {kind for kind, _ in AssistantJob.TYPE_CHOICES}
        logged = set(views.ACCESS_LOGGED_JOB_TYPES)
        self.assertEqual(
            set(), types - logged - NOT_A_CHILD_READ,
            "A job type the log neither lists nor exempts: say which, and why, in "
            "views.ACCESS_LOGGED_JOB_TYPES or NOT_A_CHILD_READ.")
        self.assertEqual(set(), logged & NOT_A_CHILD_READ, "Both listed and exempt.")
        self.assertEqual(set(), (logged | NOT_A_CHILD_READ) - types,
                         "Names a job type that no longer exists.")

    def test_every_listed_type_really_reaches_the_log(self):
        template = AgencyFormTemplate.objects.create(
            title="Self-report", fields=[{"label": "Q1"}])
        invite = OpinionnaireInvite.objects.create(
            child=self.child, template=template,
            status=OpinionnaireInvite.SUBMITTED, submitted_at=timezone.now(),
            answers={"Q1": "Okay lang."}, expires_at=timezone.now() + timedelta(days=7))
        refs = {"brief": f"child:{self.child.id}", "case_brief": f"child:{self.child.id}",
                "doc_intelligence": f"report:{self.report.id}",
                "self_report": f"invite:{invite.id}"}
        self.assertEqual(set(views.ACCESS_LOGGED_JOB_TYPES), set(refs))
        for job_type, ref in refs.items():
            AssistantJob.objects.create(job_type=job_type, input_ref=ref, child=self.child,
                                        created_by=self.sw, ok=True)
        entries = self._log()["entries"]
        self.assertEqual(
            ["brief", "case_brief", "report_summary", "survey_check"],
            sorted(e["kind"] for e in entries))

    def test_the_exempt_types_write_no_child(self):
        """Run the real endpoints: not one of these reads a record, so not one
        of the rows names a child."""
        self.client.force_authenticate(self.psy)
        with patch.object(services.OllamaClient, "choose_tool",
                          return_value=("get_child_summary", {"name": "Maria"})):
            self.assertEqual(200, self.client.post(
                "/api/assistant/ask/", {"question": "tell me about Maria"},
                format="json").status_code)
        with patch.object(services.OllamaClient, "generate", return_value="Polished."):
            self.assertEqual(200, self.client.post(
                "/api/assistant/polish-remark/", {"text": "Maria slept well."},
                format="json").status_code)
        self.client.force_authenticate(self.admin)
        with patch.object(services.OllamaClient, "generate", return_value="Narrative."):
            self.assertEqual(200, self.client.post(
                "/api/assistant/census-narrative/", {"figures": {"active": 2}},
                format="json").status_code)
        rows = AssistantJob.objects.filter(job_type__in=NOT_A_CHILD_READ)
        self.assertEqual(NOT_A_CHILD_READ, {r.job_type for r in rows})
        self.assertEqual([], [r.job_type for r in rows if r.child_id is not None])


class OrderAndLimitTest(AccessLogBase):
    def test_newest_first_and_capped(self):
        now = timezone.now()
        jobs = []
        for i in range(4):
            job = AssistantJob.objects.create(
                job_type="brief", input_ref=f"child:{self.child.id}",
                child=self.child, created_by=self.psy)
            AssistantJob.objects.filter(pk=job.pk).update(
                created_at=now - timedelta(days=i))
            jobs.append(job)

        with patch.object(views.ChildAccessLogView, "LIMIT", 3):
            data = self._log()
        self.assertEqual(3, len(data["entries"]))
        self.assertEqual(4, data["total"])
        times = [e["at"] for e in data["entries"]]
        self.assertEqual(sorted(times, reverse=True), times)
        self.assertEqual(3, len(set(times)))
        self.assertNotIn(f"job:{jobs[3].id}", [e["key"] for e in data["entries"]])


class BackfillTest(AccessLogBase):
    """assistant 0006: rows logged before the column existed are attributed
    from what still exists, and only the three job types that read a record."""

    def test_existing_rows_are_attributed_and_the_rest_left_alone(self):
        template = AgencyFormTemplate.objects.create(
            title="Self-report", fields=[{"label": "Q1"}])
        invite = OpinionnaireInvite.objects.create(
            child=self.child, template=template,
            status=OpinionnaireInvite.SUBMITTED, submitted_at=timezone.now(),
            answers={"Q1": "Okay lang."},
            expires_at=timezone.now() + timedelta(days=7))

        def row(job_type, ref):
            return AssistantJob.objects.create(job_type=job_type, input_ref=ref)

        brief = row("brief", f"child:{self.child.id}")
        report = row("doc_intelligence", f"report:{self.report.id}")
        referral = row("doc_intelligence", f"casereferral:{self.referral.id}")
        survey = row("self_report", f"invite:{invite.id}")
        gone = row("doc_intelligence", "report:999999")
        # Typed text that merely looks like a reference.
        chat = row("chat", f"child:{self.child.id}")
        polish = row("remark_polish", "remark:draft")

        import_module("assistant.migrations.0006_assistantjob_child").attribute(apps, None)

        owner = dict(AssistantJob.objects.values_list("pk", "child_id"))
        for job in (brief, report, referral, survey):
            self.assertEqual(self.child.id, owner[job.pk])
        for job in (gone, chat, polish):
            self.assertIsNone(owner[job.pk])
