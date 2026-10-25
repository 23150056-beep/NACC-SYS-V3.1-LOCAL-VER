"""No model-written text reaches a social worker unchecked - for every kind of
model job there is, and for the ones there will be.

`guards.STAFF_DRAFTING` says, for each `AssistantJob` type, how a Staff account
is treated: REFUSED (no door), GUARDED (the draft is checked first) or TOOL_ONLY
(the model only picks a tool). This module holds that table to account:

* every job type must be in it, and a new one fails here with a message saying
  to decide it - the written case brief reached social workers because nothing
  ever asked;
* every entry is then EXERCISED as a Staff account, through the endpoint (or the
  background check) that would give them the text, against a model that writes
  an invented name, date and number. What actually happens is compared with what
  the table claims, so an entry cannot be flipped without the code, nor the code
  loosened without the table noticing.

Each exercise returns what it OBSERVED: "refused", "guarded", "tool_only", or
"open" - model text reached Staff unchecked - which no entry can ever be.
"""
import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import Resolver404, resolve
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from assistant import guards, services
from assistant.models import AssistantJob, AssistantSetting
from children.models import Child
from clinical.models import (AgencyFormTemplate, CaseReferral, OpinionnaireInvite,
                             PsychologicalReport, SelfReportFlag)
from clinical.self_report_model_check import run_model_check

User = get_user_model()

# What the fake model writes. A name in the middle of a sentence (the name check
# does not see one that begins it), a date and a number, none of which any
# source here holds.
INVENTED = "it was reported by Villanueva on 14 March that 77 children were seen"
TELLTALES = ("Villanueva", "14 March", "77")

REFERRAL_TEXT = "Maria was found at the barangay hall with her aunt in San Juan."


def leaked(body):
    """The telltale pieces of INVENTED found anywhere in `body`.

    Each is looked for as a piece of its own: "77" inside a timestamp's
    microseconds ("...:52.477123+08:00", which a serialised flag carries) is
    not the invented number, and read as one it failed this suite about one run
    in fourteen.
    """
    text = body if isinstance(body, str) else json.dumps(body, default=str)
    return [t for t in TELLTALES
            if re.search(rf"(?<![\w.]){re.escape(t)}(?![\w.])", text)]


class StaffDraftingFixture(APITestCase):
    def setUp(self):
        roles = {n: Role.objects.create(role_name=n)
                 for n in (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)}
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=roles[Role.ADMINISTRATOR])
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=roles[Role.PSYCHOLOGIST])
        self.sw = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=roles[Role.STAFF])
        self.child = Child.objects.create(
            fullname="Maria Santos", first_name="Maria", last_name="Santos",
            social_worker=self.sw, assigned_psychologist=self.psy)
        self.referral = CaseReferral.objects.create(
            child=self.child, uploaded_by=self.sw, original_filename="r.pdf",
            extracted_text=REFERRAL_TEXT,
            ai_summary="A summary a person confirmed.", ai_summary_confirmed=True)
        self.report = PsychologicalReport.objects.create(
            child=self.child, author=self.psy, extracted_text="Full report text.")
        cfg = AssistantSetting.load()
        cfg.enabled = True
        cfg.save()
        self.client.force_authenticate(self.sw)

    def model_writes(self, text=INVENTED):
        return patch.object(services.OllamaClient, "generate", return_value=text)

    def model_never_asked(self):
        return patch.object(services.OllamaClient, "generate",
                            side_effect=AssertionError("the model must not be asked"))


