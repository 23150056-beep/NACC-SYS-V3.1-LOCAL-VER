"""The ISA's console, one request (GET /api/admin/overview/).

The ISA is the agency's IT support, so what this answers is a list of things
to do and to check. Three things are held here and not left to the screen:

* Who may ask. Administrators only.
* What it counts. Each of the eight queues is built from a focused fixture
  that includes the near-miss that must NOT be counted, since a count that is
  slightly too generous looks exactly like one that is right.
* What it never carries. A sentinel string is planted in every kind of text the
  endpoint must not return (case study, remark, a child's own answers, what the
  assistant read or wrote, a custodian), and the whole response body is
  searched for each.

Time is pinned with a patch on django.utils.timezone.now (CLAUDE.md: a test
never reads the wall clock). The pinned moment is mid-afternoon in Manila, so
"today" differs from the UTC date for the first eight hours of every local day
and the sign-in tests put a login on each side of that line.
"""
import ast
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import Counter
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts import admin_overview, email_notifications
from accounts.lockout import ADDRESS_LOCK_LABEL, NOT_AN_EMAIL, readable_sign_in_name
from accounts.models import Role, SmsSend, User
from activity.models import ActivityLog
from activity.services import log_activity
from assistant.models import AssistantJob, AssistantSetting
from case_study.models import CaseStudy, CaseStudySection
from children.models import AssignmentRequest, Child
from children.views import ChildViewSet
from clinical.models import (AgencyFormTemplate, CaseReferral, OpinionnaireInvite,
                             RemarkNote, SelfReportFlag)
from scheduling.models import (Appointment, AvailabilityBlock, CustodianReminder,
                               SessionReminder, Unavailability)

URL = "/api/admin/overview/"
MANILA = ZoneInfo("Asia/Manila")
# Wednesday 14 Oct 2026, 3:37 PM in Manila (07:37 UTC).
NOW = datetime(2026, 10, 14, 15, 37, 12, tzinfo=MANILA)
SHA = "486fa753e5836cf8b69b9df8d2cbe4b4676acab8"


def at(days=0, hours=0, minutes=0):
    """A moment this far before the pinned now."""
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


def restamp(model, pk, **fields):
    """Move a row in time. created_at is auto_now_add, which a save cannot
    override and an UPDATE can."""
    model.objects.filter(pk=pk).update(**fields)


