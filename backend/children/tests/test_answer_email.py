"""One email for "accepted" and the first session (owner's request, 24 Oct 2026;
design: docs/superpowers/specs/2026-10-21-email-notifications-design.md, "Round
three").

Accepting a case and booking its first session are two saves, and the social
worker used to get two emails seconds apart. The acceptance is now recorded as
NOT YET MAILED (`AssignmentRequest.answer_mailed_at` NULL) and the first of
three things sends it, once:

1. the psychologist books - ONE email for both;
2. the dialog ends with no booking - `POST /assignment-requests/<id>/mail-answer/`;
3. ten minutes pass - the sweep, run by the activity feed and by
   `send_session_reminders`.

Every test goes through the real endpoints, with the mail service on, the
sender's one seam (`accounts.email_notifications._queue`) patched to collect
what would have gone to Brevo, the commit callbacks run, and the clock pinned
(Monday 19 Oct 2026, 10:00 in Manila; sessions are on the Wednesday after).
"""
import importlib
import json
import time
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.apps import apps as real_apps
from django.core.cache import cache
from django.core.management import call_command
from django.db import migrations
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts import email_notifications
from accounts.email_notifications import (
    ANSWER_MAIL_GRACE, SAFETY, accepted_with_first_session, case_answered,
    claim_answer_mail, mail_overdue_answers, maybe_sweep, wait_for_queued)
from accounts.models import EmailOptOut
from children.models import AssignmentRequest, Child
from children.tests.payloads import planted_text
from scheduling.models import Appointment
from scheduling.reminders import send_session_reminders
from scheduling.tests import test_session_mail as mail
from scheduling.tests.test_api import give_referral

ORIGIN = mail.ORIGIN
NOW = mail.NOW
SWEEP_KEY = "email-answer-sweep"
FIRST_SESSION = "Wed 21 Oct 2026, 9:30 AM to 10:30 AM"
SECOND_SESSION = "Wed 21 Oct 2026, 10:30 AM to 11:30 AM"


