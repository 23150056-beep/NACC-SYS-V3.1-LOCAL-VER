"""Every door in the API, and what each kind of account gets at it today.

`route_table.json`, beside this file, holds one entry per route and method
under /api/, and in it one word per identity: what an anonymous caller, the
ISA, a social worker on their own child, a social worker on somebody else's,
the assigned psychologist, an account with no role and an account holding a
role called "Officer" are each told. This test asks every door as every
identity, with real tokens, and compares.

**What it guards.** Roles in this codebase are read as "if psychologist ...
else everything" in a dozen places, and a permission class that lists three
roles by name says nothing about a fourth. A door added next month will have
been decided by whoever wrote it for the accounts they had in mind. So a
route or a method that is not in the table FAILS this test with "decide what
every role gets here", and a cell that moved fails naming the route, the
identity, the old word and the new one. A package that changes a cell on
purpose rewrites the file, and the diff is what gets reviewed. It is the
twelfth call site's guard, the way `STAFF_DRAFTING` is for the assistant's
job types and `KEEPS_IT` is for the relations of a child.

**The words.** `past` (2xx: the request did something), `invalid` (400, 409,
415 or 422: the gate let the request through and the handler turned the body
down; with the `{}` a write carries here this says nothing about whether a real
body would be accepted, only that the permission gate did not stop it),
`refused` (401, 403), `hidden` (404), `verb` (405), `unavailable` (503: the
assistant switched off or unreachable). A GET that answers 200 with a list, or
a dict with a `results` list, adds `:rows` or `:empty`, so "past:empty" and
"past:rows" are different truths. Any other 5xx is not a word: it is a bug, and
the test fails with the traceback rather than recording it.

**How a request is made.** Writes carry `{}`. Each request runs inside its own
savepoint that is rolled back, so one probe never changes what the next one
sees. Tokens are minted the way sign-in mints them
(`LoginSerializer.get_token`), so the real authentication class runs, its
no-role refusal and its password-claim check included. The clock is pinned,
and everything that would leave the machine (texts, email, the model runtime,
the brief prefetch thread) is replaced by a no-op or an unreachable runtime.

The "Officer" role row is made here, in the test database only (`OFFICER`):
the role does not exist yet, and this table is what shows what it would get
the day it does. Since the authentication door refuses an account whose role
the code does not know (accounts/authentication.py), that column is "refused"
at every authenticated door until the role is added to
`accounts.capabilities.EVERY_ROLE`.

The "probe" identity is a role row called "Probe" with `EVERY_ROLE` patched,
for that identity's class only, to include it: an account the code admits
and has put in no other list. It is the detector for an `else` that grants by
default - any cell of its column that is not refused, hidden or empty is a
door that hands an unlisted role somebody else's access, and is explained in
the diff that makes it so.

**What the table does not cover.** Route-level gates only. Field-level and
tool-level doors (the Social Worker field, the custodian, the edit's ask
branch, closure reasons, transfer ends, the chatbot's tools) are pinned by
unit tests in the apps' own test files.

**Rewriting the table.** After a deliberate change:

    ROUTE_TABLE_WRITE=1 python manage.py test config.tests.test_every_route_is_decided

Run it WITHOUT `--parallel`: each identity's test measures its own column and
merges it into the file, so the classes must run in one process. The run
rewrites the whole file (sorted keys, one entry per route and method) and
skips the comparison. `ROUTE_TABLE_TIMING=1` prints how long each identity
took.
"""
import json
import os
import re
import sys
import time
import traceback
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import SimpleTestCase, TestCase
from django.urls import URLResolver, get_resolver
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import capabilities
from accounts.models import Role, User
from accounts.serializers import LoginSerializer
from activity.models import ActivityLog
from assistant.models import AssistantJob
from children.models import AssignmentRequest, Child, TerminationRecord
from clinical.models import (
    AgencyFormTemplate, CaseReferral, ClinicalInterviewRecord, ConsentRecord,
    InstrumentCatalog, OpinionnaireInvite, PreAssessment, ProblemEntry,
    PsychologicalReport, RemarkNote, ResultEntry, SelfReportFlag, TreatmentPlan)