class ConsoleCase(TestCase):
    def setUp(self):
        cache.clear()
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        # A test run on Render must not see Render's own commit.
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("RENDER_GIT_COMMIT", None)

        self.roles = {r: Role.objects.create(role_name=r)
                      for r in (Role.ADMINISTRATOR, Role.STAFF, Role.PSYCHOLOGIST)}
        self.admin = self.person("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.editha = self.person("editha@t.ph", "Editha", "Pascua", Role.STAFF)
        self.rosa = self.person("rosa@t.ph", "Rosa", "Santos", Role.STAFF)
        self.bulan = self.person("bulan@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        self.reyes = self.person("reyes@t.ph", "Lino", "Reyes", Role.PSYCHOLOGIST)
        self.client = self.as_user(self.admin)

    # -- builders -----------------------------------------------------------

    def person(self, email, first, last, role=None, **extra):
        return User.objects.create_user(
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last,
            role=self.roles[role] if role else None, **extra)

    def pending(self, email, first, last, asked=None, days=0, **extra):
        user = self.person(email, first, last, None, status=User.PENDING,
                           requested_role=self.roles[asked] if asked else None, **extra)
        restamp(User, user.pk, created_at=at(days=days))
        return user

    def child(self, first="Ana", last="Cruz", **extra):
        return Child.objects.create(first_name=first, last_name=last, **extra)

    def as_user(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def overview(self, client=None):
        response = (client or self.client).get(URL)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data

    def need(self, key, data=None):
        return next(n for n in (data or self.overview())["needs"] if n["key"] == key)

    def health(self, key, data=None):
        return next(h for h in (data or self.overview())["health"] if h["key"] == key)

    def lockout_row(self, email, when=None):
        row = ActivityLog.objects.create(
            actor_label="System", action=ActivityLog.UPDATED, category=ActivityLog.SECURITY,
            entity_type="User",
            entity_label=f"Login locked for {email} after repeated failures")
        if when is not None:
            restamp(ActivityLog, row.pk, created_at=when)
        return row


# ---------------------------------------------------------------------------
# Who may ask
# ---------------------------------------------------------------------------


class WhoMayAskTest(ConsoleCase):
    def test_an_administrator_is_answered(self):
        self.assertEqual(self.client.get(URL).status_code, 200)

    def test_a_social_worker_is_refused(self):
        self.assertEqual(self.as_user(self.editha).get(URL).status_code, 403)

    def test_a_psychologist_is_refused(self):
        self.assertEqual(self.as_user(self.bulan).get(URL).status_code, 403)

    def test_nobody_signed_in_is_asked_to_sign_in(self):
        self.assertEqual(APIClient().get(URL).status_code, 401)

    def test_it_only_reads(self):
        self.assertEqual(self.client.post(URL, {}, format="json").status_code, 405)


# ---------------------------------------------------------------------------
# The shape the screen is built against
# ---------------------------------------------------------------------------


class ShapeTest(ConsoleCase):
    def test_the_top_level_keys(self):
        self.assertEqual(
            set(self.overview()),
            {"generated_at", "needs", "health", "version", "people", "workload",
             "security_log", "security_log_more"})
        self.assertEqual(self.overview()["security_log_more"], [])

    def test_all_eight_queues_come_back_in_order_even_when_empty(self):
        needs = self.overview()["needs"]
        self.assertEqual([n["key"] for n in needs],
                         ["access_requests", "lockouts", "temp_passwords", "stuck_requests",
                          "no_social_worker", "archived_holder", "names_to_fix", "duplicates"])
        self.assertEqual([n["group"] for n in needs],
                         ["people", "people", "people", "records", "records", "records",
                          "records", "records"])
        for n in needs:
            self.assertEqual(n["count"], 0, n["key"])
            self.assertEqual(n["items"], [], n["key"])
            self.assertTrue(n["hint"], n["key"])

    def test_the_six_health_lines_come_back_in_order(self):
        health = self.overview()["health"]
        self.assertEqual([h["key"] for h in health],
                         ["server", "version", "sms", "email", "assistant", "uploads"])
        self.assertEqual([h["test"] for h in health],
                         [None, None, "sms", "email", "assistant", None])
        for h in health:
            self.assertIn(h["status"], {"ok", "warn", "bad", "off"})
            self.assertTrue(h["title"] and h["detail"], h["key"])

    def test_times_carry_the_manila_offset(self):
        self.assertEqual(self.overview()["generated_at"], "2026-10-14T15:37:12+08:00")

    def test_a_queue_shows_at_most_ten_but_counts_them_all(self):
        for i in range(14):
            self.child(f"Kid{i}", "Orphan")
        queue = self.need("no_social_worker")
        self.assertEqual(queue["count"], 14)
        self.assertEqual(len(queue["items"]), 10)


# ---------------------------------------------------------------------------
# needs: the eight queues
# ---------------------------------------------------------------------------


class AccessRequestsTest(ConsoleCase):
    def test_counts_pending_requests_and_names_the_oldest(self):
        self.pending("tito@t.ph", "Tito", "Tan", Role.PSYCHOLOGIST, days=3, email_verified=False)
        self.pending("gina@t.ph", "Gina", "Gomez", Role.STAFF, days=1, email_verified=True,
                     google_sub="sub-1")
        # Neither of these is waiting.
        declined = self.person("no@t.ph", "No", "Thanks", None, status=User.ARCHIVED)
        self.assertIsNone(declined.role)

        queue = self.need("access_requests")
        self.assertEqual(queue["count"], 2)
        self.assertEqual(queue["hint"], "Oldest 3 days · 1 email unconfirmed")
        self.assertEqual(queue["link"], "/users?tab=requests")

    def test_the_longest_wait_is_first_and_each_row_says_how_it_arrived(self):
        self.pending("tito@t.ph", "Tito", "Tan", Role.PSYCHOLOGIST, days=3, email_verified=False)
        self.pending("gina@t.ph", "Gina", "Gomez", Role.STAFF, days=1, email_verified=True,
                     google_sub="sub-1")
        self.pending("nobody@t.ph", "Nia", "None", None, days=0, email_verified=True)
        items = self.need("access_requests")["items"]
        self.assertEqual([i["label"] for i in items], ["Tito Tan", "Gina Gomez", "Nia None"])
        self.assertEqual(items[0]["sub"],
                         "Asked for Psychologist · email not confirmed · 3 days ago")
        self.assertEqual(items[1]["sub"], "Asked for Staff · Google sign-in · 1 day ago")
        self.assertEqual(items[2]["sub"], "No role stated · email confirmed · today")
        self.assertEqual({i["link"] for i in items}, {"/users?tab=requests"})

    def test_an_empty_queue_says_so(self):
        queue = self.need("access_requests")
        self.assertEqual((queue["count"], queue["hint"]), (0, "No one is waiting for access"))


class LockoutsTest(ConsoleCase):
    def test_a_real_lockout_is_counted_from_the_line_the_login_view_writes(self):
        """Through the login view, so the queue and the writer cannot drift
        apart on the label."""
        for _ in range(5):
            APIClient().post("/api/auth/login/",
                             {"email": "ghost@t.ph", "password": "nope"}, format="json")
        queue = self.need("lockouts")
        self.assertEqual(queue["count"], 1)
        self.assertEqual(queue["hint"], "Last 24 hours \u00b7 latest: ghost@t.ph")
        self.assertEqual(queue["items"][0]["label"], "ghost@t.ph")
        self.assertEqual(queue["items"][0]["sub"], "Locked today at 3:37 PM")
        self.assertIsNone(queue["items"][0]["link"])

    def test_only_the_last_24_hours_count(self):
        self.lockout_row("inside@t.ph", at(hours=23))
        self.lockout_row("outside@t.ph", at(hours=25))
        queue = self.need("lockouts")
        self.assertEqual(queue["count"], 1)
        self.assertEqual([i["label"] for i in queue["items"]], ["inside@t.ph"])

    def test_it_links_to_the_account_when_there_is_one_whatever_the_case_typed(self):
        self.lockout_row("EDITHA@T.PH", at(hours=2))
        self.lockout_row("stranger@t.ph", at(hours=3))
        items = self.need("lockouts")["items"]
        self.assertEqual(items[0]["label"], "EDITHA@T.PH")
        self.assertEqual(items[0]["link"], f"/users?edit={self.editha.pk}")
        self.assertIsNone(items[1]["link"])

    def test_newest_first_with_yesterday_named(self):
        self.lockout_row("older@t.ph", at(hours=20))
        self.lockout_row("newer@t.ph", at(hours=1))
        items = self.need("lockouts")["items"]
        self.assertEqual([i["label"] for i in items], ["newer@t.ph", "older@t.ph"])
        self.assertEqual(items[1]["sub"], "Locked yesterday at 7:37 PM")

    def test_other_security_lines_are_not_lockouts(self):
        log_activity(self.editha, ActivityLog.UPDATED, ActivityLog.SECURITY,
                     entity_type="User", entity_label="Changed own password")
        self.assertEqual(self.need("lockouts")["count"], 0)

    def test_a_sign_up_with_a_lockout_in_its_name_is_not_a_lockout(self):
        """The applicant's name is the one part of an audit line a stranger
        writes. Under this name the label of their access request reads
        'Login locked for ceo@racco1.gov.ph after repeated failures — access
        request (asked for: Staff)'; it was counted, and shown as a lockout."""
        APIClient().post("/api/auth/signup/", {
            "first_name": "Login locked for", "last_name": "ceo@racco1.gov.ph after repeated failures",
            "email": "mallory@t.ph", "password": "a-long-enough-passphrase-9",
            "requested_role": Role.STAFF}, format="json")
        self.assertTrue(ActivityLog.objects.filter(
            entity_label__startswith="Login locked for").exists())
        queue = self.need("lockouts")
        self.assertEqual((queue["count"], queue["items"]), (0, []))
        self.assertEqual([e["kind"] for e in self.overview()["security_log"]], ["request"])

    def test_only_the_line_the_login_view_writes_is_a_lockout(self):
        """The same words under another action, another writer or an actor are
        not one."""
        words = "Login locked for ghost@t.ph after repeated failures"
        for action, category, actor, entity_id in (
                (ActivityLog.CREATED, ActivityLog.SECURITY, None, None),
                (ActivityLog.UPDATED, ActivityLog.USER, None, None),
                (ActivityLog.UPDATED, ActivityLog.SECURITY, self.editha, None),
                (ActivityLog.UPDATED, ActivityLog.SECURITY, None, self.editha.pk)):
            ActivityLog.objects.create(
                actor=actor, actor_label="x", action=action, category=category,
                entity_type="User", entity_label=words, entity_id=entity_id)
        self.assertEqual(self.need("lockouts")["count"], 0)
        self.lockout_row("ghost@t.ph")
        self.assertEqual(self.need("lockouts")["count"], 1)

    def test_whatever_was_typed_that_is_not_an_email_is_not_shown(self):
        """Somebody who types their password into the box and fails five times
        used to have it listed on the ISA's Dashboard."""
        typed = ["", "   ", "hunter2", "P@ssw0rd!", "my password is x@y.ph ok",
                 "<script>alert(1)</script>@t.ph", "x" * 200 + "@t.ph", "tab\t@t.ph",
                 "no-dot@localhost"]
        for n, value in enumerate(typed):
            self.lockout_row(value, at(minutes=n))
        self.lockout_row("EDITHA@T.PH", at(hours=3))
        queue = self.need("lockouts")
        self.assertEqual(queue["count"], len(typed) + 1)
        labels = [i["label"] for i in queue["items"]]
        self.assertEqual(labels[:len(typed)], ["an unrecognised sign-in name"] * len(typed))
        self.assertEqual(labels[len(typed)], "EDITHA@T.PH")
        body = str(self.overview())
        for value in typed[2:]:
            self.assertNotIn(value, body)
        # Nor does the unrecognised name look anybody up or link anywhere.
        self.assertEqual([i["link"] for i in queue["items"][:len(typed)]], [None] * len(typed))
        self.assertEqual(queue["items"][len(typed)]["link"], f"/users?edit={self.editha.pk}")
        # The security log says the same.
        texts = [e["text"] for e in self.overview()["security_log"]]
        self.assertIn("Login locked for an unrecognised sign-in name after repeated failures", texts)

    def test_a_password_that_nearly_looks_like_an_address_is_not_kept_either(self):
        """The loosest reading of 'has an @ and a dot after it' kept all of
        these, and with them whatever a person typed into the wrong box. Lines
        already written are judged again when they are read."""
        typed = ["p@ss.word!", "me@x.ph;DROP", "javascript:alert(1)//@x.ph",
                 "a@b.c d", "x@y..z", "x@.y.z", "(x)@y.ph", "x@[1.2.3.4]"]
        for n, value in enumerate(typed):
            self.lockout_row(value, at(minutes=n))
        queue = self.need("lockouts")
        self.assertEqual([i["label"] for i in queue["items"]], [NOT_AN_EMAIL] * len(typed))
        body = str(self.overview())
        for value in typed:
            self.assertNotIn(value, body)

    def test_the_addresses_people_really_have_are_kept(self):
        for typed in ("editha@t.ph", "E.Pascua+demo@racco1.gov.ph", "o'brien@t.ph",
                      "ana_maria@sub-domain.example.co.ph", "peñaranda@t.ph", "ÑANDÚ@T.PH"):
            self.assertEqual(readable_sign_in_name(typed), typed, typed)
        self.assertEqual(readable_sign_in_name("  editha@t.ph "), "editha@t.ph")
        self.assertEqual(readable_sign_in_name(None), NOT_AN_EMAIL)
        self.assertEqual(readable_sign_in_name(["editha@t.ph"]), NOT_AN_EMAIL)

    def test_the_login_view_does_not_write_a_typed_password_into_the_trail(self):
        for _ in range(5):
            APIClient().post("/api/auth/login/",
                             {"email": "correct horse battery", "password": "x"}, format="json")
        line = ActivityLog.objects.get(entity_label__startswith="Login locked for")
        self.assertEqual(line.entity_label,
                         "Login locked for an unrecognised sign-in name after repeated failures")
        self.assertEqual(self.need("lockouts")["items"][0]["label"], "an unrecognised sign-in name")

    def spray(self, emails, times):
        for email in emails:
            for _ in range(times):
                APIClient().post("/api/auth/login/", {"email": email, "password": "nope"},
                                 format="json")

    def test_a_lock_on_the_whole_address_is_a_lockout_of_its_own_kind(self):
        """Twenty failures from one address, none of them five against one
        name: the lock turns away everyone behind the address, the ISA among
        them, and used to be recorded as the lock of the twentieth name typed."""
        self.spray([f"nobody{n}@t.ph" for n in range(20)], 1)
        # And it really is locked: a correct password is refused.
        refused = APIClient().post("/api/auth/login/",
                                   {"email": "admin@t.ph", "password": "pass12345"}, format="json")
        self.assertEqual(refused.status_code, 429)
        (line,) = ActivityLog.objects.filter(entity_label__startswith="Sign-ins from")
        self.assertEqual(line.entity_label, ADDRESS_LOCK_LABEL)
        self.assertFalse(ActivityLog.objects.filter(entity_label__startswith="Login locked").exists())

        queue = self.need("lockouts")
        self.assertEqual(queue["count"], 1)
        self.assertEqual(queue["hint"], "Last 24 hours \u00b7 latest: a whole address")
        (item,) = queue["items"]
        self.assertEqual(item["label"], "Every sign-in from one address")
        self.assertEqual(item["sub"], "Locked today at 3:37 PM \u00b7 everyone behind that address "
                                      "is turned away for 15 minutes")
        self.assertIsNone(item["link"])
        # It names no address and nothing that was typed.
        self.assertNotIn("nobody", str(self.overview()))
        (entry,) = self.overview()["security_log"]
        self.assertEqual((entry["kind"], entry["text"]), ("lockout", ADDRESS_LOCK_LABEL))

    def test_a_spray_that_also_locks_names_counts_both_kinds(self):
        self.spray([f"spray{n}@t.ph" for n in range(4)], 5)
        queue = self.need("lockouts")
        # Four names locked at their fifth failure; the twentieth failure was
        # also the one that locked the address.
        self.assertEqual(queue["count"], 5)
        labels = sorted(i["label"] for i in queue["items"])
        self.assertEqual(labels, ["Every sign-in from one address", "spray0@t.ph", "spray1@t.ph",
                                  "spray2@t.ph", "spray3@t.ph"])

    def test_the_address_line_is_only_the_exact_sentence(self):
        """Another writer's line with other words is not a lockout."""
        for label in (ADDRESS_LOCK_LABEL + " now", "x " + ADDRESS_LOCK_LABEL):
            ActivityLog.objects.create(
                actor_label="System", action=ActivityLog.UPDATED, category=ActivityLog.SECURITY,
                entity_type="User", entity_label=label)
        self.assertEqual(self.need("lockouts")["count"], 0)

    def test_a_sign_up_cannot_write_the_address_line(self):
        APIClient().post("/api/auth/signup/", {
            "first_name": "Sign-ins from one", "last_name": "address locked after repeated failures",
            "email": "mallory@t.ph", "password": "a-long-enough-passphrase-9",
            "requested_role": Role.STAFF}, format="json")
        self.assertEqual(self.need("lockouts")["count"], 0)


class TempPasswordsTest(ConsoleCase):
    def test_created_reset_and_unrecorded_accounts_are_all_listed(self):
        created = self.client.post("/api/users/", {
            "email": "nina@t.ph", "first_name": "Nina", "last_name": "New",
            "role": self.roles[Role.PSYCHOLOGIST].pk}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        nina = User.objects.get(email="nina@t.ph")
        log = ActivityLog.objects.get(category=ActivityLog.USER, entity_id=nina.pk,
                                      action=ActivityLog.CREATED)
        restamp(ActivityLog, log.pk, created_at=at(days=3))

        reset = self.client.post(f"/api/users/{self.editha.pk}/reset-password/")
        self.assertEqual(reset.status_code, 200, reset.data)

        # Flagged with nothing in the audit trail to say when.
        User.objects.filter(pk=self.rosa.pk).update(must_change_password=True)
        # An archived account is not waiting on anything.
        gone = self.person("gone@t.ph", "Gone", "Away", Role.STAFF, status=User.ARCHIVED,
                           must_change_password=True)
        self.assertTrue(gone.must_change_password)

        queue = self.need("temp_passwords")
        self.assertEqual(queue["count"], 3)
        self.assertEqual(queue["hint"], "Oldest issued 3 days ago")
        self.assertEqual([i["label"] for i in queue["items"]],
                         ["Nina New", "Editha Pascua", "Rosa Santos"])
        self.assertEqual(queue["items"][0]["sub"], "Psychologist · issued 3 days ago")
        self.assertEqual(queue["items"][1]["sub"], "Staff · issued today")
        self.assertEqual(queue["items"][2]["sub"], "Staff · issue date not recorded")
        self.assertEqual(queue["items"][0]["link"], f"/users?edit={nina.pk}")

    def test_the_people_list_carries_the_same_dates(self):
        self.client.post(f"/api/users/{self.editha.pk}/reset-password/")
        User.objects.filter(pk=self.rosa.pk).update(must_change_password=True)
        rows = {r["id"]: r for r in self.overview()["people"]["temp_passwords"]}
        self.assertEqual(rows[self.editha.pk]["since"], "2026-10-14T15:37:12+08:00")
        self.assertEqual(rows[self.editha.pk]["role"], "Staff")
        self.assertIsNone(rows[self.rosa.pk]["since"])

    def test_a_reactivated_account_counts_from_the_day_it_came_back(self):
        old = self.person("old@t.ph", "Old", "Hand", Role.STAFF, status=User.ARCHIVED)
        done = self.client.post(f"/api/users/{old.pk}/reactivate/")
        self.assertEqual(done.status_code, 200, done.data)
        item = self.need("temp_passwords")["items"][0]
        self.assertEqual((item["label"], item["sub"]), ("Old Hand", "Staff · issued today"))

    def test_an_edit_of_an_account_named_like_a_reset_is_not_an_issue_date(self):
        """The date is found by the kind the reset line was written with. Read
        from the end of its words, an ordinary edit of this account (whose
        label is just its name) counted as a reset."""
        nina = self.person("nina@t.ph", "Nina", "Temp \u2014 password reset", Role.STAFF,
                           must_change_password=True)
        saved = self.client.put(f"/api/users/{nina.pk}/", {
            "email": nina.email, "first_name": "Nina", "last_name": nina.last_name,
            "role": self.roles[Role.STAFF].pk}, format="json")
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertTrue(ActivityLog.objects.filter(
            entity_id=nina.pk, entity_label__endswith=" \u2014 password reset").exists())
        self.assertEqual(self.need("temp_passwords")["items"][0]["sub"],
                         "Staff \u00b7 issue date not recorded")

    def test_nobody_waiting(self):
        queue = self.need("temp_passwords")
        self.assertEqual((queue["count"], queue["hint"]),
                         (0, "Everyone has chosen their own password"))


class StuckRequestsTest(ConsoleCase):
    def ask(self, child, who=None, status=AssignmentRequest.PENDING, days=0, **extra):
        req = AssignmentRequest.objects.create(
            child=child, psychologist=who or self.bulan, requested_by=self.editha,
            status=status, **extra)
        restamp(AssignmentRequest, req.pk, created_at=at(days=days))
        return req

    def test_unanswered_for_a_week_and_declined_and_forgotten_are_the_stuck_ones(self):
        silent = self.child("Silent", "Wait", social_worker=self.editha)
        self.ask(silent, days=8)
        recent = self.child("Recent", "Ask", social_worker=self.editha)
        self.ask(recent, days=2)

        refused = self.child("Refused", "Nobody", social_worker=self.editha)
        reason = "x" * 100
        declined = self.ask(refused, status=AssignmentRequest.DECLINED, days=5, reason=reason)
        restamp(AssignmentRequest, declined.pk, decided_at=at(days=3))

        # Declined, then asked again: the new question is open, not stuck.
        asked_again = self.child("Again", "Asked", social_worker=self.editha)
        before = self.ask(asked_again, status=AssignmentRequest.DECLINED, days=6)
        restamp(AssignmentRequest, before.pk, decided_at=at(days=5))
        self.ask(asked_again, who=self.reyes, days=1)

        # Declined, but already has a psychologist (a transfer that was refused).
        holder = self.child("Held", "Already", social_worker=self.editha,
                            assigned_psychologist=self.reyes)
        self.ask(holder, status=AssignmentRequest.DECLINED, days=4)

        # A closed case: nothing left to ask.
        closed = self.child("Closed", "Case", status=Child.INACTIVE)
        self.ask(closed, days=10)

        queue = self.need("stuck_requests")
        self.assertEqual(queue["count"], 2)
        self.assertEqual(queue["hint"],
                         "1 unanswered for 7+ days · 1 declined with nobody asked again")
        self.assertEqual(queue["link"], "/children")
        first, second = queue["items"]
        self.assertEqual(first["label"], f"C-{silent.pk:04d} · Silent Wait")
        self.assertEqual(first["sub"], "Asked M. Bulan 8 days ago")
        self.assertEqual(first["link"], f"/children?edit={silent.pk}")
        self.assertEqual(second["label"], f"C-{refused.pk:04d} · Refused Nobody")
        # Who and how long ago, never why: the reason is the psychologist's
        # own words and is read on the child's record.
        self.assertEqual(second["sub"], "Declined by M. Bulan 3 days ago")

    def test_a_decline_says_who_and_when_and_never_why(self):
        child = self.child("Short", "Reason")
        reason = "ZZ-DECLINE-REASON-ZZ Caseload is full"
        # No answer date on the row (an older one): it is dated by when it was asked.
        self.ask(child, status=AssignmentRequest.DECLINED, days=2, reason=reason)
        response = self.client.get(URL)
        sub = self.need("stuck_requests", response.data)["items"][0]["sub"]
        self.assertEqual(sub, "Declined by M. Bulan 2 days ago")
        self.assertNotIn("ZZ-DECLINE-REASON-ZZ", response.content.decode())

    def test_a_decline_today_reads_today(self):
        child = self.child("Fresh", "Decline")
        refused = self.ask(child, status=AssignmentRequest.DECLINED, days=1, reason="Full")
        restamp(AssignmentRequest, refused.pk, decided_at=at(days=0))
        self.assertEqual(self.need("stuck_requests")["items"][0]["sub"],
                         "Declined by M. Bulan today")

    def test_a_withdrawn_request_before_the_decline_does_not_hide_it(self):
        """The latest request is the one judged, whatever came before."""
        child = self.child("Order", "Matters")
        self.ask(child, status=AssignmentRequest.WITHDRAWN, days=9)
        refused = self.ask(child, status=AssignmentRequest.DECLINED, days=4,
                           reason="Not my area")
        restamp(AssignmentRequest, refused.pk, decided_at=at(days=3))
        self.assertEqual(self.need("stuck_requests")["count"], 1)

    def test_nothing_stuck(self):
        queue = self.need("stuck_requests")
        self.assertEqual((queue["count"], queue["hint"]),
                         (0, "Every request is answered or recent"))


class NoSocialWorkerTest(ConsoleCase):
    def test_active_records_nobody_holds(self):
        orphan = self.child("Cara", "Diaz", case_type="Foster Care")
        restamp(Child, orphan.pk, created_at=at(days=4))
        self.child("Held", "Fine", social_worker=self.editha)
        self.child("Closed", "Orphan", status=Child.INACTIVE)

        queue = self.need("no_social_worker")
        self.assertEqual(queue["count"], 1)
        self.assertEqual(queue["hint"], "Only you can open them until a social worker is assigned")
        item = queue["items"][0]
        self.assertEqual(item["label"], f"C-{orphan.pk:04d} · Cara Diaz")
        self.assertEqual(item["sub"], "Foster Care · added 4 days ago")
        self.assertEqual(item["link"], f"/children?edit={orphan.pk}")

    def test_a_record_without_a_case_type(self):
        self.child("No", "Type")
        self.assertEqual(self.need("no_social_worker")["items"][0]["sub"],
                         "No case type · added today")


class ArchivedHolderTest(ConsoleCase):
    def test_an_active_record_held_by_an_account_that_cannot_sign_in(self):
        gone = self.person("gone@t.ph", "Old", "Worker", Role.STAFF, status=User.ARCHIVED)
        stranded = self.child("Stranded", "Child", social_worker=gone)
        self.child("Fine", "Child", social_worker=self.editha)
        self.child("Closed", "Child", social_worker=gone, status=Child.INACTIVE)
        self.child("Nobody", "Child")                       # the other queue's business

        queue = self.need("archived_holder")
        self.assertEqual(queue["count"], 1)
        self.assertEqual(queue["hint"], "Held by 1 deactivated account")
        item = queue["items"][0]
        self.assertEqual(item["label"], f"C-{stranded.pk:04d} · Stranded Child")
        self.assertEqual(item["sub"], "Social worker Old Worker (inactive)")
        self.assertEqual(item["link"], f"/children?edit={stranded.pk}")

    def test_the_hint_counts_accounts_not_records(self):
        gone = self.person("gone@t.ph", "Old", "Worker", Role.STAFF, status=User.ARCHIVED)
        for n in range(3):
            self.child(f"K{n}", "Stranded", social_worker=gone)
        queue = self.need("archived_holder")
        self.assertEqual((queue["count"], queue["hint"]), (3, "Held by 1 deactivated account"))

    def test_a_record_assigned_to_a_psychologist_who_can_no_longer_sign_in(self):
        """The same stranding as a social worker's: archiving the account moves
        nothing, and the child stays assigned to someone who cannot open a
        page. Nine children of an archived psychologist once vanished from the
        console altogether."""
        gone = self.person("psy@t.ph", "Old", "Psych", Role.PSYCHOLOGIST, status=User.ARCHIVED)
        stranded = self.child("Stranded", "Child", assigned_psychologist=gone,
                              social_worker=self.editha)
        self.child("Fine", "Child", assigned_psychologist=self.bulan, social_worker=self.editha)
        self.child("Closed", "Child", assigned_psychologist=gone, status=Child.INACTIVE)
        pending = self.person("pend@t.ph", "Not", "Yet", Role.PSYCHOLOGIST, status=User.PENDING)
        self.child("Waiting", "Child", assigned_psychologist=pending)   # not active either

        queue = self.need("archived_holder")
        self.assertEqual(queue["count"], 2)
        self.assertEqual(queue["hint"], "Held by 2 deactivated accounts")
        by_label = {i["label"]: i for i in queue["items"]}
        item = by_label[f"C-{stranded.pk:04d} \u00b7 Stranded Child"]
        self.assertEqual(item["sub"], "Psychologist Old Psych (inactive)")
        self.assertEqual(item["link"], f"/children?edit={stranded.pk}")

    def test_a_record_with_both_holders_gone_is_one_record_with_both_named(self):
        social = self.person("gone@t.ph", "Old", "Worker", Role.STAFF, status=User.ARCHIVED)
        psych = self.person("psy@t.ph", "Old", "Psych", Role.PSYCHOLOGIST, status=User.ARCHIVED)
        self.child("Both", "Gone", social_worker=social, assigned_psychologist=psych)
        queue = self.need("archived_holder")
        self.assertEqual((queue["count"], queue["hint"]), (1, "Held by 2 deactivated accounts"))
        self.assertEqual(queue["items"][0]["sub"],
                         "Social worker Old Worker (inactive) \u00b7 Psychologist Old Psych (inactive)")


class NamesToFixTest(ConsoleCase):
    def test_accounts_whose_name_lost_a_letter(self):
        broken_user = self.person("pe@t.ph", "Ana", "PE�AMORA", Role.STAFF)
        # Out of scope: a deactivated account and a good name.
        self.person("old@t.ph", "Old", "Br�ken", Role.STAFF, status=User.ARCHIVED)
        self.person("good@t.ph", "Peña", "Ñandú", Role.STAFF)

        queue = self.need("names_to_fix")
        self.assertEqual(queue["count"], 1)
        self.assertEqual(queue["hint"],
                         "1 account with a letter that did not save; retype the name")
        (user,) = queue["items"]
        self.assertEqual((user["label"], user["sub"], user["link"]),
                         ("Ana PE�AMORA", "Account", f"/users?edit={broken_user.pk}"))

    def test_a_child_record_is_not_listed_because_its_name_cannot_be_retyped(self):
        """The record form locks a child's name once the record exists, for
        everyone, so a row for one would open a form that cannot fix it and the
        queue could never empty."""
        broken = self.child("Juan", "PE�A", social_worker=self.editha)
        Child.objects.create(fullname="Cara Di�z")
        queue = self.need("names_to_fix")
        self.assertEqual((queue["count"], queue["items"]), (0, []))
        self.assertEqual(queue["hint"], "No account name has an unreadable letter")
        # The premise: the one place the record could be corrected refuses.
        refused = self.client.patch(f"/api/children/{broken.pk}/", {"last_name": "PEÑA"},
                                    format="json")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("last_name", refused.data)


class DuplicatesTest(ConsoleCase):
    def test_groups_by_the_rule_intake_uses(self):
        birth = date(2015, 3, 2)
        # The same child, typed differently: spacing, case and a decomposed ñ.
        one = self.child("Ana", "Peña", birth_date=birth)
        two = self.child(" ana ", "Peña", birth_date=birth, status=Child.INACTIVE)
        trio = [self.child("Ben", "Lim", birth_date=date(2014, 1, 1)) for _ in range(3)]
        # Not repeats: another birth date, no birth date, a blank last name.
        self.child("Ana", "Peña", birth_date=date(2015, 3, 3))
        self.child("No", "Birth")
        self.child("No", "Birth")
        self.child("Cara", "", birth_date=birth)
        self.child("Cara", "", birth_date=birth)

        queue = self.need("duplicates")
        self.assertEqual(queue["count"], 2)
        self.assertEqual(queue["hint"], "2 children with more than one record (5 records)")
        self.assertEqual(queue["link"], "/children")
        pair, three = queue["items"]
        self.assertEqual(pair["label"], "Ana Peña · born Mar 2, 2015")
        # The second record is closed, and the row says so: Records opens on its
        # Active tab, and the link asks for all of them.
        self.assertEqual(pair["sub"], f"C-{one.pk:04d} and C-{two.pk:04d} (closed)")
        self.assertEqual(pair["link"], "/children?q=Pe%C3%B1a&status=all")
        self.assertEqual(three["label"], "Ben Lim · born Jan 1, 2014")
        self.assertEqual(three["sub"],
                         f"C-{trio[0].pk:04d}, C-{trio[1].pk:04d} and C-{trio[2].pk:04d}")

    def test_the_link_finds_every_record_of_the_child_whatever_the_middle_name(self):
        """Records filters on a plain substring of the full name. The full name
        of one record is not in the other when only one has a middle name."""
        birth = date(2015, 3, 2)
        self.child("Ana", "Peña", middle_name="M.", birth_date=birth)
        self.child("Ana", "Peña", birth_date=birth)
        link = self.need("duplicates")["items"][0]["link"]
        wanted = parse_qs(urlsplit(link).query)["q"][0].lower()
        names = [c.fullname.lower() for c in Child.objects.all()]
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertIn(wanted, name)

    def test_the_link_asks_for_the_last_name_as_records_will_compare_it(self):
        """Records compares a plain substring, so the link carries the last name
        composed (NFC) and with one space between words, whatever way the first
        record happens to have stored it. Records folds the names it compares
        the same way."""
        birth = date(2015, 3, 2)
        self.child("Ana", "Pen\u0303a  de  la Cruz", birth_date=birth)      # decomposed, doubled spaces
        self.child("Ana", "Pe\u00f1a de la Cruz", birth_date=birth)
        (item,) = self.need("duplicates")["items"]
        query = parse_qs(urlsplit(item["link"]).query)
        self.assertEqual(query["q"], ["Pe\u00f1a de la Cruz"])
        self.assertEqual(query["status"], ["all"])

    def test_an_all_active_group_is_not_marked_closed(self):
        birth = date(2015, 3, 2)
        one = self.child("Ana", "Peña", birth_date=birth)
        two = self.child("Ana", "Peña", birth_date=birth)
        (item,) = self.need("duplicates")["items"]
        self.assertEqual(item["sub"], f"C-{one.pk:04d} and C-{two.pk:04d}")

    def test_no_repeats(self):
        self.child("Solo", "Child", birth_date=date(2015, 3, 2))
        queue = self.need("duplicates")
        self.assertEqual((queue["count"], queue["hint"]),
                         (0, "No child has more than one record"))


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


class ServerHealthTest(ConsoleCase):
    def test_it_names_the_database_it_reached_and_times_the_answer(self):
        line = self.health("server")
        self.assertEqual(line["status"], "ok")
        # Whichever engine the suite runs on: the line names it, and where it is.
        self.assertRegex(line["detail"], r"^Up · \w+ \(.+\) · answered in \d+ ms$")
        self.assertIn(connection.vendor, line["detail"])


class VersionTest(ConsoleCase):
    def test_render_says_which_commit(self):
        os.environ["RENDER_GIT_COMMIT"] = SHA
        with patch.object(admin_overview, "STARTED_AT", at(hours=5)):
            data = self.overview()
        self.assertEqual(data["version"], {
            "api_commit": SHA, "api_started_at": "2026-10-14T10:37:12+08:00",
            "source": "render"})
        line = self.health("version", data)
        self.assertEqual((line["status"], line["detail"]),
                         ("ok", "API 486fa75 · up since 10:37 AM"))

    def test_a_process_up_since_another_day_says_the_day(self):
        os.environ["RENDER_GIT_COMMIT"] = SHA
        with patch.object(admin_overview, "STARTED_AT", at(days=2, hours=3)):
            line = self.health("version")
        self.assertEqual(line["detail"], "API 486fa75 · up since Oct 12, 12:37 PM")

    def _repo(self, tmp):
        root = Path(tmp)
        (root / "backend").mkdir()
        (root / ".git").mkdir()
        return root

    def _ask(self, root):
        """What a process started in this checkout would report: the commit is
        read once, as the module is imported, and the request only repeats it."""
        with override_settings(BASE_DIR=root / "backend"):
            commit = admin_overview._git_commit()
        with patch.object(admin_overview, "BOOT_COMMIT", commit):
            return self.overview()

    def test_the_commit_is_the_one_the_process_started_on_not_the_checkouts_now(self):
        """A development server keeps running the code it loaded, however many
        commits are made afterwards; the web build reads its own commit once
        as well, so a request that re-read the files would make the two differ
        for no reason."""
        with patch.object(admin_overview, "BOOT_COMMIT", SHA):
            with patch.object(admin_overview, "_git_commit",
                              side_effect=AssertionError("read the checkout per request")):
                data = self.overview()
        self.assertEqual((data["version"]["api_commit"], data["version"]["source"]), (SHA, "git"))

    def test_without_render_it_reads_the_branch_file_of_the_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            (root / ".git" / "refs" / "heads").mkdir(parents=True)
            (root / ".git" / "refs" / "heads" / "main").write_text(SHA + "\n")
            data = self._ask(root)
        self.assertEqual((data["version"]["api_commit"], data["version"]["source"]), (SHA, "git"))
        self.assertEqual(self.health("version", data)["status"], "ok")

    def test_it_falls_back_to_packed_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            (root / ".git" / "packed-refs").write_text(
                "# pack-refs with: peeled fully-peeled sorted\n"
                f"{'0' * 40} refs/heads/other\n{SHA} refs/heads/main\n^{'1' * 40}\n")
            data = self._ask(root)
        self.assertEqual(data["version"]["api_commit"], SHA)

    def test_a_detached_head_is_the_commit_itself(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / ".git" / "HEAD").write_text(SHA + "\n")
            data = self._ask(root)
        self.assertEqual((data["version"]["api_commit"], data["version"]["source"]), (SHA, "git"))

    def test_a_linked_worktree_follows_its_pointer_and_the_shared_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work"
            (root / "backend").mkdir(parents=True)
            shared = Path(tmp) / "main.git"
            (shared / "refs" / "heads").mkdir(parents=True)
            (shared / "refs" / "heads" / "feature").write_text(SHA + "\n")
            tree = shared / "worktrees" / "work"
            tree.mkdir(parents=True)
            (tree / "HEAD").write_text("ref: refs/heads/feature\n")
            (tree / "commondir").write_text("../..\n")
            (root / ".git").write_text(f"gitdir: {tree}\n")
            data = self._ask(root)
        self.assertEqual((data["version"]["api_commit"], data["version"]["source"]), (SHA, "git"))

    def test_no_commit_anywhere_is_unknown_and_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "backend").mkdir()
            data = self._ask(root)
        self.assertEqual(data["version"]["api_commit"], None)
        self.assertEqual(data["version"]["source"], "unknown")
        line = self.health("version", data)
        self.assertEqual(line["status"], "warn")
        self.assertTrue(line["detail"].startswith("API version unknown · up since "))

    def test_garbage_in_the_ref_file_is_unknown_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
            (root / ".git" / "refs" / "heads").mkdir(parents=True)
            (root / ".git" / "refs" / "heads" / "main").write_text("not a sha\n")
            data = self._ask(root)
        self.assertEqual(data["version"]["source"], "unknown")


class SmsHealthTest(ConsoleCase):
    def appointment(self):
        return Appointment.objects.create(child=self.child(), psychologist=self.bulan, start=NOW)

    def reminder(self, day, sent=None, claimed=None):
        row = SessionReminder.objects.create(psychologist=self.bulan, day=day, session_count=1)
        restamp(SessionReminder, row.pk, sent_at=sent, claimed_at=claimed or sent or NOW)

    def custodian(self, sent=None, claimed=None):
        row = CustodianReminder.objects.create(appointment=self.appointment())
        restamp(CustodianReminder, row.pk, sent_at=sent, claimed_at=claimed or sent or NOW)

    @override_settings(SMS_PROVIDER="console")
    def test_off_when_texts_only_go_to_the_log(self):
        line = self.health("sms")
        self.assertEqual((line["status"], line["detail"], line["test"]),
                         ("off", "Not set up - texts go to the server log", "sms"))

    @override_settings(SMS_PROVIDER="")
    def test_an_empty_provider_is_the_same_as_none(self):
        self.assertEqual(self.health("sms")["status"], "off")

    @override_settings(SMS_PROVIDER="semaphor", SMS_API_KEY="k")
    def test_a_misspelt_provider_is_a_fault_not_silence(self):
        line = self.health("sms")
        self.assertEqual(line["status"], "bad")
        self.assertIn("'semaphor'", line["detail"])

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="")
    def test_a_gateway_with_no_key_is_a_fault(self):
        line = self.health("sms")
        self.assertEqual(line["status"], "bad")
        self.assertIn("SMS_API_KEY is not set", line["detail"])

    def spend(self, outcome="sent", credits=1, **fields):
        row = SmsSend.objects.create(
            kind="session_reminder", route="normal", credits=credits, outcome=outcome,
            number_hash="h" * 64, number_tail="0000")
        restamp(SmsSend, row.pk, **fields)
        return row

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="k", SMS_DAILY_CREDIT_LIMIT=300)
    def test_quiet_day_says_so(self):
        line = self.health("sms")
        self.assertEqual((line["status"], line["detail"]),
                         ("ok", "Semaphore · 0 texts, about 0 credits in the last "
                                "24 hours (limit 300 a day)"))

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="k", SMS_DAILY_CREDIT_LIMIT=300)
    def test_counts_texts_and_credits_in_the_last_day(self):
        self.spend(credits=1, created_at=at(hours=1))
        self.spend(credits=2, created_at=at(hours=3))
        self.spend(credits=1, created_at=at(hours=30))           # too old to count
        line = self.health("sms")
        self.assertEqual((line["status"], line["detail"]),
                         ("ok", "Semaphore · 2 texts, about 3 credits in the last "
                                "24 hours (limit 300 a day)"))

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="k", SMS_DAILY_CREDIT_LIMIT=300)
    def test_unconfirmed_and_stopped_texts_are_counted_apart(self):
        self.spend("sent", 1, created_at=at(hours=1))
        self.spend("unknown", 1, created_at=at(hours=2))
        self.spend("limited", 0, created_at=at(hours=2))
        self.spend("limited", 0, created_at=at(hours=2))
        self.spend("refused", 0, created_at=at(hours=2))         # went nowhere
        line = self.health("sms")
        self.assertEqual(line["detail"],
                         "Semaphore · 2 texts, about 2 credits in the last 24 hours "
                         "(limit 300 a day); 1 unconfirmed; 2 stopped by a limit")
        self.assertEqual(line["status"], "ok")

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="k", SMS_DAILY_CREDIT_LIMIT=10)
    def test_amber_from_eighty_percent_red_at_the_limit(self):
        self.spend(credits=7, created_at=at(hours=1))
        self.assertEqual(self.health("sms")["status"], "ok")
        self.spend(credits=1, created_at=at(hours=1))
        self.assertEqual(self.health("sms")["status"], "warn")   # 8 of 10
        self.spend(credits=2, created_at=at(hours=1))
        line = self.health("sms")
        self.assertEqual(line["status"], "bad")                  # 10 of 10
        self.assertIn("daily limit is reached", line["detail"])

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="k", SMS_DAILY_CREDIT_LIMIT=300)
    def test_a_claim_never_stamped_sent_is_a_warning(self):
        self.reminder(date(2026, 10, 13), sent=at(hours=1))
        self.reminder(date(2026, 10, 14), claimed=at(minutes=20))   # stopped partway
        self.custodian(claimed=at(minutes=5))                       # still sending
        line = self.health("sms")
        self.assertEqual(line["status"], "warn")
        self.assertIn("1 reminder not confirmed sent", line["detail"])

    @override_settings(SMS_PROVIDER="philsms", SMS_API_KEY="k")
    def test_other_gateways_are_named(self):
        self.assertTrue(self.health("sms")["detail"].startswith("PhilSMS · "))