class AnswerMailBase(mail.SessionMailBase):
    """The session-mail fixture (a social worker, two psychologists, the ISA,
    Wednesday-morning windows, a referral on file), except that the child is not
    yet anyone's: the social worker asks `psy` to take it, and `psy` answers."""

    def setUp(self):
        super().setUp()
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=None)
        self.child.refresh_from_db()
        cache.clear()
        self.addCleanup(cache.clear)

    # -- getting to the state under test -------------------------------------

    def _fresh_child(self, **fields):
        child = Child.objects.create(
            first_name="Zyxwanita", last_name="Qorvath", fullname="Zyxwanita Qorvath",
            birth_date=mail.BORN, case_type="Foster Care", case_category="Neglected",
            custodian_name=mail.CUSTODIAN, social_worker=self.sw, **fields)
        give_referral(child, self.sw)
        return child

    def ask(self, child=None, psychologist=None):
        """The social worker picks `psychologist` on the record: a request."""
        child = child or self.child
        with self.outbox():
            res = self._as(self.sw).patch(
                f"/api/children/{child.pk}/",
                {"psychologist": (psychologist or self.psy).pk}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        return AssignmentRequest.objects.get(child=child, status=AssignmentRequest.PENDING)

    def accepted(self, child=None, psychologist=None):
        """Asked, then accepted, with everything it mailed set aside. The
        request is returned as it now stands: accepted, its email owed."""
        psychologist = psychologist or self.psy
        req = self.ask(child, psychologist)
        with self.outbox() as sent:
            res = self._as(psychologist).post(f"/api/assignment-requests/{req.pk}/accept/")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual([], sent, "accepting mails nobody by itself")
        req.refresh_from_db()
        self.assertIsNone(req.answer_mailed_at)
        return req

    def mail_answer(self, req, user=None):
        return self._as(user or self.psy).post(
            f"/api/assignment-requests/{req.pk}/mail-answer/")

    def owed(self, req):
        req.refresh_from_db()
        return req.answer_mailed_at is None

    def grow_old(self, req, minutes):
        """Make the acceptance `minutes` old, without moving the clock."""
        AssignmentRequest.objects.filter(pk=req.pk).update(
            decided_at=NOW - timedelta(minutes=minutes))

    def texts(self, sent, user):
        return "\n".join(p["textContent"] for _, p in self.to(sent, user))


# --------------------------------------------------------------------------
# 1. The psychologist books: ONE email
# --------------------------------------------------------------------------

class AcceptingAloneTest(AnswerMailBase):
    def test_accepting_mails_nobody_and_leaves_the_email_owed(self):
        req = self.ask()
        with self.outbox() as sent:
            res = self._as(self.psy).post(f"/api/assignment-requests/{req.pk}/accept/")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual([], sent)
        self.assertTrue(self.owed(req))
        req.refresh_from_db()
        self.assertEqual(AssignmentRequest.ACCEPTED, req.status)


class BookingTheFirstSessionTest(AnswerMailBase):
    def test_accepting_then_booking_sends_the_social_worker_one_email(self):
        req = self.accepted()
        with self.outbox() as sent:
            res = self.book(self.psy)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        payload = self.one(sent, self.sw)
        self.assertEqual(f"Marivic Bulan accepted case {self.ref} and booked the first session",
                         payload["subject"])
        text = payload["textContent"]
        self.assertIn(f"Marivic Bulan accepted case {self.ref}. It is now in their records, "
                      "and the first session is booked.", text)
        self.assertIn(f"Case: {self.ref}", text)
        self.assertIn(f"First session: {FIRST_SESSION}", text)
        self.assertIn(f"Open the schedule: {ORIGIN}/schedule?for={self.sw.pk}", text)
        self.assertNotIn("Next session", text)
        self.assertNotIn("No session is booked yet", text)
        self.assertEqual([{"email": "sw@t.ph", "name": "Editha Pascua"}], payload["to"])
        # The acceptance is dealt with, and the psychologist is told nothing
        # about their own booking.
        self.assertFalse(self.owed(req))
        self.assertEqual([], self.to(sent, self.psy))

    def test_the_page_carries_the_schedule_button_and_the_time(self):
        self.accepted()
        with self.outbox() as sent:
            self.book(self.psy)
        html = self.one(sent, self.sw)["htmlContent"]
        self.assertIn(f'href="{ORIGIN}/schedule?for={self.sw.pk}"', html)
        self.assertIn("Open the schedule", html)
        self.assertIn("First session", html)
        self.assertIn("9:30&nbsp;AM", html)
        self.assertIn(SAFETY, html)

    def test_only_the_first_booking_combines(self):
        """Once the acceptance has gone, the next booking is the ordinary
        "booked a session" email."""
        self.accepted()
        with self.outbox() as sent:
            self.book(self.psy)
        self.assertEqual(["case_accepted"], self.kinds_to(sent, self.sw))
        with self.outbox() as sent:
            res = self.book(self.psy, start=self.later())
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])
        self.assertEqual(f"Marivic Bulan booked a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])

    def test_a_social_worker_who_switched_off_accepted_still_hears_of_the_booking(self):
        EmailOptOut.objects.create(user=self.sw, kind="case_accepted")
        req = self.accepted()
        with self.outbox() as sent:
            res = self.book(self.psy)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])
        self.assertEqual(f"Marivic Bulan booked a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])
        # The acceptance was dealt with all the same, or the sweep would look
        # at it every minute for good.
        self.assertFalse(self.owed(req))

    def test_a_social_worker_who_switched_off_both_hears_nothing(self):
        EmailOptOut.objects.create(user=self.sw, kind="case_accepted")
        EmailOptOut.objects.create(user=self.sw, kind="child_sessions")
        req = self.accepted()
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual([], sent)
        self.assertFalse(self.owed(req))

    def test_a_psychologist_booking_a_child_they_did_not_accept_is_the_ordinary_email(self):
        """No acceptance of theirs is owed, so nothing combines."""
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=self.psy)
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])

    def test_the_isa_booking_first_is_the_ordinary_email_and_the_acceptance_stays_owed(self):
        req = self.accepted()
        with self.outbox() as sent:
            res = self.book(self.admin)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        self.assertEqual("Ada Admin booked a session for case " + self.ref,
                         self.one(sent, self.sw)["subject"])
        self.assertTrue(self.owed(req))
        # And then the dialog ends: the acceptance names the session that is there.
        with self.outbox() as sent:
            res = self.mail_answer(req)
        self.assertEqual({"mailed": True}, res.data)
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        payload = self.one(sent, self.sw)
        self.assertEqual(f"Marivic Bulan accepted case {self.ref}", payload["subject"])
        self.assertIn(f"Next session: {FIRST_SESSION}", payload["textContent"])
        self.assertIn("and a session is booked.", payload["textContent"])
        self.assertNotIn("First session", payload["textContent"])
        self.assertIn(f"Open the schedule: {ORIGIN}/schedule?for={self.sw.pk}",
                      payload["textContent"])

    def test_the_social_worker_booking_first_is_not_emailed_for_it_and_the_acceptance_stays_owed(self):
        req = self.accepted()
        with self.outbox() as sent:
            res = self.book(self.sw)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["my_calendar"], [k for k, _ in sent])
        self.assertEqual([], self.to(sent, self.sw))
        self.assertTrue(self.owed(req))
        with self.outbox() as sent:
            self.mail_answer(req)
        self.assertIn(f"Next session: {FIRST_SESSION}", self.texts(sent, self.sw))

    def test_a_session_that_is_not_one_to_come_does_not_combine_or_claim(self):
        req = self.accepted()
        past = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=NOW - timedelta(days=1),
            status=Appointment.SCHEDULED)
        done = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=NOW + timedelta(days=1),
            status=Appointment.COMPLETED)
        for appointment in (past, done):
            self.assertIs(False, accepted_with_first_session(appointment, by=self.psy))
        self.assertTrue(self.owed(req))

    def test_nobody_but_the_psychologist_who_accepted_combines(self):
        req = self.accepted()
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=self.start,
            status=Appointment.SCHEDULED)
        for other in (self.admin, self.sw, self.psy2):
            self.assertIs(False, accepted_with_first_session(appointment, by=other))
        self.assertTrue(self.owed(req))

    def test_a_failure_while_deciding_is_logged_and_the_ordinary_email_goes(self):
        self.accepted()
        with self.outbox() as sent, \
                patch("accounts.email_notifications.claim_answer_mail",
                      side_effect=RuntimeError("no")), \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            res = self.book(self.psy)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])

    def test_an_email_that_cannot_be_prepared_gives_the_claim_back(self):
        """The booking's own email goes (the social worker is not left with
        nothing about either), and the acceptance is still owed, so the dialog's
        call or the sweep sends it."""
        req = self.accepted()
        with self.outbox() as sent, \
                patch("accounts.email_notifications.span", side_effect=RuntimeError("no")), \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            res = self.book(self.psy)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])
        self.assertEqual(f"Marivic Bulan booked a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])
        self.assertTrue(self.owed(req))
        with self.outbox() as sent:
            self.assertEqual({"mailed": True}, self.mail_answer(req).data)
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        self.assertIn(f"Next session: {FIRST_SESSION}", self.texts(sent, self.sw))

    def test_a_social_worker_who_switched_off_accepted_keeps_the_claim_taken(self):
        """Switched off is an answer, not a failure: nothing is given back."""
        EmailOptOut.objects.create(user=self.sw, kind="case_accepted")
        req = self.accepted()
        with self.outbox():
            self.book(self.psy)
        self.assertFalse(self.owed(req))


class TheFirstSessionTest(AnswerMailBase):
    """"Booked the first session" is said only of a first session. The
    combined email is the psychologist's booking, but a session somebody else
    had booked before it - or one from an earlier time with this child - makes
    it not the first, and then the booking is the ordinary email and the
    acceptance goes later, naming the earliest session."""

    def test_a_session_the_isa_booked_first_is_not_followed_by_a_first_session(self):
        req = self.accepted()
        with self.outbox():
            self.assertEqual(201, self.book(self.admin).status_code)         # 9:30
        with self.outbox() as sent:
            res = self.book(self.psy, start=self.later())                    # 10:30
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])
        self.assertEqual(f"Marivic Bulan booked a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])
        self.assertTrue(self.owed(req))
        with self.outbox() as sent:
            self.assertEqual({"mailed": True}, self.mail_answer(req).data)
        text = self.texts(sent, self.sw)
        self.assertIn(f"Next session: {FIRST_SESSION}", text)
        self.assertNotIn("First session", text)
        self.assertNotIn("booked the first session", text)

    def test_a_session_the_social_worker_booked_first_is_not_either(self):
        req = self.accepted()
        with self.outbox():
            self.assertEqual(201, self.book(self.sw).status_code)
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy, start=self.later()).status_code)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])
        self.assertTrue(self.owed(req))

    def test_a_child_who_has_had_sessions_with_them_before_is_not_on_a_first_session(self):
        req = self.accepted()
        for status in (Appointment.COMPLETED, Appointment.NO_SHOW):
            Appointment.objects.create(
                child=self.child, psychologist=self.psy, status=status,
                start=NOW - timedelta(days=40))
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])
        self.assertTrue(self.owed(req))

    def test_a_session_called_off_or_with_somebody_else_does_not_count(self):
        self.accepted()
        Appointment.objects.create(                 # called off
            child=self.child, psychologist=self.psy, status=Appointment.CANCELLED,
            start=self.start)
        Appointment.objects.create(                 # another psychologist's
            child=self.child, psychologist=self.psy2, status=Appointment.SCHEDULED,
            start=self.later(2))
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        self.assertIn("booked the first session", self.one(sent, self.sw)["subject"])

    def test_a_booking_for_another_child_does_not_use_up_this_childs_acceptance(self):
        waiting = self.accepted()
        other = self._child(self.other_sw, first="Yara", last="Zelmont")
        with self.outbox() as sent:
            res = self.book(self.psy, child=other)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["child_sessions"], self.kinds_to(sent, self.other_sw))
        self.assertEqual([], self.to(sent, self.sw))
        self.assertTrue(self.owed(waiting))

    def test_with_two_acceptances_owed_the_newest_combines_and_the_older_is_dropped(self):
        """Possible only through a history the endpoints do not make in one step,
        so the rows are built: the same psychologist accepted the child twice."""
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=self.psy)
        old, new = (AssignmentRequest.objects.create(
            child=self.child, psychologist=self.psy, requested_by=self.sw,
            status=AssignmentRequest.ACCEPTED, decided_by=self.psy,
            decided_at=NOW - timedelta(minutes=minutes)) for minutes in (5, 1))
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        self.assertTrue(self.owed(old))
        self.assertFalse(self.owed(new))
        # The older one is not mailed as if it were current: the newer one is
        # the child's acceptance, and has had its email.
        self.grow_old(old, 30)
        cache.clear()
        with self.outbox() as sent:
            self.assertEqual(1, mail_overdue_answers())
        self.assertEqual([], sent)
        self.assertFalse(self.owed(old))