from locations.models import Barangay, Municipality, Province
from scheduling.models import AvailabilityBlock, Appointment, Unavailability

OFFICER = "Officer"
PROBE = "Probe"

TABLE_PATH = Path(__file__).with_name("route_table.json")
WRITE = bool(os.environ.get("ROUTE_TABLE_WRITE"))
TIMING = bool(os.environ.get("ROUTE_TABLE_TIMING"))

IDENTITIES = ("anonymous", "isa", "sw_own", "sw_other", "psy", "norole", "officer",
              "probe")

# Mon 23 Nov 2026, 08:00 in TIME_ZONE. Never the wall clock.
NOW = timezone.make_aware(datetime(2026, 11, 23, 8, 0))
# The Wednesday after it, inside psy's 09:00-12:00 window.
SESSION_DAY = date(2026, 11, 25)

_METHODS = ("get", "post", "put", "patch", "delete")


# ---------------------------------------------------------------- the routes

def _walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from _walk(p.url_patterns, prefix + str(p.pattern))
        else:
            yield prefix + str(p.pattern), p


_KWARG_REGEX = re.compile(r"\(\?P<(\w+)>[^)]*\)")
_CONVERTER = re.compile(r"<\w+:(\w+)>")


def _normalise(raw):
    """`api/^children/(?P<pk>[^/.]+)/terminate/$` and `api/reports/child/
    <int:child_id>/` become `api/children/<pk>/terminate/` and `api/reports/
    child/<child_id>/`. Anything left over that is not a plain path segment is
    a pattern this walker does not understand, and says so."""
    out = raw.replace("^", "").replace("$", "")
    out = _KWARG_REGEX.sub(r"<\1>", out)
    out = _CONVERTER.sub(r"<\1>", out)
    if re.search(r"[^A-Za-z0-9_<>/\-]", out):
        raise AssertionError(f"Cannot normalise the route pattern {raw!r} (got {out!r})")
    return out


_ROUTES = None


def routes():
    """[{key, path, kwargs, method}] for every route and method under api/,
    sorted by key. Format-suffix variants, the routers' API-root pages and
    everything outside /api/ (admin/, healthz/) are left out."""
    global _ROUTES
    if _ROUTES is not None:
        return _ROUTES
    found = {}
    for raw, p in _walk(get_resolver().url_patterns):
        if not raw.startswith("api/") or "format" in raw:
            continue
        cb = p.callback
        cls = getattr(cb, "cls", None) or getattr(cb, "view_class", None)
        if cls is None or cls.__name__ == "APIRootView":
            continue
        path = _normalise(raw)
        actions = getattr(cb, "actions", None)
        if actions is not None:                       # a ViewSet route
            methods = [m for m in actions if m in _METHODS]
        else:                                         # an APIView
            methods = [m for m in _METHODS if hasattr(cls, m)]
        for m in methods:
            key = f"{m.upper()} {path}"
            if key in found:
                raise AssertionError(f"Two routes share the key {key!r}")
            found[key] = {"key": key, "path": path, "method": m,
                          "kwargs": re.findall(r"<(\w+)>", path)}
    _ROUTES = [found[k] for k in sorted(found)]
    return _ROUTES


# ------------------------------------------------------------ the fixtures