class EmailHealthTest(ConsoleCase):
    @override_settings(BREVO_API_KEY="")
    def test_off_without_a_key(self):
        line = self.health("email")
        self.assertEqual((line["status"], line["detail"], line["test"]),
                         ("off", "Not set up - emails go to the server log", "email"))

    @override_settings(BREVO_API_KEY="secret-key", BREVO_SENDER_EMAIL="racco@t.ph")
    def test_on_with_a_key_and_never_shows_it(self):
        response = self.client.get(URL)
        line = self.health("email", response.data)
        self.assertEqual((line["status"], line["detail"]), ("ok", "Brevo · sends from racco@t.ph"))
        self.assertNotIn("secret-key", response.content.decode())


class AssistantHealthTest(ConsoleCase):
    def test_local_by_default_and_reading_it_creates_nothing(self):
        self.assertFalse(AssistantSetting.objects.exists())
        line = self.health("assistant")
        self.assertEqual((line["status"], line["detail"], line["test"]),
                         ("ok", "On · local model qwen2.5:3b-instruct", "assistant"))
        self.assertFalse(AssistantSetting.objects.exists())

    def test_off_when_the_switch_is_off(self):
        AssistantSetting.objects.create(enabled=False)
        line = self.health("assistant")
        self.assertEqual((line["status"], line["detail"]), ("off", "Switched off in Settings"))

    def test_the_model_the_administrator_chose(self):
        AssistantSetting.objects.create(enabled=True, model_name="llama3:8b")
        self.assertEqual(self.health("assistant")["detail"], "On · local model llama3:8b")

    @override_settings(ASSISTANT_ALLOW_HOSTED_MODEL=True, ASSISTANT_MODEL_URL="https://m.example/v1",
                       ASSISTANT_MODEL_TOKEN="tok-secret", ASSISTANT_MODEL_NAME="scout-17b")
    def test_hosted_says_so_and_never_shows_the_token(self):
        response = self.client.get(URL)
        self.assertEqual(self.health("assistant", response.data)["detail"],
                         "On · hosted model scout-17b")
        self.assertNotIn("tok-secret", response.content.decode())