class SupersededTest(AnswerMailBase):
    """An acceptance the record has moved past is dealt with and not mailed:
    "accepted ... it is now in their records" of a child who is with somebody
    else, or has been taken off the psychologist, is not so any more."""

    def test_a_child_who_went_back_and_forth_is_told_of_the_last_acceptance_only(self):
        first = self.accepted(psychologist=self.psy)
        second = self.accepted(psychologist=self.psy2)
        third = self.accepted(psychologist=self.psy)
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        self.assertFalse(self.owed(third))
        for req in (first, second):
            self.assertTrue(self.owed(req))
            self.grow_old(req, 30)
        cache.clear()
        with self.outbox() as sent:
            self.assertEqual(2, mail_overdue_answers())
        self.assertEqual([], sent)
        self.assertFalse(self.owed(first) or self.owed(second))

    def test_the_dialog_of_a_superseded_acceptance_mails_nothing_but_deals_with_it(self):
        old = self.accepted(psychologist=self.psy)
        self.accepted(psychologist=self.psy2)
        with self.outbox() as sent:
            res = self.mail_answer(old)
        self.assertEqual({"mailed": True}, res.data)
        self.assertEqual([], sent)
        self.assertFalse(self.owed(old))

    def test_a_child_taken_off_the_psychologist_is_not_told_they_accepted(self):
        req = self.accepted()
        res = self._as(self.sw).patch(
            f"/api/children/{self.child.pk}/", {"psychologist": None}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.assigned_psychologist_id)
        with self.outbox() as sent:
            self.assertEqual({"mailed": True}, self.mail_answer(req).data)
        self.assertEqual([], sent)
        self.assertFalse(self.owed(req))

    def test_a_question_still_open_to_somebody_else_does_not_supersede(self):
        """The social worker has asked another psychologist, who has not
        answered: the child is still with the one who accepted, and the
        acceptance is still true."""
        req = self.accepted()
        self.ask(psychologist=self.psy2)
        with self.outbox() as sent:
            self.assertEqual({"mailed": True}, self.mail_answer(req).data)
        self.assertEqual(["case_accepted"], [k for k, _ in sent])

    def test_booking_for_a_superseded_acceptance_is_the_ordinary_email(self):
        """A booking is never swallowed by an acceptance that will not be mailed."""
        old = self.accepted(psychologist=self.psy)
        self.accepted(psychologist=self.psy2)          # the child is psy2's now
        self.assertTrue(self.owed(old))
        self.assertIs(False, accepted_with_first_session(
            Appointment.objects.create(
                child=self.child, psychologist=self.psy, start=self.start,
                status=Appointment.SCHEDULED), by=self.psy))
        self.assertTrue(self.owed(old))


class ClosedCaseTest(AnswerMailBase):
    """The email waits for the dialog or the sweep, so the case can be closed in
    the meantime. It still says the psychologist accepted, and promises nothing."""

    def test_a_case_closed_before_the_mail_goes_promises_no_session_email(self):
        req = self.accepted()
        Child.objects.filter(pk=self.child.pk).update(status=Child.INACTIVE)
        with self.outbox() as sent:
            self.assertEqual({"mailed": True}, self.mail_answer(req).data)
        payload = self.one(sent, self.sw)
        self.assertEqual(f"Marivic Bulan accepted case {self.ref}", payload["subject"])
        text = payload["textContent"]
        self.assertIn(f"Marivic Bulan accepted case {self.ref}, which has since been closed.",
                      text)
        self.assertNotIn("you will get an email", text)
        self.assertNotIn("No session is booked yet", text)
        self.assertNotIn("in their records", text)

    def test_an_open_case_still_promises_it(self):
        req = self.accepted()
        with self.outbox() as sent:
            self.mail_answer(req)
        self.assertIn("you will get an email when one is", self.texts(sent, self.sw))


# --------------------------------------------------------------------------
# 2. The dialog ends with no booking: POST .../mail-answer/
# --------------------------------------------------------------------------