class TheRegistryIsCompleteTest(StaffDraftingFixture):
    def test_every_job_type_is_decided(self):
        types = {kind for kind, _ in AssistantJob.TYPE_CHOICES}
        undecided = sorted(types - set(guards.STAFF_DRAFTING))
        self.assertEqual(
            [], undecided,
            f"Job type(s) {undecided} have no entry in assistant/guards.py "
            "STAFF_DRAFTING. Decide how a Staff account is treated for each: REFUSED "
            "(they are shut out), GUARDED (checked_draft() runs on the text) or "
            "TOOL_ONLY (the model only picks a tool) - and map it in EXERCISES below.")

    def test_it_names_no_job_type_that_does_not_exist(self):
        types = {kind for kind, _ in AssistantJob.TYPE_CHOICES}
        self.assertEqual([], sorted(set(guards.STAFF_DRAFTING) - types))

    def test_every_entry_is_one_of_the_three_treatments(self):
        for job_type, treatment in guards.STAFF_DRAFTING.items():
            with self.subTest(job_type):
                self.assertIn(treatment, (guards.REFUSED, guards.GUARDED, guards.TOOL_ONLY))

    def test_every_job_type_is_exercised_below(self):
        types = {kind for kind, _ in AssistantJob.TYPE_CHOICES}
        unmapped = sorted(types - set(EXERCISES))
        self.assertEqual(
            [], unmapped,
            f"Job type(s) {unmapped} have no exercise in EXERCISES. Decide it here: "
            "map the type to the endpoint a Staff account would reach it by, and "
            "say what they must be given.")
        self.assertEqual([], sorted(set(EXERCISES) - types))

    def test_a_guarded_type_has_checks_decided_for_it(self):
        for job_type, treatment in guards.STAFF_DRAFTING.items():
            if treatment == guards.GUARDED:
                with self.subTest(job_type):
                    self.assertIn(job_type, guards.CHECKS)

    def test_a_type_with_checks_is_guarded_not_something_else(self):
        for job_type in guards.CHECKS:
            with self.subTest(job_type):
                self.assertEqual(guards.GUARDED, guards.STAFF_DRAFTING[job_type])


# --- the exercises ---------------------------------------------------------------
#
# Each takes the test case (for its fixtures) and returns what a Staff account
# was observed to get.


def exercise_brief(t):
    """Every door to the written brief, as a social worker."""
    child = t.child.id
    with t.model_never_asked() as generate:
        doors = [t.client.post(f"/api/assistant/brief/child/{child}/"),
                 t.client.get(f"/api/assistant/brief/child/{child}/latest/"),
                 t.client.post("/api/assistant/prefetch-briefs/")]
    t.assertEqual([403, 403, 403], [r.status_code for r in doors])
    generate.assert_not_called()
    t.assertFalse(AssistantJob.objects.exists())
    return guards.REFUSED


def exercise_case_brief(t):
    """There is no route for the written case brief, under either old address."""
    for url in (f"/api/assistant/case-brief/child/{t.child.id}/",
                f"/api/assistant/case-brief/child/{t.child.id}/latest/"):
        with t.assertRaises(Resolver404):
            resolve(url)
        t.assertEqual(404, t.client.post(url).status_code)
    return guards.REFUSED


def exercise_remark_polish(t):
    with t.model_writes() as generate:
        res = t.client.post("/api/assistant/polish-remark/",
                            {"text": "Maria slept well."}, format="json")
    if res.status_code == 200:
        return "open"
    t.assertEqual(403, res.status_code)
    generate.assert_not_called()
    t.assertFalse(AssistantJob.objects.exists())
    return guards.REFUSED


def exercise_doc_intelligence(t):
    with t.model_writes():
        res = t.client.post(f"/api/assistant/summarize-case-referral/{t.referral.id}/")
        report = t.client.post(f"/api/assistant/summarize-report/{t.report.id}/")
    # A psychologist's report is not a social worker's to summarise at all.
    t.assertEqual(403, report.status_code)
    t.referral.refresh_from_db()
    if res.status_code == 200 or leaked(t.referral.ai_summary):
        return "open"
    t.assertEqual(422, res.status_code)
    t.assertEqual({"detail": guards.REFUSAL_SENTENCE}, res.data)
    t.assertEqual("A summary a person confirmed.", t.referral.ai_summary)
    t.assertTrue(t.referral.ai_summary_confirmed)
    t.assertEqual([False], [j.ok for j in AssistantJob.objects.all()])
    return guards.GUARDED


def exercise_census_narrative(t):
    figures = {"period": "monthly", "children_seen": 12, "completed_pre_assessments": 18}
    with t.model_writes():
        res = t.client.post("/api/assistant/census-narrative/", {"figures": figures},
                            format="json")
    if res.status_code == 200:
        return "open"
    t.assertEqual(422, res.status_code)
    t.assertEqual({"detail": guards.REFUSAL_SENTENCE}, res.data)
    t.assertEqual([], leaked(res.data))
    t.assertEqual([False], [j.ok for j in AssistantJob.objects.all()])
    return guards.GUARDED