class UploadsHealthTest(ConsoleCase):
    @override_settings(DEBUG=False)
    def test_local_disk_in_production_is_a_warning(self):
        line = self.health("uploads")
        self.assertEqual(line["status"], "warn")
        self.assertEqual(line["detail"], "Local disk, which a host wipes on every deploy · no files yet")

    @override_settings(DEBUG=True)
    def test_local_disk_while_developing_is_fine(self):
        line = self.health("uploads")
        self.assertEqual((line["status"], line["detail"]), ("ok", "Local disk · no files yet"))

    @override_settings(DEBUG=True)
    def test_the_newest_upload_is_the_newest_of_the_three_kinds(self):
        old = CaseReferral(child=self.child(), original_filename="a.pdf")
        old.file.save("a.pdf", ContentFile(b"%PDF-1.4"), save=True)
        restamp(CaseReferral, old.pk, created_at=at(days=3))
        new = CaseReferral(child=self.child("Ben", "Lim"), original_filename="b.pdf")
        new.file.save("b.pdf", ContentFile(b"%PDF-1.4"), save=True)
        restamp(CaseReferral, new.pk, created_at=at(hours=2))
        self.assertEqual(self.health("uploads")["detail"],
                         "Local disk · newest file today at 1:37 PM")

    def _s3(self, **options):
        storages = {"default": {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": options},
                    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
        return override_settings(STORAGES=storages, DEBUG=False)

    def test_cloudflare_r2_is_named_with_its_bucket_but_not_its_account(self):
        with self._s3(bucket_name="nacc-v3-media",
                      endpoint_url="https://abc123acct.r2.cloudflarestorage.com"):
            response = self.client.get(URL)
        line = self.health("uploads", response.data)
        self.assertEqual((line["status"], line["detail"]),
                         ("ok", "Cloudflare R2 · bucket nacc-v3-media · no files yet"))
        self.assertNotIn("abc123acct", response.content.decode())

    def test_object_storage_with_no_bucket_is_a_fault(self):
        with self._s3(bucket_name=None):
            line = self.health("uploads")
        self.assertEqual(line["status"], "bad")


# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------


class PeopleTest(ConsoleCase):
    def login(self, user, when):
        row = ActivityLog.objects.create(
            actor=user, actor_label="x", action=ActivityLog.LOGIN, category=ActivityLog.SECURITY)
        restamp(ActivityLog, row.pk, created_at=when)

    def test_roles_count_only_active_accounts(self):
        self.person("gone@t.ph", "Gone", "Away", Role.STAFF, status=User.ARCHIVED)
        self.pending("wait@t.ph", "Wait", "Ing", Role.STAFF)
        self.assertEqual(self.overview()["people"]["roles"],
                         {"Administrator": 1, "Staff": 2, "Psychologist": 2})

    def test_roles_are_always_all_three(self):
        User.objects.exclude(pk=self.admin.pk).delete()
        self.assertEqual(self.overview()["people"]["roles"],
                         {"Administrator": 1, "Staff": 0, "Psychologist": 0})

    def test_signed_in_today_is_judged_on_the_manila_date_not_the_utc_one(self):
        # 23:59 the evening before, and 00:01 this morning: the latter is still
        # the 13th in UTC and must count as today here.
        self.login(self.editha, datetime(2026, 10, 13, 23, 59, tzinfo=MANILA))
        self.login(self.rosa, datetime(2026, 10, 14, 0, 1, tzinfo=MANILA))
        self.login(self.bulan, datetime(2026, 10, 14, 8, 0, tzinfo=MANILA))
        self.login(self.bulan, datetime(2026, 10, 14, 12, 30, tzinfo=MANILA))
        gone = self.person("gone@t.ph", "Gone", "Away", Role.STAFF, status=User.ARCHIVED)
        self.login(gone, datetime(2026, 10, 14, 9, 0, tzinfo=MANILA))
        rows = self.overview()["people"]["signed_in_today"]
        self.assertEqual(rows, [
            {"id": self.bulan.pk, "name": "Marivic Bulan", "role": "Psychologist",
             "at": "2026-10-14T12:30:00+08:00"},
            {"id": self.rosa.pk, "name": "Rosa Santos", "role": "Staff",
             "at": "2026-10-14T00:01:00+08:00"},
        ])

    def test_dormant_means_old_enough_and_no_sign_in_for_thirty_days(self):
        for user in (self.editha, self.rosa, self.bulan, self.reyes):
            restamp(User, user.pk, created_at=at(days=60))
        self.login(self.editha, at(days=10))      # signed in recently: not dormant
        self.login(self.rosa, at(days=35))        # long ago: dormant
        self.login(self.rosa, at(days=40))
        # bulan never signed in: dormant, with no date.
        restamp(User, self.reyes.pk, created_at=at(days=10))   # too new to be dormant
        old_gone = self.person("gone@t.ph", "Gone", "Away", Role.STAFF, status=User.ARCHIVED)
        restamp(User, old_gone.pk, created_at=at(days=90))
        rows = self.overview()["people"]["dormant"]
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(set(by_name), {"Rosa Santos", "Marivic Bulan"})
        self.assertEqual(by_name["Rosa Santos"]["last_sign_in"], "2026-09-09T15:37:12+08:00")
        self.assertIsNone(by_name["Marivic Bulan"]["last_sign_in"])
        self.assertEqual(by_name["Marivic Bulan"]["role"], "Psychologist")
        # Never signed in comes first.
        self.assertEqual(rows[0]["name"], "Marivic Bulan")

    def test_the_boundary_day_is_not_dormant_yet(self):
        restamp(User, self.editha.pk, created_at=at(days=60))
        self.login(self.editha, at(days=29, hours=23))
        names = [r["name"] for r in self.overview()["people"]["dormant"]]
        self.assertNotIn("Editha Pascua", names)


# ---------------------------------------------------------------------------
# workload
# ---------------------------------------------------------------------------


class WorkloadTest(ConsoleCase):
    def test_each_psychologist_with_their_caseload_and_open_questions(self):
        for n in range(2):
            self.child(f"Mine{n}", "Kid", assigned_psychologist=self.bulan,
                       social_worker=self.editha)
        self.child("Closed", "Kid", assigned_psychologist=self.bulan, status=Child.INACTIVE)
        waiting = self.child("Waiting", "Kid", social_worker=self.editha)
        AssignmentRequest.objects.create(child=waiting, psychologist=self.bulan,
                                         requested_by=self.editha)
        AssignmentRequest.objects.create(child=self.child("Said", "No"), psychologist=self.bulan,
                                         requested_by=self.editha,
                                         status=AssignmentRequest.DECLINED)
        self.person("old@t.ph", "Old", "Psych", Role.PSYCHOLOGIST, status=User.ARCHIVED)

        rows = self.overview()["workload"]["psychologists"]
        self.assertEqual(rows, [
            {"id": self.bulan.pk, "name": "Marivic Bulan", "active": 2, "pending": 1,
             "inactive_account": False},
            {"id": self.reyes.pk, "name": "Lino Reyes", "active": 0, "pending": 0,
             "inactive_account": False},
        ])

    def test_a_deactivated_psychologist_still_holding_children_is_listed_and_counted(self):
        """Their children are in care. Left out of the list, the total was
        nine short and nothing said why."""
        gone = self.person("old@t.ph", "Old", "Psych", Role.PSYCHOLOGIST, status=User.ARCHIVED)
        for n in range(3):
            self.child(f"Held{n}", "Kid", assigned_psychologist=gone)
        self.child("Closed", "Kid", assigned_psychologist=gone, status=Child.INACTIVE)
        self.person("older@t.ph", "Older", "Psych", Role.PSYCHOLOGIST, status=User.ARCHIVED)

        rows = {r["name"]: r for r in self.overview()["workload"]["psychologists"]}
        self.assertEqual(set(rows), {"Marivic Bulan", "Lino Reyes", "Old Psych"})
        self.assertEqual((rows["Old Psych"]["active"], rows["Old Psych"]["inactive_account"]),
                         (3, True))
        self.assertFalse(rows["Marivic Bulan"]["inactive_account"])

    def test_both_lists_run_heaviest_first_then_by_name(self):
        a = self.person("a@t.ph", "Ana", "Abad", Role.PSYCHOLOGIST)
        z = self.person("z@t.ph", "Zed", "Zamora", Role.PSYCHOLOGIST)
        for who, n in ((self.reyes, 1), (z, 3), (a, 3), (self.bulan, 0)):
            for i in range(n):
                self.child(f"P{who.pk}{i}", "Kid", assigned_psychologist=who)
        # One waiting on an answer counts with the load, as the bar draws it.
        AssignmentRequest.objects.create(child=self.child("Ask", "Kid"), psychologist=self.bulan,
                                         requested_by=self.editha)
        AssignmentRequest.objects.create(child=self.child("Ask2", "Kid"), psychologist=self.bulan,
                                         requested_by=self.editha)
        names = [r["name"] for r in self.overview()["workload"]["psychologists"]]
        self.assertEqual(names, ["Ana Abad", "Zed Zamora", "Marivic Bulan", "Lino Reyes"])

        for who, n in ((self.rosa, 4), (self.editha, 1)):
            for i in range(n):
                self.child(f"S{who.pk}{i}", "Kid", social_worker=who)
        quiet = self.person("q@t.ph", "Ana", "Quiet", Role.STAFF)
        names = [r["name"] for r in self.overview()["workload"]["social_workers"]]
        self.assertEqual(names, ["Rosa Santos", "Editha Pascua", quiet.fullname])

    def test_social_workers_with_records_plus_active_ones_with_none(self):
        gone = self.person("gone@t.ph", "Old", "Worker", Role.STAFF, status=User.ARCHIVED)
        self.person("gone2@t.ph", "Older", "Worker", Role.STAFF, status=User.ARCHIVED)
        self.child("A", "Kid", social_worker=self.editha)
        self.child("B", "Kid", social_worker=gone)
        self.child("C", "Kid", social_worker=gone, status=Child.INACTIVE)

        rows = {r["name"]: r for r in self.overview()["workload"]["social_workers"]}
        self.assertEqual(set(rows), {"Editha Pascua", "Rosa Santos", "Old Worker"})
        self.assertEqual(rows["Editha Pascua"], {"id": self.editha.pk, "name": "Editha Pascua",
                                                 "active": 1, "inactive_account": False})
        self.assertEqual(rows["Rosa Santos"]["active"], 0)
        self.assertEqual((rows["Old Worker"]["active"], rows["Old Worker"]["inactive_account"]),
                         (1, True))

    def test_unassigned_counts_active_records_with_no_psychologist(self):
        self.child("A", "Kid")
        self.child("B", "Kid", social_worker=self.editha)
        self.child("C", "Kid", assigned_psychologist=self.bulan)
        self.child("D", "Kid", status=Child.INACTIVE)
        self.assertEqual(self.overview()["workload"]["unassigned"], 2)

    def test_psychologists_who_cannot_be_booked_at_all(self):
        today = date(2026, 10, 14)
        fixed = self.person("fixed@t.ph", "Fixed", "Hours", Role.PSYCHOLOGIST)
        future = self.person("future@t.ph", "Future", "Date", Role.PSYCHOLOGIST)
        past = self.person("past@t.ph", "Past", "Date", Role.PSYCHOLOGIST)
        off = self.person("off@t.ph", "Window", "Off", Role.PSYCHOLOGIST)
        block = dict(start_time="08:00", end_time="12:00")
        AvailabilityBlock.objects.create(psychologist=fixed, weekday=1, **block)
        AvailabilityBlock.objects.create(psychologist=future, date=today, **block)
        AvailabilityBlock.objects.create(psychologist=past, date=today - timedelta(days=1), **block)
        AvailabilityBlock.objects.create(psychologist=off, weekday=2, active=False, **block)
        rows = self.overview()["workload"]["no_availability"]
        # By surname: the two with no window at all, a window that has passed,
        # and one that is switched off. A fixed day and a future date are fine.
        self.assertEqual([r["name"] for r in rows],
                         ["Marivic Bulan", "Past Date", "Window Off", "Lino Reyes"])
        self.assertEqual(rows[0]["id"], self.bulan.pk)
        self.assertEqual(fixed.availability_blocks.count() + future.availability_blocks.count(), 2)

    def test_leave_in_the_next_week(self):
        today = date(2026, 10, 14)

        def away(who, start, end):
            return Unavailability.objects.create(psychologist=who, starts_on=start, ends_on=end)

        away(self.bulan, today + timedelta(days=6), today + timedelta(days=8))   # starts on the last day
        away(self.reyes, today + timedelta(days=7), today + timedelta(days=9))   # a day too late
        current = self.person("now@t.ph", "Right", "Now", Role.PSYCHOLOGIST)
        away(current, today - timedelta(days=2), today + timedelta(days=1))      # under way
        ended = self.person("ended@t.ph", "Just", "Back", Role.PSYCHOLOGIST)
        away(ended, today - timedelta(days=5), today - timedelta(days=1))        # ended yesterday
        retired = self.person("ret@t.ph", "Old", "Psych", Role.PSYCHOLOGIST, status=User.ARCHIVED)
        away(retired, today, today + timedelta(days=1))                          # no longer here

        rows = self.overview()["workload"]["on_leave"]
        self.assertEqual(rows, [
            {"id": current.pk, "name": "Right Now", "start": "2026-10-12", "end": "2026-10-15"},
            {"id": self.bulan.pk, "name": "Marivic Bulan", "start": "2026-10-20", "end": "2026-10-22"},
        ])


# ---------------------------------------------------------------------------
# security log
# ---------------------------------------------------------------------------


class SecurityLogTest(ConsoleCase):
    def kinds(self):
        return [e["kind"] for e in self.overview()["security_log"]]

    def test_every_kind_comes_from_the_line_its_own_endpoint_writes(self):
        """Each action is done through the real endpoint, then read back, so a
        reworded label in accounts/views.py fails here instead of quietly
        turning into 'other'."""
        role = self.roles[Role.PSYCHOLOGIST]
        # created
        self.client.post("/api/users/", {"email": "nina@t.ph", "first_name": "Nina",
                                         "last_name": "New", "role": role.pk}, format="json")
        # role changed
        self.client.put(f"/api/users/{self.editha.pk}/", {
            "email": self.editha.email, "first_name": "Editha", "last_name": "Pascua",
            "role": role.pk}, format="json")
        # a plain edit
        self.client.put(f"/api/users/{self.rosa.pk}/", {
            "email": self.rosa.email, "first_name": "Rosa", "last_name": "Santos-Reyes",
            "role": self.roles[Role.STAFF].pk}, format="json")
        # access approved, access declined
        yes = self.pending("yes@t.ph", "Yes", "Please", Role.STAFF, email_verified=True)
        no = self.pending("no@t.ph", "No", "Thanks", Role.STAFF, email_verified=True)
        self.client.post(f"/api/users/{yes.pk}/approve/",
                         {"role": self.roles[Role.STAFF].pk}, format="json")
        self.client.post(f"/api/users/{no.pk}/decline/")
        # reset, archived, reactivated
        self.client.post(f"/api/users/{self.reyes.pk}/reset-password/")
        self.client.post(f"/api/users/{self.reyes.pk}/archive/")
        self.client.post(f"/api/users/{self.reyes.pk}/reactivate/")
        # an access request typed on the sign-up page
        APIClient().post("/api/auth/signup/", {
            "first_name": "Maria", "last_name": "Santos", "email": "maria@t.ph",
            "password": "a-long-enough-passphrase-9", "requested_role": Role.STAFF},
            format="json")
        # own password changed
        self.as_user(self.bulan).post("/api/auth/change-password/", {
            "current_password": "pass12345", "new_password": "a-new-long-passphrase-9"},
            format="json")
        # a lockout, and an export
        for _ in range(5):
            APIClient().post("/api/auth/login/", {"email": "ghost@t.ph", "password": "x"},
                             format="json")
        self.client.post("/api/children/exported/", {"rows": 12}, format="json")
        # an assistant read
        AssistantJob.objects.create(job_type="brief", input_ref="child:1",
                                    created_by=self.bulan, child=self.child())

        log = self.overview()["security_log"]
        self.assertEqual(
            {e["kind"] for e in log},
            {"created", "role", "other", "approval", "decline", "reset", "archive",
             "reactivate", "request", "password", "lockout", "export", "assistant_read"})
        def line(kind):
            return [e for e in log if e["kind"] == kind]

        def only(kind):
            found = line(kind)
            self.assertEqual(len(found), 1, f"{kind}: {found}")
            return found[0]

        self.assertEqual(only("role")["text"],
                         "Changed the role of Editha Pascua from Staff to Psychologist")
        self.assertEqual(only("role")["actor"], "Ada Admin")
        self.assertEqual(only("approval")["text"],
                         "Approved Yes Please as Staff (asked for: Staff)")
        self.assertEqual(only("decline")["text"],
                         "Declined the access request from No Thanks (asked for: Staff)")
        self.assertEqual(only("reset")["text"], "Issued a temporary password to Lino Reyes")
        self.assertEqual(only("reactivate")["text"], "Reactivated Lino Reyes")
        self.assertEqual(only("archive")["text"], "Deactivated Lino Reyes")
        self.assertEqual(only("created")["text"], "Created the account for Nina New")
        # The plain edit reads as an edit; the role change is its own line only.
        self.assertEqual([e["text"] for e in line("other")],
                         ["Edited the account of Rosa Santos-Reyes"])
        self.assertEqual(only("request")["text"],
                         "Access request from Maria Santos (asked for: Staff)")
        self.assertEqual(only("request")["actor"], "System")
        self.assertEqual(only("lockout")["actor"], "System")
        self.assertEqual(only("lockout")["text"],
                         "Login locked for ghost@t.ph after repeated failures")
        self.assertEqual(only("export")["text"], "Exported the Records list (CSV, 12 rows)")
        self.assertEqual(only("export")["actor"], "Ada Admin")
        self.assertEqual(only("password")["actor"], "Marivic Bulan")

    TAILS = (
        " \u2014 access approved as Administrator (asked for: Administrator)",
        " \u2014 access request declined (asked for: Staff)",
        " \u2014 access request (asked for: Staff)",
        " \u2014 role changed from Staff to Administrator",
        " \u2014 password reset",
        " \u2014 account reactivated",
    )

    def signup(self, email, first, last):
        response = APIClient().post("/api/auth/signup/", {
            "first_name": first, "last_name": last, "email": email,
            "password": "a-long-enough-passphrase-9", "requested_role": Role.STAFF},
            format="json")
        self.assertEqual(response.status_code, 202, response.data)

    def test_a_sign_up_named_like_an_approval_is_still_only_a_request(self):
        """Signing up as 'Mallory Evil \u2014 access approved as Administrator
        (asked for: Administrator)' put an 'Approved ...' line, with the green
        approval icon, into the ISA's log."""
        self.signup("mallory@t.ph", "Mallory", "Evil" + self.TAILS[0])
        (line,) = self.overview()["security_log"]
        self.assertEqual((line["kind"], line["actor"]), ("request", "System"))
        self.assertEqual(
            line["text"],
            "Access request from Mallory Evil" + self.TAILS[0] + " (asked for: Staff)")

    def test_an_edit_of_an_account_named_like_any_other_line_is_an_edit(self):
        for n, tail in enumerate(self.TAILS):
            user = self.person(f"evil{n}@t.ph", "Evil", f"Name{n}{tail}", Role.STAFF)
            saved = self.client.put(f"/api/users/{user.pk}/", {
                "email": user.email, "first_name": "Evil", "last_name": user.last_name,
                "role": self.roles[Role.STAFF].pk}, format="json")
            self.assertEqual(saved.status_code, 200, saved.data)
        log = self.overview()["security_log"]
        self.assertEqual(Counter(e["kind"] for e in log), {"other": len(self.TAILS)})
        for entry in log:
            self.assertTrue(entry["text"].startswith("Edited the account of Evil Name"), entry)

    def test_deactivating_an_account_named_like_a_decline_is_a_deactivation(self):
        user = self.person("evil@t.ph", "Evil", "Name" + self.TAILS[1], Role.STAFF)
        self.client.post(f"/api/users/{user.pk}/archive/")
        (line,) = self.overview()["security_log"]
        self.assertEqual(line["kind"], "archive")

    def test_the_real_lines_for_an_account_with_such_a_name_keep_their_own_kind(self):
        """The name is whatever it is; the kind is what the endpoint did, and the
        facts in the sentence come from the end of the label."""
        pending = self.pending("p@t.ph", "Pat", "Evil" + self.TAILS[3], Role.STAFF,
                               email_verified=True)
        self.client.post(f"/api/users/{pending.pk}/approve/",
                         {"role": self.roles[Role.STAFF].pk}, format="json")
        pat = User.objects.get(pk=pending.pk)
        self.client.put(f"/api/users/{pat.pk}/", {
            "email": pat.email, "first_name": "Pat", "last_name": pat.last_name,
            "role": self.roles[Role.PSYCHOLOGIST].pk}, format="json")
        self.client.post(f"/api/users/{pat.pk}/reset-password/")
        by_kind = {e["kind"]: e["text"] for e in self.overview()["security_log"]}
        name = pat.fullname
        self.assertEqual(by_kind["approval"], f"Approved {name} as Staff (asked for: Staff)")
        self.assertEqual(by_kind["role"],
                         f"Changed the role of {name} from Staff to Psychologist")
        self.assertEqual(by_kind["reset"], f"Issued a temporary password to {name}")

    def test_lines_written_before_kinds_existed_are_not_guessed_at(self):
        """A USER line with no kind is told apart by its action alone, whatever
        its words say."""
        for action, text in ((ActivityLog.UPDATED, "Edited the account of "),
                             (ActivityLog.ARCHIVED, "Deactivated "),
                             (ActivityLog.CREATED, "Created the account for ")):
            ActivityLog.objects.create(
                actor=self.admin, actor_label="Ada Admin", action=action,
                category=ActivityLog.USER, entity_type="User", entity_id=self.editha.pk,
                entity_label="Editha Pascua" + self.TAILS[0])
        texts = sorted(e["text"] for e in self.overview()["security_log"])
        self.assertEqual(texts, sorted([
            "Created the account for Editha Pascua" + self.TAILS[0],
            "Deactivated Editha Pascua" + self.TAILS[0],
            "Edited the account of Editha Pascua" + self.TAILS[0]]))

    def test_plain_sign_ins_and_the_phone_verification_are_left_out(self):
        APIClient().post("/api/auth/login/", {"email": "editha@t.ph", "password": "pass12345"},
                         format="json")
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.LOGIN).exists())
        log_activity(self.editha, ActivityLog.UPDATED, ActivityLog.SECURITY,
                     entity_type="User", entity_label="Verified own mobile number")
        self.assertEqual(self.overview()["security_log"], [])

    def test_a_week_back_newest_first(self):
        old = ActivityLog.objects.create(
            actor_label="Ada Admin", action=ActivityLog.ARCHIVED, category=ActivityLog.USER,
            entity_type="User", entity_label="Eight Days")
        restamp(ActivityLog, old.pk, created_at=at(days=8))
        edge = ActivityLog.objects.create(
            actor_label="Ada Admin", action=ActivityLog.ARCHIVED, category=ActivityLog.USER,
            entity_type="User", entity_label="Six Days")
        restamp(ActivityLog, edge.pk, created_at=at(days=6))
        mid = ActivityLog.objects.create(
            actor_label="Ada Admin", action=ActivityLog.ARCHIVED, category=ActivityLog.USER,
            entity_type="User", entity_label="Two Days")
        restamp(ActivityLog, mid.pk, created_at=at(days=2))
        texts = [e["text"] for e in self.overview()["security_log"]]
        self.assertEqual(texts, ["Deactivated Two Days", "Deactivated Six Days"])

    def test_an_account_named_like_the_phone_check_is_not_hidden_from_the_log(self):
        """The phone check is left out by its words, but only as the security
        line it is; an edit of an account called that is an edit."""
        user = self.person("v@t.ph", "Verified own", "mobile number", Role.STAFF)
        ActivityLog.objects.create(
            actor=self.admin, actor_label="Ada Admin", action=ActivityLog.UPDATED,
            category=ActivityLog.USER, entity_type="User", entity_id=user.pk,
            entity_label="Verified own mobile number")
        self.assertEqual(len(self.overview()["security_log"]), 1)

    def test_record_events_are_not_security_events(self):
        log_activity(self.editha, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="Child", entity_label="Ana Cruz")
        self.assertEqual(self.overview()["security_log"], [])

    def test_at_most_thirty_and_a_flood_of_assistant_reads_is_cut_to_five(self):
        for n in range(25):
            row = ActivityLog.objects.create(
                actor_label="Ada Admin", action=ActivityLog.ARCHIVED,
                category=ActivityLog.USER, entity_type="User", entity_label=f"User {n}")
            restamp(ActivityLog, row.pk, created_at=at(hours=n + 20))
        child = self.child()
        for n in range(15):
            job = AssistantJob.objects.create(job_type="brief", input_ref="child:1",
                                              created_by=self.bulan, child=child)
            restamp(AssistantJob, job.pk, created_at=at(hours=n))
        data = self.overview()
        log = data["security_log"]
        self.assertEqual(len(log), 30)
        stamps = [e["at"] for e in log]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        # The five newest reads, then the 25 audit rows; the other ten reads
        # are counted, not listed.
        self.assertEqual([e["kind"] for e in log[:5]], ["assistant_read"] * 5)
        self.assertEqual(Counter(e["kind"] for e in log), {"assistant_read": 5, "archive": 25})
        self.assertEqual(data["security_log_more"], [
            {"kind": "assistant_read", "count": 10, "text": "and 10 more assistant reads not shown"}])

    def test_a_psychologist_at_work_cannot_push_the_administrators_own_actions_off_the_log(self):
        """Forty briefs in forty hours is an ordinary week for one psychologist
        (the limit is twenty an hour). They filled the thirty rows and the ISA's
        approval, reset and role change three days ago were not in the log, with
        nothing to say so."""
        yes = self.pending("yes@t.ph", "Yes", "Please", Role.STAFF, email_verified=True)
        self.client.post(f"/api/users/{yes.pk}/approve/",
                         {"role": self.roles[Role.STAFF].pk}, format="json")
        self.client.post(f"/api/users/{self.rosa.pk}/reset-password/")
        self.client.put(f"/api/users/{self.editha.pk}/", {
            "email": self.editha.email, "first_name": "Editha", "last_name": "Pascua",
            "role": self.roles[Role.PSYCHOLOGIST].pk}, format="json")
        ActivityLog.objects.filter(category=ActivityLog.USER).update(created_at=at(days=3))
        child = self.child()
        for n in range(40):
            job = AssistantJob.objects.create(job_type="brief", input_ref="child:1",
                                              created_by=self.bulan, child=child)
            restamp(AssistantJob, job.pk, created_at=at(hours=n))
        data = self.overview()
        kinds = Counter(e["kind"] for e in data["security_log"])
        self.assertEqual(kinds["assistant_read"], 5)
        self.assertEqual((kinds["approval"], kinds["reset"], kinds["role"]), (1, 1, 1))
        self.assertEqual(data["security_log_more"], [
            {"kind": "assistant_read", "count": 35, "text": "and 35 more assistant reads not shown"}])

    def test_a_survey_read_answer_by_answer_counts_as_one_line(self):
        child = self.child()
        for n in range(30):
            job = AssistantJob.objects.create(job_type="self_report", input_ref=f"invite:{n // 10}",
                                              child=child)
            restamp(AssistantJob, job.pk, created_at=at(minutes=n))
        for n in range(4):
            job = AssistantJob.objects.create(job_type="brief", input_ref="child:1",
                                              created_by=self.bulan, child=child)
            restamp(AssistantJob, job.pk, created_at=at(hours=n + 1))
        data = self.overview()
        # Three surveys and four briefs are seven lines, five shown.
        self.assertEqual(len(data["security_log"]), 5)
        self.assertEqual(data["security_log_more"], [
            {"kind": "assistant_read", "count": 2, "text": "and 2 more assistant reads not shown"}])

    def test_older_events_beyond_the_thirty_say_how_many(self):
        for n in range(40):
            row = ActivityLog.objects.create(
                actor_label="Ada Admin", action=ActivityLog.ARCHIVED,
                category=ActivityLog.USER, entity_type="User", entity_label=f"User {n}")
            restamp(ActivityLog, row.pk, created_at=at(hours=n))
        data = self.overview()
        self.assertEqual(len(data["security_log"]), 30)
        self.assertEqual(data["security_log"][0]["text"], "Deactivated User 0")
        self.assertEqual(data["security_log_more"],
                         [{"kind": "older", "count": 10, "text": "and 10 older events not shown"}])

    def test_one_older_event_is_singular(self):
        for n in range(31):
            row = ActivityLog.objects.create(
                actor_label="Ada Admin", action=ActivityLog.ARCHIVED,
                category=ActivityLog.USER, entity_type="User", entity_label=f"User {n}")
            restamp(ActivityLog, row.pk, created_at=at(hours=n))
        self.assertEqual(self.overview()["security_log_more"][0]["text"],
                         "and 1 older event not shown")

    def test_a_bulk_line_cut_by_the_thirty_is_counted_too(self):
        """A bulk kind keeps its newest five, and the thirty-line cut can then
        drop those as well when the administrator's own lines are newer. They
        are said, not forgotten."""
        for n in range(30):
            row = ActivityLog.objects.create(
                actor_label="Ada Admin", action=ActivityLog.ARCHIVED,
                category=ActivityLog.USER, entity_type="User", entity_label=f"User {n}")
            restamp(ActivityLog, row.pk, created_at=at(hours=n + 1))
        for n in range(8):
            row = ActivityLog.objects.create(
                actor=self.bulan, actor_label="Marivic Bulan", action=ActivityLog.EXPORTED,
                category=ActivityLog.SECURITY, entity_type="Child",
                entity_label=f"Exported the Records list (CSV, {n} rows)")
            restamp(ActivityLog, row.pk, created_at=at(days=5, minutes=n))
        data = self.overview()
        shown = Counter(e["kind"] for e in data["security_log"])
        self.assertEqual(sum(shown.values()), 30)
        # The exports are the oldest lines, so none made the cut at all: all
        # eight are left out, and the audit rows are the thirty shown.
        self.assertEqual(shown, {"archive": 30})
        more = {m["kind"]: m["count"] for m in data["security_log_more"]}
        self.assertEqual(more, {"export": 8})

    def test_a_role_change_is_one_line_not_two(self):
        """The change of role has a line of its own that names both roles; an
        'edited the account' line for the same save said less."""
        self.client.put(f"/api/users/{self.editha.pk}/", {
            "email": self.editha.email, "first_name": "Editha", "last_name": "Pascua",
            "role": self.roles[Role.PSYCHOLOGIST].pk}, format="json")
        log = self.overview()["security_log"]
        self.assertEqual([e["kind"] for e in log], ["role"])
        self.assertEqual(ActivityLog.objects.filter(category=ActivityLog.USER,
                                                    entity_id=self.editha.pk).count(), 1)

    def test_a_role_change_that_also_changes_the_name_keeps_both_lines(self):
        self.client.put(f"/api/users/{self.editha.pk}/", {
            "email": self.editha.email, "first_name": "Editha", "last_name": "Pascua-Reyes",
            "role": self.roles[Role.PSYCHOLOGIST].pk}, format="json")
        self.assertEqual(sorted(e["kind"] for e in self.overview()["security_log"]),
                         ["other", "role"])

    def test_an_edit_that_changes_nothing_is_still_logged_as_an_edit(self):
        self.client.put(f"/api/users/{self.editha.pk}/", {
            "email": self.editha.email, "first_name": "Editha", "last_name": "Pascua",
            "role": self.roles[Role.STAFF].pk}, format="json")
        self.assertEqual([e["kind"] for e in self.overview()["security_log"]], ["other"])

    def test_a_plain_edit_is_one_edit_line(self):
        self.client.patch(f"/api/users/{self.editha.pk}/", {"first_name": "Edith"}, format="json")
        self.assertEqual([e["text"] for e in self.overview()["security_log"]],
                         ["Edited the account of Edith Pascua"])