class MailAnswerTest(AnswerMailBase):
    def test_the_route(self):
        req = self.accepted()
        self.assertEqual(f"/api/assignment-requests/{req.pk}/mail-answer/",
                         reverse("assignment-request-mail-answer", args=[req.pk]))

    def test_it_sends_the_acceptance_on_its_own_once(self):
        req = self.accepted()
        with self.outbox() as sent:
            first = self.mail_answer(req)
            second = self.mail_answer(req)
        self.assertEqual((200, {"mailed": True}), (first.status_code, first.data))
        self.assertEqual((200, {"mailed": False}), (second.status_code, second.data))
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        payload = self.one(sent, self.sw)
        self.assertEqual(f"Marivic Bulan accepted case {self.ref}", payload["subject"])
        text = payload["textContent"]
        self.assertIn(f"Marivic Bulan accepted case {self.ref}. It is now in their records. "
                      "No session is booked yet; you will get an email when one is.", text)
        self.assertNotIn("Next session", text)
        self.assertNotIn("First session", text)
        self.assertIn(f"Open the record: {ORIGIN}/report/child/{self.child.pk}"
                      f"?for={self.sw.pk}", text)
        self.assertFalse(self.owed(req))

    def test_a_booking_after_it_is_the_ordinary_email(self):
        req = self.accepted()
        with self.outbox():
            self.mail_answer(req)
        with self.outbox() as sent:
            self.assertEqual(201, self.book(self.psy).status_code)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])

    def test_after_a_booking_carried_the_acceptance_it_mails_nothing(self):
        """The dialog's "Book later" can still be pressed after the booking in
        another tab: the acceptance has gone, and it says so."""
        req = self.accepted()
        with self.outbox():
            self.book(self.psy)
        with self.outbox() as sent:
            res = self.mail_answer(req)
        self.assertEqual({"mailed": False}, res.data)
        self.assertEqual([], sent)

    def test_the_promise_of_a_later_email_is_made_only_to_one_who_gets_those(self):
        EmailOptOut.objects.create(user=self.sw, kind="child_sessions")
        req = self.accepted()
        with self.outbox() as sent:
            self.mail_answer(req)
        text = self.one(sent, self.sw)["textContent"]
        self.assertIn(f"Marivic Bulan accepted case {self.ref}. It is now in their records. "
                      "No session is booked yet.\n", text)
        self.assertNotIn("you will get an email", text)

    def test_a_social_worker_who_switched_the_acceptance_off_is_mailed_nothing_but_it_is_dealt_with(self):
        EmailOptOut.objects.create(user=self.sw, kind="case_accepted")
        req = self.accepted()
        with self.outbox() as sent:
            res = self.mail_answer(req)
        self.assertEqual({"mailed": True}, res.data)
        self.assertEqual([], sent)
        self.assertFalse(self.owed(req))

    def test_who_may_ask(self):
        req = self.accepted()
        other_psychologist = self.mail_answer(req, self.psy2)
        holder = self.mail_answer(req, self.sw)
        isa = self.mail_answer(req, self.admin)
        stranger = self.mail_answer(req, self.other_sw)
        anonymous = APIClient().post(f"/api/assignment-requests/{req.pk}/mail-answer/")
        # Another psychologist is outside the queue entirely, as for accept and
        # decline: hidden, not disclosed. Those who can see the request are told
        # no, as they are there.
        self.assertEqual(404, other_psychologist.status_code)
        self.assertEqual(403, holder.status_code)
        self.assertEqual(403, isa.status_code)
        self.assertEqual(404, stranger.status_code)
        self.assertEqual(401, anonymous.status_code)
        self.assertTrue(self.owed(req))

    def test_nothing_is_sent_to_anyone_who_may_not_ask(self):
        req = self.accepted()
        with self.outbox() as sent:
            for user in (self.psy2, self.sw, self.admin, self.other_sw):
                self.mail_answer(req, user)
        self.assertEqual([], sent)

    def test_only_an_accepted_request_has_an_answer_to_mail(self):
        pending = self.ask()
        self.assertEqual(409, self.mail_answer(pending).status_code)
        self.assertIn("accepted", self.mail_answer(pending).data["detail"])

        declined = self.ask(self._fresh_child(), self.psy)
        self.assertEqual(200, self._as(self.psy).post(
            f"/api/assignment-requests/{declined.pk}/decline/",
            {"reason": "Full."}, format="json").status_code)
        self.assertEqual(409, self.mail_answer(declined).status_code)

        withdrawn = self.ask(self._fresh_child(), self.psy)
        self.assertEqual(200, self._as(self.sw).post(
            f"/api/assignment-requests/{withdrawn.pk}/withdraw/").status_code)
        self.assertEqual(409, self.mail_answer(withdrawn).status_code)

    def test_it_only_takes_post(self):
        req = self.accepted()
        res = self._as(self.psy).get(f"/api/assignment-requests/{req.pk}/mail-answer/")
        self.assertEqual(405, res.status_code)

    def test_it_sends_only_the_acceptance_of_that_request(self):
        """No body is read: nothing a caller sends can reach the email."""
        req = self.accepted()
        with self.outbox() as sent:
            res = self._as(self.psy).post(
                f"/api/assignment-requests/{req.pk}/mail-answer/",
                {"subject": "Hello", "to": "x@evil.example", "paragraphs": ["x"]},
                format="json")
        self.assertEqual(200, res.status_code)
        ((kind, payload),) = sent
        self.assertEqual("case_accepted", kind)
        self.assertEqual("sw@t.ph", payload["to"][0]["email"])
        self.assertNotIn("evil", json.dumps(payload))


# --------------------------------------------------------------------------
# 3. Nothing came: the sweep
# --------------------------------------------------------------------------