def _value_for(kwarg, path, ids):
    """Which fixture a kwarg of a route stands for. An unmapped one fails
    loudly and names itself: a guess here would make the table lie quietly."""
    parts = path.split("/")                 # api, <resource>, ...
    resource = parts[1]
    try:
        if kwarg == "child_id":
            return ids["child_a"]
        if kwarg == "job_id":
            return ids["job"]
        if kwarg == "doc_id":
            return ids[{
                "summarize-report": "report", "confirm-summary": "report",
                "summarize-case-referral": "referral",
                "confirm-case-referral-summary": "referral",
            }[parts[2]]]
        if kwarg == "psgc_code":
            return ids[{"provinces": "province", "municipalities": "municipality",
                        "barangays": "barangay"}[parts[2]]]
        if kwarg == "pk":
            if resource == "users":
                action = parts[3] if len(parts) > 3 else ""
                return ids[{"approve": "pending_user", "decline": "pending_user",
                            "reactivate": "archived_user"}.get(action, "subject")]
            if resource == "children":
                return ids["child_c" if parts[3:4] == ["reopen"] else "child_a"]
            return ids[{
                "assignment-requests": "request", "instruments": "instrument",
                "form-templates": "template", "consents": "consent",
                "interviews": "interview", "problems": "problem",
                "pre-assessments": "pre_assessment", "report-files": "report",
                "remarks": "remark", "treatment-plans": "plan",
                "result-entries": "result", "case-referrals": "referral",
                "opinionnaire-invites": "invite", "opinionnaire": "token",
                "self-report-flags": "flag", "availability": "block",
                "appointments": "appointment", "unavailability": "leave",
            }[resource]]
    except KeyError as exc:
        raise LookupError(
            f"No fixture for <{kwarg}> in {path!r} ({exc}): map it in _value_for") from exc
    raise LookupError(f"No fixture for <{kwarg}> in {path!r}: map it in _value_for")


def _query_for(key, ids):
    """Query strings for the GETs that answer 400 without one, so the probe
    reaches the handler and its own scoping rather than its first guard."""
    return {
        "GET api/availability/slots/": {
            "psychologist": ids["psy"], "date": SESSION_DAY.isoformat(),
            "child": ids["child_a"]},
        "GET api/availability/month/": {
            "psychologist": ids["psy"], "month": "2026-12", "child": ids["child_a"]},
        "GET api/availability/openings/": {"child": ids["child_a"]},
        "GET api/availability/next-slots/": {"child": ids["child_a"]},
        "GET api/availability/psychologists/": {"day": SESSION_DAY.isoformat()},
        "GET api/opinionnaire-invites/templates/": {"child": ids["child_a"]},
        "GET api/children/check-duplicate/": {
            "first_name": "Ana", "last_name": "Cruz", "birth_date": "2015-03-02"},
    }.get(key, {})


class ServerError(Exception):
    """A 5xx: a bug, never a recorded truth."""


def _outcome(method, response):
    status = response.status_code
    if status in (401, 403):
        return "refused"
    if status == 404:
        return "hidden"
    if status == 405:
        return "verb"
    if status == 503:
        return "unavailable"
    if status >= 500:
        raise ServerError(status)
    if status in (400, 409, 415, 422):
        return "invalid"
    if 200 <= status < 300:
        word = "past"
        if (method == "get" and status == 200
                and "json" in response.get("Content-Type", "")):
            body = response.json()
            rows = body if isinstance(body, list) else (
                body.get("results") if isinstance(body, dict) else None)
            if isinstance(rows, list):
                word += ":rows" if rows else ":empty"
        return word
    raise AssertionError(
        f"Unexpected status {status}: decide which word it is in _outcome")