class BulkLinesTest(ConsoleCase):
    """Exports, lockouts and access requests can be written as fast as someone
    likes (a signed-in account, or a stranger). Each shows its newest five and
    says how many more there are, so none can push everything else off a log of
    thirty lines. Thirty exports from one psychologist used to leave the log
    holding nothing else, and the ISA's actions with it."""

    def exports(self, how_many, who=None):
        for n in range(how_many):
            row = ActivityLog.objects.create(
                actor=who or self.bulan, actor_label="Marivic Bulan",
                action=ActivityLog.EXPORTED, category=ActivityLog.SECURITY,
                entity_type="Child", entity_label=f"Exported the Records list (CSV, {n} rows)")
            restamp(ActivityLog, row.pk, created_at=at(minutes=how_many - n))

    def requests(self, how_many):
        for n in range(how_many):
            row = ActivityLog.objects.create(
                actor_label="System", action=ActivityLog.CREATED, category=ActivityLog.SECURITY,
                entity_type="User", entity_label=f"Person {n} \u2014 access request (asked for: Staff)")
            restamp(ActivityLog, row.pk, created_at=at(minutes=how_many - n))

    def lockouts(self, how_many):
        for n in range(how_many):
            self.lockout_row(f"p{n}@t.ph", at(minutes=how_many - n))

    def test_a_flood_of_exports_leaves_room_for_the_administrators_own_actions(self):
        self.exports(40)
        self.client.put(f"/api/users/{self.editha.pk}/", {
            "email": self.editha.email, "first_name": "Editha", "last_name": "Pascua",
            "role": self.roles[Role.PSYCHOLOGIST].pk}, format="json")
        self.client.post(f"/api/users/{self.rosa.pk}/reset-password/")
        data = self.overview()
        kinds = Counter(e["kind"] for e in data["security_log"])
        self.assertEqual(kinds["export"], 5)
        self.assertEqual((kinds["role"], kinds["reset"]), (1, 1))
        # The five shown are the newest five.
        shown = [e["text"] for e in data["security_log"] if e["kind"] == "export"]
        self.assertEqual(shown[0], "Exported the Records list (CSV, 39 rows)")
        self.assertEqual(shown[-1], "Exported the Records list (CSV, 35 rows)")
        self.assertEqual(data["security_log_more"],
                         [{"kind": "export", "count": 35, "text": "and 35 more exports not shown"}])

    def test_each_bulk_kind_is_capped_on_its_own_and_says_so(self):
        self.exports(6)
        self.lockouts(12)
        self.requests(8)
        data = self.overview()
        kinds = Counter(e["kind"] for e in data["security_log"])
        self.assertEqual((kinds["export"], kinds["lockout"], kinds["request"]), (5, 5, 5))
        self.assertEqual(
            {m["kind"]: (m["count"], m["text"]) for m in data["security_log_more"]},
            {"export": (1, "and 1 more export not shown"),
             "lockout": (7, "and 7 more sign-in lockouts not shown"),
             "request": (3, "and 3 more access requests not shown")})
        # The tiles that list lockouts and requests still count all of them.
        self.assertEqual(self.need("lockouts", data)["count"], 12)

    def test_nothing_is_added_when_nothing_is_left_out(self):
        self.exports(5)
        self.assertEqual(self.overview()["security_log_more"], [])

    def test_the_cap_is_not_applied_to_what_an_administrator_does(self):
        for n in range(12):
            user = self.person(f"a{n}@t.ph", "A", f"Person{n}", Role.STAFF)
            self.client.post(f"/api/users/{user.pk}/reset-password/")
        kinds = Counter(e["kind"] for e in self.overview()["security_log"])
        self.assertEqual(kinds["reset"], 12)