class SweepTest(AnswerMailBase):
    def feed(self, user=None):
        return self._as(user or self.sw).get("/api/activity/")

    def test_an_acceptance_eleven_minutes_old_is_mailed_by_the_activity_feed_once(self):
        req = self.accepted()
        self.grow_old(req, 11)
        cache.clear()
        with self.outbox() as sent:
            res = self.feed()
        self.assertEqual(200, res.status_code)
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        payload = self.one(sent, self.sw)
        self.assertEqual(f"Marivic Bulan accepted case {self.ref}", payload["subject"])
        self.assertIn("No session is booked yet; you will get an email when one is.",
                      payload["textContent"])
        self.assertFalse(self.owed(req))
        # Read again, past the once-a-minute limit: it is not mailed twice.
        cache.clear()
        with self.outbox() as sent:
            self.feed()
        self.assertEqual([], sent)

    def test_the_sweep_names_a_session_booked_since(self):
        req = self.accepted()
        with self.outbox():
            self.book(self.admin)
        self.grow_old(req, 30)
        cache.clear()
        with self.outbox() as sent:
            self.feed(self.psy)
        self.assertEqual(["case_accepted"], self.kinds_to(sent, self.sw))
        self.assertIn(f"Next session: {FIRST_SESSION}", self.texts(sent, self.sw))

    def test_nine_minutes_is_not_overdue_and_ten_is(self):
        young, exact = self.accepted(), self.accepted(self._fresh_child())
        self.grow_old(young, 9)
        self.grow_old(exact, 10)
        self.assertEqual(timedelta(minutes=10), ANSWER_MAIL_GRACE)
        cache.clear()
        with self.outbox() as sent:
            self.feed()
        self.assertEqual(1, len(sent))
        self.assertTrue(self.owed(young))
        self.assertFalse(self.owed(exact))

    def test_a_fresh_acceptance_is_left_to_the_dialog(self):
        req = self.accepted()
        cache.clear()
        with self.outbox() as sent:
            self.feed()
        self.assertEqual([], sent)
        self.assertTrue(self.owed(req))

    def test_an_acceptance_already_dealt_with_is_left_alone(self):
        req = self.accepted()
        with self.outbox():
            self.mail_answer(req)
        self.grow_old(req, 60)
        cache.clear()
        with self.outbox() as sent:
            self.feed()
        self.assertEqual([], sent)

    def test_other_answers_are_not_the_sweeps_business(self):
        declined = self.ask()
        with self.outbox():
            self._as(self.psy).post(f"/api/assignment-requests/{declined.pk}/decline/",
                                    {"reason": "Full."}, format="json")
        pending = self.ask(self._fresh_child(), self.psy2)
        AssignmentRequest.objects.update(decided_at=NOW - timedelta(days=2))
        AssignmentRequest.objects.filter(pk=declined.pk).update(answer_mailed_at=None)
        cache.clear()
        with self.outbox() as sent:
            self.assertEqual(0, mail_overdue_answers())
        self.assertEqual([], sent)
        self.assertTrue(self.owed(declined) and self.owed(pending))

    def test_a_second_read_inside_the_minute_does_not_sweep_again(self):
        first = self.accepted()
        self.grow_old(first, 11)
        cache.clear()
        with self.outbox() as sent:
            self.feed()
        self.assertEqual(1, len(sent))
        second = self.accepted(self._fresh_child())
        self.grow_old(second, 11)
        with self.outbox() as sent:
            self.feed()                  # the cache still holds the last sweep
        self.assertEqual([], sent)
        self.assertTrue(self.owed(second))
        cache.clear()                    # a minute later
        with self.outbox() as sent:
            self.feed()
        self.assertEqual(1, len(sent))
        self.assertFalse(self.owed(second))

    def test_signed_out_there_is_no_sweep_at_all(self):
        req = self.accepted()
        self.grow_old(req, 11)
        cache.clear()
        with self.outbox() as sent:
            res = APIClient().get("/api/activity/")
        self.assertEqual(401, res.status_code)
        self.assertEqual([], sent)
        self.assertIsNone(cache.get(SWEEP_KEY))
        self.assertTrue(self.owed(req))

    def test_the_oldest_go_first_and_a_batch_is_capped(self):
        reqs = [self.accepted(self._fresh_child()) for _ in range(3)]
        for age, req in zip((12, 40, 20), reqs):
            self.grow_old(req, age)
        with self.outbox() as sent, patch.object(email_notifications, "SWEEP_BATCH", 2):
            self.assertEqual(2, mail_overdue_answers())
        self.assertEqual(2, len(sent))
        # The 40- and 20-minute-old ones; the 12-minute-old one waits.
        self.assertEqual([True, False, False], [self.owed(r) for r in reqs])
        with self.outbox() as sent:
            self.assertEqual(1, mail_overdue_answers())
            self.assertEqual(0, mail_overdue_answers())

    def test_withdrawn_requests_do_not_use_up_the_batch(self):
        """Only an accepted request is owed an email. A withdrawn one keeps
        `answer_mailed_at` empty for good, and older ones must not crowd out the
        acceptance behind them."""
        for _ in range(3):
            AssignmentRequest.objects.create(
                child=self.child, psychologist=self.psy, requested_by=self.sw,
                status=AssignmentRequest.WITHDRAWN, decided_at=NOW - timedelta(days=3))
        req = self.accepted(self._fresh_child())
        self.grow_old(req, 30)
        with self.outbox() as sent, patch.object(email_notifications, "SWEEP_BATCH", 2):
            self.assertEqual(1, mail_overdue_answers())
        self.assertEqual(1, len(sent))
        self.assertFalse(self.owed(req))

    def test_the_feed_mails_a_few_at_a_time_and_the_reminder_run_the_rest(self):
        """The feed's request is the one every screen waits for, so a backlog is
        cleared a few a minute there; the timer-driven run takes a whole batch."""
        self.assertLess(email_notifications.FEED_SWEEP_BATCH, email_notifications.SWEEP_BATCH)
        reqs = [self.accepted(self._fresh_child()) for _ in range(3)]
        for age, req in zip((30, 20, 12), reqs):
            self.grow_old(req, age)
        cache.clear()
        with self.outbox() as sent, patch.object(email_notifications, "FEED_SWEEP_BATCH", 2):
            self.assertEqual(200, self.feed().status_code)
        self.assertEqual(2, len(sent))
        self.assertEqual([False, False, True], [self.owed(r) for r in reqs])
        with self.outbox() as sent:
            report = send_session_reminders()
        self.assertEqual(1, len(sent))
        self.assertEqual(1, report["answers_mailed"])
        self.assertFalse(any(self.owed(r) for r in reqs))

    def test_an_acceptance_whose_decider_has_gone_is_still_mailed(self):
        """`decided_by` is cleared if that account is ever deleted."""
        req = self.accepted()
        AssignmentRequest.objects.filter(pk=req.pk).update(decided_by=None)
        self.grow_old(req, 30)
        with self.outbox() as sent:
            self.assertEqual(1, mail_overdue_answers())
        self.assertEqual(["case_accepted"], [k for k, _ in sent])

    def test_a_failure_on_one_does_not_stop_the_next(self):
        first, second = self.accepted(), self.accepted(self._fresh_child())
        self.grow_old(first, 30)
        self.grow_old(second, 20)
        real = email_notifications.claim_answer_mail
        calls = []

        def flaky(req):
            calls.append(req.pk)
            if len(calls) == 1:
                raise RuntimeError("database is busy")
            return real(req)
        with self.outbox() as sent, \
                patch("accounts.email_notifications.claim_answer_mail", side_effect=flaky), \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            self.assertEqual(1, mail_overdue_answers())
        self.assertEqual(1, len(sent))
        self.assertTrue(self.owed(first))
        self.assertFalse(self.owed(second))

    def test_it_never_raises(self):
        with patch("accounts.email_notifications.timezone.now", side_effect=RuntimeError), \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            self.assertEqual(0, mail_overdue_answers())
        with patch("accounts.email_notifications.cache.add", side_effect=RuntimeError), \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            maybe_sweep()

    def test_a_sweep_that_finds_nothing_costs_one_cache_call_afterwards(self):
        cache.clear()
        with patch("accounts.email_notifications.mail_overdue_answers") as sweep:
            maybe_sweep()
            maybe_sweep()
            maybe_sweep()
        sweep.assert_called_once_with(limit=email_notifications.FEED_SWEEP_BATCH)