class _Column:
    """One identity's pass over every door. A mixin, so the eight classes
    share the fixtures and `--parallel` can spread them."""

    identity = None
    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_user
        role = lambda name: Role.objects.get_or_create(role_name=name)[0]    # noqa: E731
        admin_r, staff_r, psy_r, officer_r, probe_r = (
            role(Role.ADMINISTRATOR), role(Role.STAFF), role(Role.PSYCHOLOGIST),
            role(OFFICER), role(PROBE))

        def make(name, role_row, **extra):
            return user(email=f"{name}@racco1.gov.ph", username=name,
                        password="pass1234", role=role_row,
                        first_name=name.title(), last_name="Tester", **extra)

        cls.people = {
            "isa": make("isa", admin_r),
            "sw_own": make("sw_own", staff_r),
            "sw_other": make("sw_other", staff_r),
            "psy": make("psy", psy_r),
            "norole": make("norole", None),
            "officer": make("officer", officer_r),
            "probe": make("probe", probe_r),
        }
        sw_own, sw_other, psy, isa = (
            cls.people[k] for k in ("sw_own", "sw_other", "psy", "isa"))
        psy2 = make("psy2", psy_r)
        subject = make("subject", staff_r)
        pending = make("pending", staff_r, status=User.PENDING)
        archived = make("archived", staff_r, status=User.ARCHIVED)

        def child(first, last, holder, **extra):
            return Child.objects.create(
                first_name=first, last_name=last, birth_date=date(2015, 3, 2),
                gender="F", case_type="Foster Care", case_category="Dependent",
                social_worker=holder, **extra)

        a = child("Ana", "Cruz", sw_own, assigned_psychologist=psy)
        b = child("Ben", "Reyes", sw_other)
        c = child("Cora", "Diaz", sw_own, status=Child.INACTIVE,
                  case_status=Child.STAGE_TERMINATED)
        TerminationRecord.objects.create(
            child=c, terminated_by=isa, reason_category="Other", note="Closed.")

        pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        referral = CaseReferral.objects.create(
            child=a, uploaded_by=sw_own, original_filename="referral.pdf",
            file=SimpleUploadedFile("referral.pdf", pdf, "application/pdf"),
            extracted_text="Referred for assessment.")
        report = PsychologicalReport.objects.create(
            child=a, author=psy, original_filename="report.pdf",
            file=SimpleUploadedFile("report.pdf", pdf, "application/pdf"),
            report_type="initial", extracted_text="Initial evaluation.")
        t_ci = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.CLINICAL_INTERVIEW, title="Interview",
            owner=psy, attestation=True, attested_at=NOW,
            fields=[{"id": "q1", "label": "How are you?", "type": "text"}])
        t_sr = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV, title="Self-report",
            owner=psy, attestation=True, attested_at=NOW,
            fields=[{"id": "q1", "label": "Who do you talk to?", "type": "text"}])
        consent = ConsentRecord.objects.create(
            child=a, status=ConsentRecord.SIGNED, signer_name="Lola Cruz",
            signer_relationship="Grandmother", recorded_by=sw_own,
            scan=SimpleUploadedFile("scan.pdf", pdf, "application/pdf"))
        interview = ClinicalInterviewRecord.objects.create(
            child=a, template=t_ci, interviewer=psy, respondent="Child",
            answers={"q1": "Fine."})
        problem = ProblemEntry.objects.create(
            child=a, description="Trouble sleeping.", logged_by=psy)
        remark = RemarkNote.objects.create(child=a, author=psy, text="Settled well.")
        plan = TreatmentPlan.objects.create(
            child=a, author=psy, objectives="Sleep routine.")
        instrument = InstrumentCatalog.objects.create(title="Draw-a-Person", owner=psy)
        pa = PreAssessment.objects.create(
            child=a, psychologist=psy, status=PreAssessment.IN_PROGRESS,
            consent=consent, interview=interview)
        pa.instruments.add(instrument)
        result = ResultEntry.objects.create(
            child=a, pre_assessment=pa, instrument=instrument, entered_by=psy,
            summary="Within range.", classification="Average")
        invite = OpinionnaireInvite.objects.create(
            child=a, template=t_sr, created_by=sw_own,
            expires_at=NOW + timedelta(days=7))
        flag = SelfReportFlag.objects.create(
            invite=invite, child=a, question="Who do you talk to?",
            answer="Nobody", source=SelfReportFlag.LEXICON, matched="nobody")

        block = AvailabilityBlock.objects.create(
            psychologist=psy, weekday=2, start_time="09:00", end_time="12:00",
            capacity=2)
        appointment = Appointment.objects.create(
            child=a, psychologist=psy, booked_by=sw_own,
            start=timezone.make_aware(
                datetime(SESSION_DAY.year, SESSION_DAY.month, SESSION_DAY.day, 10, 0)))
        leave = Unavailability.objects.create(
            psychologist=psy, starts_on=date(2026, 12, 7), ends_on=date(2026, 12, 9),
            reason="Training", created_by=psy)
        request = AssignmentRequest.objects.create(
            child=a, psychologist=psy2, requested_by=sw_own,
            previous_psychologist=psy)

        ActivityLog.objects.create(
            actor=sw_own, recipient=psy, actor_label="Sw_own Tester",
            action=ActivityLog.CREATED, category=ActivityLog.RECORD,
            entity_type="Child", entity_label="Ana Cruz", entity_id=a.id)
        ActivityLog.objects.create(
            actor=isa, actor_label="Isa Tester", action=ActivityLog.UPDATED,
            category=ActivityLog.USER, entity_type="User",
            entity_label="Subject Tester", entity_id=subject.id)
        ActivityLog.objects.create(
            actor=isa, actor_label="Isa Tester", action=ActivityLog.LOGIN,
            category=ActivityLog.SECURITY)
        job = AssistantJob.objects.create(
            job_type="brief", input_ref=f"child:{a.id}", output_text="Brief.",
            model_used="stub", created_by=psy, child=a)
        # setUpTestData runs before setUp patches the clock, so the row was
        # stamped with the real time and "today's brief" (latest/) was answered
        # from whatever day the suite ran on. Pinned to the day before the
        # frozen NOW: a brief that exists, and is not today's.
        AssistantJob.objects.filter(pk=job.pk).update(created_at=NOW - timedelta(days=1))

        province = Province.objects.create(psgc_code="0102800000", name="Ilocos Norte")
        municipality = Municipality.objects.create(
            psgc_code="0102801000", name="Adams", province=province)
        barangay = Barangay.objects.create(
            psgc_code="0102801001", name="Adams (Pob.)", municipality=municipality)

        cls.ids = {
            "child_a": a.id, "child_b": b.id, "child_c": c.id, "job": job.id,
            "invite": invite.id, "token": invite.token, "report": report.id,
            "referral": referral.id, "consent": consent.id,
            "interview": interview.id, "problem": problem.id,
            "remark": remark.id, "plan": plan.id, "result": result.id,
            "pre_assessment": pa.id, "template": t_sr.id,
            "instrument": instrument.id, "request": request.id, "flag": flag.id,
            "block": block.id, "appointment": appointment.id, "leave": leave.id,
            "subject": subject.id, "pending_user": pending.id,
            "archived_user": archived.id, "psy": psy.id,
            "province": province.psgc_code, "municipality": municipality.psgc_code,
            "barangay": barangay.psgc_code,
        }

    def setUp(self):
        # Nothing may leave the machine, and nothing may run outside the
        # test's transaction (a daemon thread has a connection of its own).
        for target, replacement in (
            ("django.utils.timezone.now", lambda: NOW),
            ("accounts.sms.queue_sms", lambda *a, **k: False),
            ("accounts.sms_notifications.queue_sms", lambda *a, **k: False),
            ("accounts.email_notifications._queue", lambda *a, **k: None),
            ("children.notifications._post", lambda *a, **k: False),
            ("assistant.views._start_prefetch_thread", lambda *a, **k: None),
        ):
            p = patch(target, replacement)
            p.start()
            self.addCleanup(p.stop)

        if self.identity == "probe":
            # Admitted by the code, in no other list.
            p = patch.object(capabilities, "EVERY_ROLE",
                             capabilities.EVERY_ROLE + (PROBE,))
            p.start()
            self.addCleanup(p.stop)

        def unreachable(*args, **kwargs):
            raise urllib.error.URLError("no model runtime in this test")

        p = patch("assistant.services.urllib.request.urlopen", unreachable)
        p.start()
        self.addCleanup(p.stop)

    def _client(self):
        client = APIClient()
        # A 5xx comes back as a response (with `exc_info`), not as an
        # exception that ends the run at the first one.
        client.raise_request_exception = False
        if self.identity != "anonymous":
            user = User.objects.select_related("role").get(
                pk=self.people[self.identity].pk)
            # Minted as if the role were known: an account the code does not
            # know is refused at the door, and the door is what is measured
            # (a token issued before the role stopped being known).
            with patch.object(capabilities, "EVERY_ROLE",
                              capabilities.EVERY_ROLE + ((user.role.role_name,) if user.role else ())):
                token = str(LoginSerializer.get_token(user).access_token)
            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def measure(self):
        """{key: word} for this identity, plus the 5xx and the unmapped."""
        client = self._client()
        words, bugs, unmapped = {}, [], []
        for r in routes():
            try:
                path = r["path"]
                for kw in r["kwargs"]:
                    path = path.replace(f"<{kw}>", str(_value_for(kw, r["path"], self.ids)))
            except LookupError as exc:
                unmapped.append(str(exc))
                continue
            cache.clear()
            with transaction.atomic():
                if r["method"] == "get":
                    response = client.get("/" + path, _query_for(r["key"], self.ids))
                else:
                    response = getattr(client, r["method"])(
                        "/" + path, {}, format="json")
                try:
                    words[r["key"]] = _outcome(r["method"], response)
                except ServerError as exc:
                    detail = ""
                    if getattr(response, "exc_info", None):
                        detail = "".join(traceback.format_exception(
                            *response.exc_info)[-4:])
                    bugs.append(f"{r['key']} as {self.identity}: {exc}\n{detail}")
                transaction.set_rollback(True)
        self.requests = len(words) + len(bugs)
        return words, bugs, unmapped

    def test_this_identitys_column(self):
        started = time.perf_counter()
        words, bugs, unmapped = self.measure()
        assert not unmapped, "Unmapped route arguments:\n" + "\n".join(unmapped)
        assert not bugs, "Server errors (bugs, not table entries):\n" + "\n".join(bugs)
        if TIMING:
            print(f"[route table] {self.identity}: {len(words)} requests in "
                  f"{time.perf_counter() - started:.1f}s", file=sys.stderr)

        if WRITE:
            table = json.loads(TABLE_PATH.read_text()) if TABLE_PATH.exists() else {}
            table = {k: v for k, v in table.items() if k in words}
            for key, word in words.items():
                table.setdefault(key, {})[self.identity] = word
            TABLE_PATH.write_text(
                json.dumps(table, indent=1, sort_keys=True) + "\n")
            return

        table = json.loads(TABLE_PATH.read_text())
        problems = []
        for key, word in words.items():
            if key not in table:
                problems.append(
                    f"New route {key}: decide what every role gets here, then "
                    f"add it to route_table.json")
            elif self.identity not in table[key]:
                problems.append(
                    f"{key}: the table has no cell for {self.identity} "
                    f"(measured {word})")
            elif table[key][self.identity] != word:
                problems.append(
                    f"{key} as {self.identity}: the table says "
                    f"{table[key][self.identity]!r}, measured {word!r}")
        for key in table:
            if key not in words:
                problems.append(
                    f"{key} is in route_table.json but no longer exists: "
                    f"remove it")
        assert not problems, "\n".join(problems)


def _column(identity):
    return type(f"RouteColumn_{identity}", (_Column, TestCase), {
        "identity": identity, "__module__": __name__})


# One class per identity, so `--parallel` spreads them.
RouteColumn_anonymous = _column("anonymous")
RouteColumn_isa = _column("isa")
RouteColumn_sw_own = _column("sw_own")
RouteColumn_sw_other = _column("sw_other")
RouteColumn_psy = _column("psy")
RouteColumn_norole = _column("norole")
RouteColumn_officer = _column("officer")
RouteColumn_probe = _column("probe")


class TheTableHasNoStrangers(SimpleTestCase):
    """Cheap, needs no database: the file knows every identity and nothing
    else, so a typo in a column name cannot hide a missing cell."""

    def test_every_cell_names_a_known_identity(self):
        if WRITE:
            self.skipTest("rewriting the table")
        table = json.loads(TABLE_PATH.read_text())
        for key, cells in table.items():
            self.assertLessEqual(set(cells), set(IDENTITIES), key)