class EmailLimitInTheLogTest(ConsoleCase):
    """An account that reaches the limit on the emails its actions cause
    (accounts/email_notifications.py) writes one SECURITY line, and the log says
    so in words of its own. Like every line a signed-in account can cause, it is
    picked out by the `kind` its writer set, never by what its label says, and
    it is capped so a stolen account cannot fill the log."""

    SENTENCE = "Editha Pascua reached the email limit; further emails are held for up to a day"

    def reach_the_limit(self, by=None, times=2):
        """The real thing: `by` withdraws requests to one psychologist until the
        second email is over the limit of one."""
        child = self.child()
        with override_settings(BREVO_API_KEY="test-key", EMAIL_LIMIT_PER_RECIPIENT_HOUR=1,
                               EMAIL_LIMIT_PER_HOUR=0, EMAIL_LIMIT_PER_DAY=0), \
                patch("accounts.email_notifications._queue"):
            for _ in range(times):
                email_notifications.request_withdrawn(self.bulan, child, by=by or self.editha)

    def lines(self):
        return [e for e in self.overview()["security_log"] if e["kind"] == "email_limit"]

    def test_the_line_an_account_writes_is_in_the_log_with_its_own_words(self):
        self.reach_the_limit()
        (line,) = self.lines()
        self.assertEqual(self.SENTENCE, line["text"])
        self.assertEqual("Editha Pascua", line["actor"])
        self.assertEqual(admin_overview._iso(NOW), line["at"])
        self.assertEqual([], self.overview()["security_log_more"])

    def test_it_names_the_account_and_nobody_else(self):
        self.reach_the_limit()
        (line,) = self.lines()
        for leak in ("Marivic", "Bulan", "bulan@", "C-0", "withdr"):
            self.assertNotIn(leak, line["text"])
        self.assertNotIn("Marivic", str(self.overview()["security_log"]))

    def test_one_line_an_hour_however_long_it_goes_on(self):
        self.reach_the_limit(times=6)
        self.assertEqual(1, len(self.lines()))

    def test_the_words_are_not_read_out_of_the_label(self):
        """Without the kind it is an ordinary security line, whatever it says."""
        ActivityLog.objects.create(
            actor=self.rosa, actor_label="Rosa Santos", action=ActivityLog.UPDATED,
            category=ActivityLog.SECURITY, entity_type="User", entity_id=self.rosa.pk,
            entity_label="Rosa Santos reached the email limit")
        (line,) = self.overview()["security_log"]
        self.assertEqual("other", line["kind"])
        self.assertNotIn("held for up to a day", line["text"])

    def test_a_flood_shows_the_newest_five_and_says_how_many_are_left(self):
        for _ in range(8):
            log_activity(self.editha, ActivityLog.UPDATED, ActivityLog.SECURITY,
                         entity_type="User", entity_id=self.editha.pk,
                         entity_label="Editha Pascua reached the email limit",
                         kind=ActivityLog.KIND_EMAIL_LIMIT)
        for n, pk in enumerate(ActivityLog.objects.order_by("pk").values_list("pk", flat=True)):
            restamp(ActivityLog, pk, created_at=at(hours=8 - n))
        self.client.post(f"/api/users/{self.rosa.pk}/reset-password/")
        data = self.overview()
        kinds = Counter(e["kind"] for e in data["security_log"])
        self.assertEqual((kinds["email_limit"], kinds["reset"]), (5, 1))
        self.assertEqual(
            [{"kind": "email_limit", "count": 3, "text": "and 3 more email limits not shown"}],
            data["security_log_more"])
        # The five shown are the newest five.
        self.assertEqual(
            [at(hours=h).isoformat() for h in (1, 2, 3, 4, 5)],
            [e["at"] for e in data["security_log"] if e["kind"] == "email_limit"])

    def test_one_left_out_is_singular(self):
        for _ in range(6):
            log_activity(self.editha, ActivityLog.UPDATED, ActivityLog.SECURITY,
                         entity_type="User", entity_id=self.editha.pk,
                         entity_label="Editha Pascua reached the email limit",
                         kind=ActivityLog.KIND_EMAIL_LIMIT)
        self.assertEqual("and 1 more email limit not shown",
                         self.overview()["security_log_more"][0]["text"])

    def test_the_line_stays_readable_when_the_account_is_gone(self):
        self.reach_the_limit()
        self.editha.delete()
        (line,) = self.lines()
        self.assertEqual(self.SENTENCE, line["text"])

    def test_only_an_administrator_reads_it(self):
        self.reach_the_limit()
        for who in (self.rosa, self.bulan):
            self.assertEqual(403, self.as_user(who).get(URL).status_code)