class ReminderRunTest(AnswerMailBase):
    """`send_session_reminders` is the second place the sweep runs, and is not
    throttled: whatever calls it is on a timer already."""

    def overdue(self):
        req = self.accepted()
        self.grow_old(req, 15)
        return req

    def test_it_mails_an_overdue_acceptance_and_says_how_many(self):
        req = self.overdue()
        with self.outbox() as sent:
            report = send_session_reminders()
        self.assertEqual(["case_accepted"], self.kinds_to(sent, self.sw))
        self.assertEqual(1, report["answers_mailed"])
        self.assertIn("emailed 1 overdue acceptance(s) to social workers", report["lines"])
        self.assertFalse(self.owed(req))
        # Counts, never names: this report is printed to a terminal and a log.
        blob = "\n".join(report["lines"])
        self.assertNotIn("Zyxwanita", blob)
        self.assertNotIn("Qorvath", blob)
        self.assertNotIn(self.sw.email, blob)
        # The reminders' own counts are about reminders only.
        self.assertEqual((0, 0, 0), (report["sent"], report["skipped"], report["failed"]))

    def test_it_is_not_throttled_by_the_feeds_sweep(self):
        self.overdue()
        cache.add(SWEEP_KEY, True, 60)
        with self.outbox() as sent:
            send_session_reminders()
        self.assertEqual(1, len(sent))

    def test_a_dry_run_counts_and_claims_nothing(self):
        req = self.overdue()
        with self.outbox() as sent:
            report = send_session_reminders(dry_run=True)
        self.assertEqual([], sent)
        self.assertTrue(self.owed(req))
        self.assertEqual(0, report["answers_mailed"])
        self.assertIn("would email 1 overdue acceptance(s) to social workers",
                      report["lines"])
        # So the real run that follows still has it to send.
        with self.outbox() as sent:
            send_session_reminders()
        self.assertEqual(1, len(sent))

    def test_with_nothing_overdue_the_report_says_nothing_about_it(self):
        self.accepted()
        with self.outbox() as sent:
            report = send_session_reminders()
        self.assertEqual([], sent)
        self.assertEqual(0, report["answers_mailed"])
        self.assertFalse([line for line in report["lines"] if "acceptance" in line])

    def test_the_command_prints_it_and_waits_for_the_mail_to_leave(self):
        self.overdue()
        out = StringIO()
        with self.outbox() as sent, \
                patch("accounts.email_notifications.wait_for_queued") as wait:
            call_command("send_session_reminders", stdout=out)
        wait.assert_called_once_with()
        self.assertEqual(1, len(sent))
        self.assertIn("emailed 1 overdue acceptance(s) to social workers", out.getvalue())


class WaitForQueuedTest(AnswerMailBase):
    """A daemon thread dies with its process, and a command exits as soon as it
    has queued: `wait_for_queued` is the command's way of letting mail leave."""

    def test_it_waits_for_a_thread_the_queue_started(self):
        import threading
        release, finished = threading.Event(), []

        def slow_send():
            release.wait(5)
            finished.append(True)
        thread = threading.Thread(target=slow_send, name=email_notifications._THREAD_NAME,
                                  daemon=True)
        thread.start()
        threading.Timer(0.05, release.set).start()
        wait_for_queued(timeout=5)
        self.assertEqual([True], finished)

    def test_it_gives_up_after_its_timeout(self):
        import threading
        release = threading.Event()
        thread = threading.Thread(target=release.wait, args=(5,),
                                  name=email_notifications._THREAD_NAME, daemon=True)
        thread.start()
        self.addCleanup(release.set)
        wait_for_queued(timeout=0.05)
        self.assertTrue(thread.is_alive())

    def test_it_leaves_other_threads_alone(self):
        import threading
        release = threading.Event()
        other = threading.Thread(target=release.wait, args=(5,), name="something-else",
                                 daemon=True)
        other.start()
        self.addCleanup(release.set)
        started = time.monotonic()
        wait_for_queued(timeout=3)             # returns at once: nothing of ours
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(other.is_alive())


# --------------------------------------------------------------------------
# No mail service: the local copy
# --------------------------------------------------------------------------

class ConsoleModeTest(AnswerMailBase):
    def test_accepting_and_booking_logs_one_acceptance_line_and_deals_with_it(self):
        req = self.accepted()
        with override_settings(BREVO_API_KEY=""), \
                self.captureOnCommitCallbacks(execute=True), \
                self.assertLogs("accounts.email_notifications", level="WARNING") as logged:
            res = self.book(self.psy)
        self.assertEqual(201, res.status_code, res.data)
        (line,) = logged.output
        self.assertIn("EMAIL (console) case_accepted to sw@t.ph: ", line)
        self.assertIn("and booked the first session", line)
        self.assertNotIn("child_sessions", line)
        self.assertFalse(self.owed(req))

    def test_the_dialogs_call_logs_one_line_and_deals_with_it(self):
        req = self.accepted()
        with override_settings(BREVO_API_KEY=""), \
                self.captureOnCommitCallbacks(execute=True), \
                self.assertLogs("accounts.email_notifications", level="WARNING") as logged:
            res = self.mail_answer(req)
        self.assertEqual({"mailed": True}, res.data)
        (line,) = logged.output
        self.assertIn(f"EMAIL (console) case_accepted to sw@t.ph: Marivic Bulan accepted "
                      f"case {self.ref}", line)
        self.assertFalse(self.owed(req))
        # And the sweep does not come back for it every minute.
        self.grow_old(req, 60)
        cache.clear()
        with override_settings(BREVO_API_KEY=""), self.assertNoLogs(
                "accounts.email_notifications", level="WARNING"):
            self.assertEqual(0, mail_overdue_answers())


# --------------------------------------------------------------------------
# A decline is still mailed at once
# --------------------------------------------------------------------------