def exercise_chat(t):
    """The model says anything it likes; the person is shown fixed copy and the
    database's rows. Three ways it can try to speak: prose instead of a tool
    call, a tool that does not exist, and a real tool with an argument nobody
    declared."""
    def ask(message):
        with patch.object(services.OllamaClient, "_post", return_value={"message": message}):
            res = t.client.post("/api/assistant/ask/",
                                {"question": "what is on today?"}, format="json")
        t.assertEqual(200, res.status_code)
        return res.data

    prose = ask({"content": INVENTED})
    nonexistent = ask({"tool_calls": [{"function": {"name": INVENTED, "arguments": {}}}]})
    undeclared = ask({"tool_calls": [{"function": {
        "name": "list_my_appointments",
        "arguments": {"when": "today", "note": INVENTED}}}]})
    # What the panel renders: the message, the echo line and the result. (The
    # refusal's `detail` is the validator's log line and is not drawn.)
    shown = [{k: body.get(k) for k in ("message", "echo", "result", "followups")}
             for body in (prose, nonexistent, undeclared)]
    if any(leaked(s) for s in shown):
        return "open"
    return guards.TOOL_ONLY


def exercise_self_report(t):
    """The model's reason for flagging a child's answer is stored on the flag
    and read by whoever can read the flags: a social worker for their own."""
    template = AgencyFormTemplate.objects.create(
        form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="Self-report",
        fields=[{"label": "Who do you talk to when you are sad?"}])
    invite = OpinionnaireInvite.objects.create(
        child=t.child, template=template, created_by=t.sw,
        status=OpinionnaireInvite.SUBMITTED, submitted_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=7),
        answers={"Who do you talk to when you are sad?": "Nobody, I stay alone."})
    with t.model_writes(f"YES - {INVENTED}"):
        run_model_check(invite.pk)
    # What the social worker is sent for this child.
    body = t.client.get(f"/api/reports/child/{t.child.id}/").data
    flags = body["self_report_flags"]
    t.assertEqual(1, len(flags), "the flag itself must never be lost")
    if leaked(flags):
        return "open"
    t.assertEqual(SelfReportFlag.MODEL, flags[0]["source"])
    t.assertEqual("flagged by the local model", flags[0]["matched"])
    t.assertIn("reason not shown", AssistantJob.objects.get(job_type="self_report").error)
    return guards.GUARDED


# Job type -> how a Staff account would reach it, and what is observed.
EXERCISES = {
    "brief": exercise_brief,
    "case_brief": exercise_case_brief,
    "remark_polish": exercise_remark_polish,
    "doc_intelligence": exercise_doc_intelligence,
    "census_narrative": exercise_census_narrative,
    "chat": exercise_chat,
    "self_report": exercise_self_report,
}


class EveryEntryHoldsForAStaffAccountTest(StaffDraftingFixture):
    def test_each_job_type_behaves_as_its_entry_says(self):
        for job_type, treatment in guards.STAFF_DRAFTING.items():
            with self.subTest(job_type=job_type, claims=treatment):
                # A fresh world for each: the exercises write jobs and flags.
                AssistantJob.objects.all().delete()
                observed = EXERCISES[job_type](self)
                self.assertEqual(
                    treatment, observed,
                    f"STAFF_DRAFTING says {job_type!r} is {treatment!r} for a Staff "
                    f"account; observed {observed!r}. 'open' means model text reached "
                    "them unchecked.")

    def test_the_fake_model_really_does_say_something_every_check_would_refuse(self):
        # The control for every exercise above: INVENTED fails each check
        # against a source that holds none of it.
        for check in (guards.NAMES, guards.DATES, guards.NUMBERS):
            with self.subTest(check), patch.dict(guards.CHECKS, {"_probe": (check,)}):
                with self.assertRaises(guards.DraftRefused):
                    guards.checked_draft("_probe", INVENTED, "Nothing of the kind.")