class AssistantReadsInTheLogTest(ConsoleCase):
    def read(self, job_type, ref, user=None, child=None, when=None, ok=True):
        job = AssistantJob.objects.create(job_type=job_type, input_ref=ref, created_by=user,
                                          child=child, ok=ok)
        restamp(AssistantJob, job.pk, created_at=when or NOW)
        return job

    def test_what_each_kind_of_read_says(self):
        child = self.child()
        ref = f"C-{child.pk:04d}"
        self.read("brief", f"child:{child.pk}", self.bulan, child)
        self.read("case_brief", f"child:{child.pk}", self.editha, child, at(minutes=1))
        self.read("doc_intelligence", "report:7", self.bulan, child, at(minutes=2))
        self.read("doc_intelligence", "casereferral:3", self.editha, child, at(minutes=3))
        texts = [e["text"] for e in self.overview()["security_log"]]
        self.assertEqual(texts, [
            f"Marivic Bulan had the assistant write a brief for {ref}",
            f"Editha Pascua had the assistant write a case brief for {ref}",
            f"Marivic Bulan had the assistant summarise a report for {ref}",
            f"Editha Pascua had the assistant summarise a case referral for {ref}",
        ])
        self.assertEqual({e["kind"] for e in self.overview()["security_log"]}, {"assistant_read"})

    def test_a_survey_checked_answer_by_answer_is_one_line_and_nobody_did_it(self):
        child = self.child()
        for n in range(4):
            self.read("self_report", "invite:9", None, child, at(minutes=n))
        self.read("self_report", "invite:10", None, child, at(hours=1))
        log = self.overview()["security_log"]
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0]["actor"], "System")
        self.assertEqual(log[0]["text"],
                         f"The assistant checked survey answers for C-{child.pk:04d}")

    def test_only_reads_of_a_child_in_the_last_week_and_only_the_logged_kinds(self):
        child = self.child()
        self.read("chat", "who has no psychologist?", self.bulan, child)        # not a read
        self.read("remark_polish", "rewrite this", self.bulan, child)           # not a read
        self.read("brief", "child:1", self.bulan, None)                         # no child
        self.read("brief", "child:1", self.bulan, child, at(days=8))            # too old
        self.assertEqual(self.overview()["security_log"], [])

    def test_every_logged_job_type_has_its_words(self):
        """A sentence missing here is a console that says 'a record' for a read
        it should have named; this fails the day a type is added to the log."""
        from assistant.views import ACCESS_LOGGED_JOB_TYPES
        self.assertEqual(set(admin_overview._READ_WORDS), set(ACCESS_LOGGED_JOB_TYPES))

    def test_a_logged_job_type_with_no_sentence_still_shows_instead_of_failing(self):
        import assistant.views
        child = self.child()
        self.read("chat", "who has no psychologist?", self.bulan, child)
        with patch.object(assistant.views, "ACCESS_LOGGED_JOB_TYPES",
                          assistant.views.ACCESS_LOGGED_JOB_TYPES + ("chat",)):
            log = self.overview()["security_log"]
        self.assertEqual([e["text"] for e in log],
                         [f"Marivic Bulan had the assistant read a record for C-{child.pk:04d}"])

    def test_a_read_that_did_not_finish_says_so(self):
        child = self.child()
        self.read("brief", "child:1", self.bulan, child, ok=False)
        self.assertTrue(self.overview()["security_log"][0]["text"].endswith("(it did not finish)"))


# ---------------------------------------------------------------------------
# what it must never carry
# ---------------------------------------------------------------------------