class DeclineTest(AnswerMailBase):
    def test_a_decline_is_mailed_at_once_and_stamped(self):
        req = self.ask()
        reason = "Quillon caseload is already full until March"
        with self.outbox() as sent:
            res = self._as(self.psy).post(
                f"/api/assignment-requests/{req.pk}/decline/", {"reason": reason},
                format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["case_declined"], [k for k, _ in sent])
        payload = self.one(sent, self.sw)
        self.assertEqual(f"Marivic Bulan declined case {self.ref}", payload["subject"])
        self.assertIn("Sign in to read their reason and ask another psychologist.",
                      payload["textContent"])
        self.assertNotIn("Quillon", json.dumps(payload))
        self.assertFalse(self.owed(req))
        req.refresh_from_db()
        self.assertEqual(NOW, req.answer_mailed_at)

    def test_a_refused_decline_stamps_nothing(self):
        req = self.ask()
        with self.outbox() as sent:
            res = self._as(self.psy).post(
                f"/api/assignment-requests/{req.pk}/decline/", {"reason": ""}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertEqual([], sent)
        self.assertTrue(self.owed(req))

    def test_a_decline_is_not_the_sweeps_to_claim(self):
        req = self.ask()
        with self.outbox():
            self._as(self.psy).post(f"/api/assignment-requests/{req.pk}/decline/",
                                    {"reason": "Full."}, format="json")
        self.assertFalse(claim_answer_mail(req))


# --------------------------------------------------------------------------
# The claim
# --------------------------------------------------------------------------

class ClaimTest(AnswerMailBase):
    def test_exactly_one_caller_claims_an_acceptance(self):
        req = self.accepted()
        self.assertIs(True, claim_answer_mail(req))
        self.assertIs(False, claim_answer_mail(req))
        req.refresh_from_db()
        self.assertEqual(NOW, req.answer_mailed_at)

    def test_only_an_accepted_request_can_be_claimed(self):
        for status in (AssignmentRequest.PENDING, AssignmentRequest.DECLINED,
                       AssignmentRequest.WITHDRAWN):
            child = self._fresh_child()
            req = AssignmentRequest.objects.create(
                child=child, psychologist=self.psy, requested_by=self.sw, status=status)
            self.assertIs(False, claim_answer_mail(req), status)
            self.assertTrue(self.owed(req), status)

    def test_a_booking_and_the_dialog_and_the_sweep_racing_send_one_email(self):
        """Each tries to claim from the same stale copy of the request, as three
        requests handled at once would: only one of them wins."""
        req = self.accepted()
        stale = [AssignmentRequest.objects.get(pk=req.pk) for _ in range(3)]
        self.assertEqual([True, False, False], [claim_answer_mail(r) for r in stale])


# --------------------------------------------------------------------------
# What the email says
# --------------------------------------------------------------------------

class WordingTest(AnswerMailBase):
    def accepted_request(self):
        # As accepting leaves it: the child is with the psychologist.
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=self.psy)
        self.child.refresh_from_db()
        return AssignmentRequest.objects.create(
            child=self.child, psychologist=self.psy, requested_by=self.sw,
            status=AssignmentRequest.ACCEPTED, decided_at=NOW, decided_by=self.psy)

    def one_email(self, req, **kwargs):
        with patch("accounts.email_notifications._queue") as queue, \
                override_settings(BREVO_API_KEY="test-key", CORS_ALLOWED_ORIGINS=[ORIGIN]):
            case_answered(req, by=self.psy, **kwargs)
        queue.assert_called_once()
        return queue.call_args.args[0]

    def session(self, psychologist=None, start=None, status=Appointment.SCHEDULED,
                child=None, minutes=60):
        return Appointment.objects.create(
            child=child or self.child, psychologist=psychologist or self.psy,
            start=start or self.start, duration_minutes=minutes, status=status)

    def test_with_the_session_just_booked(self):
        appointment = self.session()
        payload = self.one_email(self.accepted_request(), first_session=appointment)
        self.assertEqual(f"Marivic Bulan accepted case {self.ref} and booked the first session",
                         payload["subject"])
        self.assertIn(f"First session: {FIRST_SESSION}", payload["textContent"])

    def test_a_session_across_midnight_names_both_days(self):
        late = self.session(start=self.start.replace(hour=23, minute=30))
        payload = self.one_email(self.accepted_request(), first_session=late)
        self.assertIn("First session: Wed 21 Oct 2026, 11:30 PM to Thu 22 Oct 2026, 12:30 AM",
                      payload["textContent"])

    def test_with_no_session_it_says_so(self):
        payload = self.one_email(self.accepted_request())
        self.assertEqual(f"Marivic Bulan accepted case {self.ref}", payload["subject"])
        self.assertIn("No session is booked yet; you will get an email when one is.",
                      payload["textContent"])
        self.assertIn(f"Open the record: {ORIGIN}/report/child/{self.child.pk}"
                      f"?for={self.sw.pk}", payload["textContent"])

    def test_the_next_session_is_the_earliest_one_still_to_come_with_this_psychologist(self):
        self.session(start=NOW - timedelta(days=2))                    # over
        self.session(start=self.start + timedelta(days=7))             # later
        self.session(start=self.start + timedelta(days=1),
                     status=Appointment.CANCELLED)                     # called off
        self.session(start=self.start - timedelta(hours=1),
                     status=Appointment.CANCELLED)                     # called off, earlier
        self.session(psychologist=self.psy2, start=self.start - timedelta(hours=1))   # not theirs
        self.session(start=self.start - timedelta(hours=1),
                     child=self._fresh_child())                        # another child
        self.session(start=self.start, minutes=45)                     # the one
        payload = self.one_email(self.accepted_request())
        self.assertIn("Next session: Wed 21 Oct 2026, 9:30 AM to 10:15 AM",
                      payload["textContent"])
        self.assertNotIn("No session is booked yet", payload["textContent"])

    def test_the_same_html_has_the_session_in_a_fact_row_and_never_the_purpose_or_notes(self):
        appointment = self.session()
        # `purpose` is a 20-character column: a longer sentinel could not be in a
        # real row, and PostgreSQL refuses to store it.
        purpose = "Xylo school refusal"
        self.assertLessEqual(len(purpose), Appointment._meta.get_field("purpose").max_length)
        Appointment.objects.filter(pk=appointment.pk).update(
            purpose=purpose, notes=mail.NOTES)
        appointment.refresh_from_db()
        payload = self.one_email(self.accepted_request(), first_session=appointment)
        blob = json.dumps(payload, ensure_ascii=False).lower()
        for sentinel in ("zyxwanita", "qorvath", "brunhilda", "xylo", "school"):
            self.assertNotIn(sentinel, blob)

    def test_nobody_is_told_of_their_own_answer(self):
        with patch("accounts.email_notifications._queue") as queue, \
                override_settings(BREVO_API_KEY="test-key"):
            case_answered(self.accepted_request(), by=self.sw, first_session=self.session())
        queue.assert_not_called()