class PrivacyTest(ConsoleCase):
    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="k")
    def test_no_text_from_a_case_a_child_or_the_assistant_comes_back(self):
        """Every sentinel is planted in a place the endpoint could be tempted
        to read, on children who ARE in the queues (no social worker, a
        stranded holder, a duplicate) so their rows are in the response."""
        sentinel = {name: f"ZZSENTINEL-{name}-ZZ" for name in (
            "case_study", "remark", "answer", "question", "job_input", "job_output",
            "custodian", "referral_reason", "medical", "decline_reason", "flag_note", "google")}
        # Two columns hold 16 characters at most.
        sentinel["custodian_number"] = "ZZ-CNUM-90210"
        sentinel["phone"] = "ZZ-PHONE-90210"

        gone = self.person("gone@t.ph", "Old", "Worker", Role.STAFF, status=User.ARCHIVED)
        orphan = self.child(
            "Ana", "Cruz", birth_date=date(2015, 3, 2), custodian_name=sentinel["custodian"],
            custodian_contact=sentinel["custodian_number"],
            referral_reason=sentinel["referral_reason"], medical_notes=sentinel["medical"])
        self.child("Ana", "Cruz", birth_date=date(2015, 3, 2), social_worker=gone,
                   assigned_psychologist=self.bulan)

        study = CaseStudy.objects.create(child=orphan)
        CaseStudySection.objects.create(case_study=study, key="child_description",
                                        value={"text": sentinel["case_study"]})
        RemarkNote.objects.create(child=orphan, author=self.bulan, text=sentinel["remark"])
        template = AgencyFormTemplate.objects.create(
            form_type=AgencyFormTemplate.SELF_REPORT_GOV, title=sentinel["question"])
        invite = OpinionnaireInvite.objects.create(
            child=orphan, template=template, created_by=self.editha, expires_at=NOW,
            answers={sentinel["question"]: sentinel["answer"]})
        SelfReportFlag.objects.create(
            invite=invite, child=orphan, question=sentinel["question"],
            answer=sentinel["answer"], source=SelfReportFlag.LEXICON, matched=sentinel["answer"],
            review_note=sentinel["flag_note"])
        # A psychologist's reason for declining is their own clinical words.
        # Its row IS in the queue (no psychologist, nobody asked again).
        AssignmentRequest.objects.create(
            child=orphan, psychologist=self.bulan, requested_by=self.editha,
            status=AssignmentRequest.DECLINED, reason=sentinel["decline_reason"])
        for kind, ref in (("brief", "child:1"), ("doc_intelligence", "report:" + sentinel["job_input"]),
                          ("self_report", "invite:" + sentinel["job_input"])):
            AssistantJob.objects.create(
                job_type=kind, input_ref=ref, output_text=sentinel["job_output"],
                created_by=self.bulan, child=orphan)
        AssistantJob.objects.create(job_type="chat", input_ref=sentinel["job_input"],
                                    output_text=sentinel["job_output"], created_by=self.bulan,
                                    child=orphan)
        User.objects.filter(pk=self.bulan.pk).update(
            google_sub=sentinel["google"], phone=sentinel["phone"])
        # A lockout line, whose text is whatever a stranger typed.
        self.lockout_row("someone@t.ph")
        # The spend record: its number's last digits, message id and subject
        # are not for the console, only counts and credits.
        SmsSend.objects.create(
            kind="custodian_booked", route="normal", credits=1, outcome="sent",
            message_id="ZZMSGID-9", subject="appointment:ZZSUBJ", number_hash="h" * 64,
            number_tail="Q7Zk", requested_by=self.editha)

        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The rows themselves ARE there, so a clean result means something.
        self.assertIn("Old Worker (inactive)", body)
        self.assertIn("had the assistant write a brief", body)
        self.assertGreater(self.need("no_social_worker", response.data)["count"], 0)
        for name, text in sentinel.items():
            self.assertNotIn(text, body, f"{name} reached the response")
        for text in ("Q7Zk", "ZZMSGID-9", "ZZSUBJ", "number_tail", "number_hash"):
            self.assertNotIn(text, body, f"the spend record leaked {text}")

    def test_no_password_or_token_comes_back(self):
        self.client.post(f"/api/users/{self.editha.pk}/reset-password/")
        User.objects.filter(pk=self.rosa.pk).update(must_change_password=True)
        body = self.client.get(URL).content.decode()
        self.assertNotIn("md5$", body)
        self.assertNotIn("pbkdf2", body)
        self.assertNotIn(self.editha.password, body)

    def test_the_module_does_not_import_the_case_study(self):
        tree = ast.parse(Path(admin_overview.__file__).read_text(encoding="utf-8"))
        imported = [node.module if isinstance(node, ast.ImportFrom) else alias.name
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                    for alias in (node.names if isinstance(node, ast.Import) else [None])]
        self.assertFalse([m for m in imported if m and m.split(".")[0] == "case_study"])


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


class BoundedQueriesTest(ConsoleCase):
    """The console runs on every open of the page, so its cost has to depend on
    how many KINDS of thing there are and not on how many children or users."""

    def populate(self, how_many, tag):
        gone = User.objects.filter(email="gone@t.ph").first() or self.person(
            "gone@t.ph", "Old", "Worker", Role.STAFF, status=User.ARCHIVED)
        for n in range(how_many):
            name = f"{tag}{n}"
            # no social worker, and unassigned
            self.child(name, "Orphan")
            # stranded with an archived holder
            self.child(name, "Stranded", social_worker=gone)
            # unanswered for 8 days, and a decline nobody followed up
            silent = self.child(name, "Silent", social_worker=self.editha)
            req = AssignmentRequest.objects.create(
                child=silent, psychologist=self.bulan, requested_by=self.editha)
            restamp(AssignmentRequest, req.pk, created_at=at(days=8))
            refused = self.child(name, "Refused", social_worker=self.editha)
            AssignmentRequest.objects.create(
                child=refused, psychologist=self.reyes, requested_by=self.editha,
                status=AssignmentRequest.DECLINED, reason="No room", decided_at=at(days=2))
            # an account name that lost a letter, and a repeated child
            self.person(f"{name}-broken@t.ph", name, "Br�ken", Role.STAFF)
            for _ in range(2):
                self.child(name, "Twin", birth_date=date(2014, 1, 1) + timedelta(days=n),
                           social_worker=self.editha)
            # a request, a lockout, a flagged account, a sign-in, a dormant account
            self.pending(f"{name}-req@t.ph", name, "Pending", Role.STAFF, days=n)
            self.lockout_row(f"{name}-lock@t.ph", at(hours=1))
            user = self.person(f"{name}-temp@t.ph", name, "Temp", Role.STAFF,
                               must_change_password=True)
            restamp(User, user.pk, created_at=at(days=60))
            signer = self.person(f"{name}-in@t.ph", name, "Signer", Role.PSYCHOLOGIST)
            ActivityLog.objects.create(actor=signer, actor_label=name, action=ActivityLog.LOGIN,
                                       category=ActivityLog.SECURITY)
            # an assistant read, a reminder, a leave, a security line
            AssistantJob.objects.create(job_type="brief", input_ref="child:1",
                                        created_by=self.bulan, child=silent)
            log_activity(self.admin, ActivityLog.ARCHIVED, ActivityLog.USER,
                         entity_type="User", entity_label=f"{name} gone")
            Unavailability.objects.create(
                psychologist=self.bulan, starts_on=date(2026, 10, 14), ends_on=date(2026, 10, 15))
            appointment = Appointment.objects.create(
                child=silent, psychologist=self.bulan, start=NOW + timedelta(days=n + 1))
            row = CustodianReminder.objects.create(appointment=appointment)
            restamp(CustodianReminder, row.pk, sent_at=at(hours=1))

    def count_queries(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        return len(queries), response.data

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="k")
    def test_five_and_twenty_five_of_everything_cost_the_same(self):
        AssistantSetting.objects.create(enabled=True)
        self.populate(5, "a")
        few, data = self.count_queries()
        self.assertEqual(self.need("no_social_worker", data)["count"], 5)
        self.populate(20, "b")
        many, data = self.count_queries()
        self.assertEqual(self.need("no_social_worker", data)["count"], 25)
        self.assertEqual(self.need("duplicates", data)["count"], 25)
        self.assertEqual(self.need("stuck_requests", data)["count"], 50)
        # Every queue is long enough to be cut at ten, so nothing was skipped
        # for being empty in one run and not the other.
        for key in ("access_requests", "lockouts", "temp_passwords", "stuck_requests",
                    "no_social_worker", "archived_holder", "names_to_fix", "duplicates"):
            self.assertEqual(len(self.need(key, data)["items"]), 10, key)
        self.assertEqual(few, many, "the query count grew with the data")
        self.assertLess(many, 60)


# ---------------------------------------------------------------------------
# export logging
# ---------------------------------------------------------------------------


class ExportLoggingTest(ConsoleCase):
    URL = "/api/children/exported/"

    def lines(self):
        return ActivityLog.objects.filter(action=ActivityLog.EXPORTED)

    def test_it_logs_who_and_how_many(self):
        response = self.client.post(self.URL, {"rows": 40}, format="json")
        self.assertEqual(response.status_code, 201)
        line = self.lines().get()
        self.assertEqual(line.actor, self.admin)
        self.assertEqual(line.actor_label, "Ada Admin")
        self.assertEqual(line.category, ActivityLog.SECURITY)
        self.assertEqual(line.entity_type, "Child")
        self.assertEqual(line.entity_label, "Exported the Records list (CSV, 40 rows)")

    def test_one_row_reads_as_one_row(self):
        self.client.post(self.URL, {"rows": 1}, format="json")
        self.assertEqual(self.lines().get().entity_label,
                         "Exported the Records list (CSV, 1 row)")

    def test_an_empty_export_is_still_an_export(self):
        self.assertEqual(self.client.post(self.URL, {"rows": 0}, format="json").status_code, 201)

    def test_anyone_who_can_read_the_list_can_say_they_took_it(self):
        for who in (self.editha, self.bulan):
            self.assertEqual(self.as_user(who).post(self.URL, {"rows": 3}, format="json").status_code,
                             201, who.email)
        self.assertEqual({a.actor_id for a in self.lines()}, {self.editha.pk, self.bulan.pk})

    def test_nobody_signed_in_is_refused_and_nothing_is_logged(self):
        self.assertEqual(APIClient().post(self.URL, {"rows": 3}, format="json").status_code, 401)
        self.assertFalse(self.lines().exists())

    def test_an_account_with_no_role_is_refused(self):
        nobody = self.pending("nobody@t.ph", "No", "Role")
        self.assertEqual(self.as_user(nobody).post(self.URL, {"rows": 3}, format="json").status_code,
                         403)
        self.assertFalse(self.lines().exists())

    def test_a_row_count_that_is_not_a_count_is_refused_and_logs_nothing(self):
        for bad in ({}, {"rows": -1}, {"rows": "many"}, {"rows": 1.5}, {"rows": True},
                    {"rows": None}, {"rows": [3]}, {"rows": 10 ** 7}):
            response = self.client.post(self.URL, bad, format="json")
            self.assertEqual(response.status_code, 400, bad)
        self.assertFalse(self.lines().exists())

    def test_a_count_typed_as_a_string_of_digits_is_taken(self):
        self.assertEqual(self.client.post(self.URL, {"rows": "12"}, format="json").status_code, 201)
        self.assertEqual(self.lines().get().entity_label, "Exported the Records list (CSV, 12 rows)")

    def test_the_result_entries_export_is_named_for_what_it_is(self):
        self.client.post(self.URL, {"rows": 9, "what": "results"}, format="json")
        self.assertEqual(self.lines().get().entity_label,
                         "Exported the result entries (CSV, 9 rows)")

    def test_a_caller_cannot_write_its_own_sentence_into_the_trail(self):
        for bad in ("anything at all", ["records"], {"a": 1}):
            response = self.client.post(self.URL, {"rows": 3, "what": bad}, format="json")
            self.assertEqual(response.status_code, 400, bad)
        self.assertFalse(self.lines().exists())

    def test_it_only_accepts_a_post(self):
        self.assertEqual(self.client.get(self.URL).status_code, 405)

    def test_a_count_with_thousands_of_digits_is_refused_not_a_server_error(self):
        """Python will not turn more than 4,300 digits into an integer, and
        raised: a 500 for whoever sent them."""
        for digits in ("9" * 5000, "9" * 4301, "9" * 8, "1000001"):
            response = self.client.post(self.URL, {"rows": digits}, format="json")
            self.assertEqual(response.status_code, 400, len(digits))
        self.assertFalse(self.lines().exists())
        # The largest count that is accepted is still accepted as digits.
        self.assertEqual(self.client.post(self.URL, {"rows": "1000000"}, format="json").status_code,
                         201)

    def test_one_account_cannot_write_the_line_without_end(self):
        """A ceiling per account on how many it will note down. The suite runs
        unthrottled, so the rate is patched in (DRF reads it as a class
        attribute)."""
        from rest_framework.throttling import SimpleRateThrottle
        with patch.object(SimpleRateThrottle, "THROTTLE_RATES", {"exports": "3/hour"}):
            cache.clear()
            codes = [self.as_user(self.editha).post(self.URL, {"rows": 1}, format="json").status_code
                     for _ in range(5)]
            self.assertEqual(codes, [201, 201, 201, 429, 429])
            self.assertEqual(self.lines().filter(actor=self.editha).count(), 3)
            # It is theirs alone: another account is not held to it.
            self.assertEqual(self.as_user(self.bulan).post(self.URL, {"rows": 1}, format="json").status_code,
                             201)
        cache.clear()

    def test_the_rate_is_configured_for_a_real_run(self):
        """Throttling is switched off while tests run; this reads the setting
        the server starts with, so deleting it fails here."""
        from django.conf import settings
        self.assertIn("exports", settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"])
        self.assertEqual(ChildViewSet.exported.kwargs["throttle_scope"], "exports")

    def test_the_new_action_is_one_the_database_accepts(self):
        self.client.post(self.URL, {"rows": 2}, format="json")
        self.assertEqual(self.lines().get().get_action_display(), "Exported")