# --------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------

class BackfillTest(AnswerMailBase):
    """0032 stamps every answered request, so the first sweep after the deploy
    does not email each social worker about every acceptance ever recorded."""

    def migration(self):
        return importlib.import_module("children.migrations.0032_answer_mailed_at")

    def row(self, status, decided_at):
        return AssignmentRequest.objects.create(
            child=self._fresh_child(), psychologist=self.psy, requested_by=self.sw,
            status=status, decided_at=decided_at)

    def test_it_is_in_the_migration_and_has_nothing_to_undo(self):
        module = self.migration()
        runs = [op for op in module.Migration.operations
                if isinstance(op, migrations.RunPython)]
        self.assertEqual(1, len(runs))
        self.assertIs(module.stamp_answered, runs[0].code)
        self.assertIs(migrations.RunPython.noop, runs[0].reverse_code)
        # After the column exists, in the same migration.
        names = [type(op).__name__ for op in module.Migration.operations]
        self.assertLess(names.index("AddField"), names.index("RunPython"))

    def test_only_pending_requests_stay_unstamped(self):
        decided = NOW - timedelta(days=9)
        rows = {status: self.row(status, None if status == "pending" else decided)
                for status in ("pending", "accepted", "declined", "withdrawn")}
        undated = self.row(AssignmentRequest.ACCEPTED, None)
        AssignmentRequest.objects.update(answer_mailed_at=None)

        self.migration().stamp_answered(real_apps, None)

        for status, req in rows.items():
            req.refresh_from_db()
            if status == "pending":
                self.assertIsNone(req.answer_mailed_at)
            else:
                self.assertEqual(decided, req.answer_mailed_at, status)
        # Nothing says when this one was decided: now.
        undated.refresh_from_db()
        self.assertEqual(NOW, undated.answer_mailed_at)

    def test_after_it_the_sweep_has_nothing_old_to_mail(self):
        self.row(AssignmentRequest.ACCEPTED, NOW - timedelta(days=30))
        self.row(AssignmentRequest.ACCEPTED, NOW - timedelta(days=3))
        AssignmentRequest.objects.update(answer_mailed_at=None)
        self.migration().stamp_answered(real_apps, None)
        with self.outbox() as sent:
            self.assertEqual(0, mail_overdue_answers())
        self.assertEqual([], sent)

    def test_run_again_it_changes_nothing(self):
        req = self.row(AssignmentRequest.ACCEPTED, NOW - timedelta(days=3))
        AssignmentRequest.objects.update(answer_mailed_at=None)
        self.migration().stamp_answered(real_apps, None)
        req.refresh_from_db()
        first = req.answer_mailed_at
        with patch("django.utils.timezone.now", return_value=NOW + timedelta(days=1)):
            self.migration().stamp_answered(real_apps, None)
        req.refresh_from_db()
        self.assertEqual(first, req.answer_mailed_at)
        # ... which is also why it is only ever stamped from the migration, once.
        self.assertEqual(NOW - timedelta(days=3), first)


# --------------------------------------------------------------------------
# What leaves the building
# --------------------------------------------------------------------------

class PrivacyTest(AnswerMailBase):
    """Rule 1 for the combined email and the two shapes of the acceptance on its
    own: a child with a name nobody else has, a custodian, a purpose and notes
    on the session, and a token in every free-text column go through all three
    routes, and every payload handed to the sender is searched for them."""

    REASON = "Quillon caseload is already full until March"

    def test_no_acceptance_email_carries_the_child(self):
        planted = planted_text()
        self.assertGreaterEqual(len(planted), 15, "the columns to plant were not found")
        Child.objects.filter(pk=self.child.pk).update(
            referral_reason=self.REASON, **planted)
        booked_with = self._fresh_child(referral_reason=self.REASON, **planted)
        alone = self._fresh_child(referral_reason=self.REASON, **planted)
        swept = self._fresh_child(referral_reason=self.REASON, **planted)

        with self.outbox() as sent:
            # 1. Accepted, then the psychologist books: one email for both.
            self.accepted(self.child)
            self.assertEqual(201, self.book(self.psy).status_code)
            # 2. Accepted, then the dialog ends with no booking.
            req = self.accepted(booked_with)
            self.assertEqual(200, self.mail_answer(req).status_code)
            # 3. Accepted, somebody else books, then the sweep.
            req = self.accepted(alone)
            self.assertEqual(201, self.book(self.admin, child=alone,
                                            start=self.later(1)).status_code)
            self.grow_old(req, 30)
            cache.clear()
            self._as(self.sw).get("/api/activity/")
            # 4. Accepted, nothing else: the sweep says no session is booked.
            req = self.accepted(swept)
            self.grow_old(req, 30)
            cache.clear()
            self._as(self.psy).get("/api/activity/")

        accepted = [(k, p) for k, p in sent if k == "case_accepted"]
        self.assertEqual(4, len(accepted), [(k, p["subject"]) for k, p in sent])
        subjects = [p["subject"] for _, p in accepted]
        self.assertEqual(1, sum("and booked the first session" in s for s in subjects))
        texts = "\n".join(p["textContent"] for _, p in accepted)
        for part in ("First session", "Next session", "No session is booked yet"):
            self.assertIn(part, texts)

        sentinels = ("Zyxwanita", "Qorvath", "Brunhilda", "Vexley", "Quillon", "March",
                     "Z. Qorvath", "Qorvath, Z", "Foster Care", "Neglected",
                     mail.BORN.isoformat(), mail.BORN.strftime("%d %b %Y"),
                     mail.BORN.strftime("%B %d, %Y"), "school records",
                     *planted.values())
        for kind, payload in sent:
            everything = json.dumps(payload, ensure_ascii=False).lower()
            for sentinel in sentinels:
                self.assertNotIn(sentinel.lower(), everything,
                                 f"{kind} ({payload['subject']}) carries {sentinel!r}")
            for part in ("subject", "htmlContent", "textContent"):
                self.assertTrue(payload[part].strip(), f"{kind} has no {part}")

