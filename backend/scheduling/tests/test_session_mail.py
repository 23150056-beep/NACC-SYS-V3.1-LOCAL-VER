"""Who is emailed when a session is booked, moved or cancelled, and what the
calendar invite on it says (scheduling/session_mail.py; owner's request, 21 Oct
2026).

Every test goes through the real appointment endpoints, so a booking has to
satisfy the real rules - the psychologist's window, the case referral, no
overlap, not in the past. The clock is pinned to Monday 19 Oct 2026, 10:00 in
Manila, and the sessions are on the Wednesday after it.

What leaves the building is read where it leaves: `_queue`, the one seam, is
patched to collect (kind, payload), and the invite is decoded out of the
payload's base64 attachment. Three things are held:

* WHO. The psychologist whose calendar it is, and the social worker whose
  child it is, and neither of them for what they did themselves - except the
  one "Calendar update" a psychologist gets when they change a session they
  were already sent an invite for, because that event would otherwise stay at
  the old time in their own calendar.
* THE INVITE. METHOD and STATUS, a SEQUENCE that only goes up and only counts
  invites really queued, one UID for one appointment.
* NOTHING ABOUT THE CHILD. A sentinel name, purpose, notes and custodian go
  through every change, and the last class searches everything for them.
"""
import base64
import hashlib
import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts import email_notifications
from accounts.models import EmailOptOut, Role
from children.models import Child
from children.tests.payloads import planted_text
from scheduling.models import Appointment, AvailabilityBlock, CalendarInvite
from scheduling.calendar_invite import legacy_uid
from scheduling.session_mail import _invite, session_changed
from scheduling.tests.test_api import give_referral

User = get_user_model()

ORIGIN = "https://nacc.example.test"
# Monday 19 Oct 2026, 10:00 in Manila.
NOW = datetime(2026, 10, 19, 2, 0, tzinfo=dt_timezone.utc)

NAME = "Zyxwanita Qorvath"
NOTES = "Brunhilda Vexley asked for the school records"
CUSTODIAN = "Brunhilda Vexley"
BORN = date(2016, 3, 2)


def attachment(payload):
    """The decoded .ics of a payload, as the text a calendar would read."""
    (attached,) = payload["attachment"]
    assert attached["name"] == "invite.ics", attached["name"]
    return base64.b64decode(attached["content"]).decode("utf-8")


def unfolded(ics):
    return ics[:-2].replace("\r\n ", "").split("\r\n")


def prop(ics, name):
    """The value of the one `NAME:` line (no parameters) in an invite."""
    found = [t.split(":", 1)[1] for t in unfolded(ics) if t.startswith(name + ":")]
    assert len(found) == 1, (name, found)
    return found[0]


class SessionMailBase(TestCase):
    """A child held by `sw` and assigned to `psy`; both psychologists work
    Wednesday mornings; the clock stands at NOW."""

    def setUp(self):
        roles = self.roles = {r: Role.objects.create(role_name=r)
                              for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        self.sw = make("sw@t.ph", "Editha", "Pascua", Role.STAFF)
        self.other_sw = make("sw2@t.ph", "Rosa", "Santos", Role.STAFF)
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        self.psy2 = make("psy2@t.ph", "Jose", "Rizal", Role.PSYCHOLOGIST)
        self.child = self._child(self.sw)
        for person in (self.psy, self.psy2):
            AvailabilityBlock.objects.create(
                psychologist=person, weekday=2, start_time="09:00", end_time="12:00",
                capacity=4)
        self.start = timezone.make_aware(datetime(2026, 10, 21, 9, 30))   # a Wednesday
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def _child(self, worker, first="Zyxwanita", last="Qorvath", **fields):
        child = Child.objects.create(
            first_name=first, last_name=last, fullname=f"{first} {last}",
            birth_date=BORN, case_type="Foster Care", case_category="Neglected",
            custodian_name=CUSTODIAN, social_worker=worker,
            assigned_psychologist=self.psy, **fields)
        give_referral(child, worker)
        return child

    @property
    def ref(self):
        return f"C-{self.child.pk:04d}"

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    @contextmanager
    def outbox(self):
        """A mail service is set up, and everything handed to the sender is
        collected as (kind, payload). `_queue` is patched, so the invite is
        counted and built exactly as it is for a real send."""
        sent = []
        with override_settings(BREVO_API_KEY="test-key", CORS_ALLOWED_ORIGINS=[ORIGIN],
                               BREVO_SENDER_EMAIL="from@racco1.gov.ph"), \
                patch("accounts.email_notifications._queue",
                      side_effect=lambda payload, description: sent.append(
                          (description.removesuffix(" email"), payload))), \
                self.captureOnCommitCallbacks(execute=True):
            yield sent

    def to(self, sent, user):
        return [(kind, payload) for kind, payload in sent
                if payload["to"][0]["email"] == user.email]

    def kinds_to(self, sent, user):
        return [kind for kind, _ in self.to(sent, user)]

    def one(self, sent, user):
        """The single payload sent to `user`."""
        found = self.to(sent, user)
        self.assertEqual(1, len(found), f"{user.email}: {[k for k, _ in found]}")
        return found[0][1]

    # -- the endpoints ---------------------------------------------------------

    def book(self, by, start=None, duration=60, psychologist=None, child=None,
             purpose="session", notes=NOTES):
        body = {"child": (child or self.child).pk,
                "start": (start or self.start).isoformat(),
                "duration_minutes": duration, "purpose": purpose, "notes": notes}
        if by.role.role_name != Role.PSYCHOLOGIST:
            body["psychologist"] = (psychologist or self.psy).pk
        return self._as(by).post("/api/appointments/", body, format="json")

    def booked(self, by=None, **kwargs):
        """A session booked with the mail service on, so the invite counts: the
        appointment, and what that booking sent."""
        with self.outbox() as sent:
            res = self.book(by or self.sw, **kwargs)
        self.assertEqual(201, res.status_code, res.data)
        return Appointment.objects.get(pk=res.data["id"]), sent

    def change(self, by, appointment, **body):
        if isinstance(body.get("start"), datetime):
            body["start"] = body["start"].isoformat()
        if "psychologist" in body:
            body["psychologist"] = body["psychologist"].pk
        return self._as(by).patch(f"/api/appointments/{appointment.pk}/", body, format="json")

    def cancel(self, by, appointment):
        return self._as(by).post(f"/api/appointments/{appointment.pk}/cancel/")

    def invite_row(self, appointment, user):
        return CalendarInvite.objects.get(appointment=appointment, attendee=user)

    def later(self, hours=1):
        return self.start + timedelta(hours=hours)


# --------------------------------------------------------------------------
# Booked
# --------------------------------------------------------------------------

class BookedTest(SessionMailBase):
    def test_a_social_worker_booking_sends_the_psychologist_an_invite_and_themselves_nothing(self):
        appointment, sent = self.booked(self.sw)
        self.assertEqual(["my_calendar"], [kind for kind, _ in sent])
        self.assertEqual([], self.to(sent, self.sw))
        payload = self.one(sent, self.psy)
        ics = attachment(payload)
        self.assertEqual("REQUEST", prop(ics, "METHOD"))
        self.assertEqual("CONFIRMED", prop(ics, "STATUS"))
        self.assertEqual("1", prop(ics, "SEQUENCE"))
        self.assertEqual("20261021T013000Z", prop(ics, "DTSTART"))
        self.assertEqual("20261021T023000Z", prop(ics, "DTEND"))
        self.assertEqual(f"NACC session · {self.ref}", prop(ics, "SUMMARY"))
        self.assertIn("mailto:psy@t.ph", [t for t in unfolded(ics)
                                         if t.startswith("ATTENDEE")][0])
        self.assertIn("mailto:from@racco1.gov.ph", [t for t in unfolded(ics)
                                                   if t.startswith("ORGANIZER")][0])
        row = self.invite_row(appointment, self.psy)
        self.assertEqual((1, False), (row.sequence, row.cancelled))
        self.assertEqual(1, CalendarInvite.objects.count())

    def test_the_email_says_who_booked_what_and_when(self):
        _, sent = self.booked(self.sw)
        payload = self.one(sent, self.psy)
        text = payload["textContent"]
        self.assertEqual("Editha Pascua booked a session on your calendar", payload["subject"])
        self.assertIn(f"Editha Pascua booked a session on your calendar for case {self.ref}.",
                      text)
        self.assertIn("A calendar invite is attached.", text)
        self.assertIn(f"Case: {self.ref}", text)
        self.assertIn("When: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", text)
        self.assertNotIn("Was:", text)
        self.assertIn(f"Open the schedule: {ORIGIN}/schedule?for={self.psy.pk}", text)
        self.assertIn(f'href="{ORIGIN}/schedule?for={self.psy.pk}"', payload["htmlContent"])
        self.assertEqual([{"email": "psy@t.ph", "name": "Marivic Bulan"}], payload["to"])

    def test_a_psychologist_booking_their_own_calendar_mails_the_social_worker_with_no_invite(self):
        with self.outbox() as sent:
            res = self.book(self.psy)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["child_sessions"], [kind for kind, _ in sent])
        self.assertEqual([], self.to(sent, self.psy))
        payload = self.one(sent, self.sw)
        self.assertNotIn("attachment", payload)
        self.assertEqual(f"Marivic Bulan booked a session for case {self.ref}",
                         payload["subject"])
        self.assertIn(f"Marivic Bulan booked a session for case {self.ref}, which is in "
                      "your records.", payload["textContent"])
        self.assertIn("When: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", payload["textContent"])
        self.assertNotIn("invite", payload["textContent"].lower())
        self.assertFalse(CalendarInvite.objects.exists(), "no invite was sent")

    def test_the_isa_booking_tells_the_psychologist_and_the_social_worker(self):
        with self.outbox() as sent:
            res = self.book(self.admin)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        self.assertEqual(2, len(sent))
        self.assertIn("attachment", self.one(sent, self.psy))
        self.assertNotIn("attachment", self.one(sent, self.sw))
        self.assertEqual("Ada Admin booked a session on your calendar",
                         self.one(sent, self.psy)["subject"])
        self.assertEqual(f"Ada Admin booked a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])

    def test_a_child_with_no_social_worker_tells_only_the_psychologist(self):
        child = self._child(None, "Cara", "Diaz")
        with self.outbox() as sent:
            res = self.book(self.admin, child=child)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["my_calendar"], [k for k, _ in sent])

    def test_a_social_worker_who_is_not_the_childs_is_not_told(self):
        with self.outbox() as sent:
            self.book(self.admin)
        self.assertEqual([], self.to(sent, self.other_sw))

    def test_a_refused_booking_mails_nobody_and_records_no_invite(self):
        past = timezone.make_aware(datetime(2026, 10, 14, 9, 30))
        outside = timezone.make_aware(datetime(2026, 10, 21, 14, 0))    # after the window
        with self.outbox() as sent:
            self.assertEqual(400, self.book(self.sw, start=past).status_code)
            self.assertEqual(400, self.book(self.sw, start=outside).status_code)
            self.assertEqual(403, self.book(self.other_sw).status_code)   # not their child
        self.assertEqual([], sent)
        self.assertFalse(Appointment.objects.exists())
        self.assertFalse(CalendarInvite.objects.exists())


# --------------------------------------------------------------------------
# Moved
# --------------------------------------------------------------------------

class MovedTest(SessionMailBase):
    def test_a_social_worker_moving_it_sends_the_psychologist_the_same_event_again(self):
        appointment, _ = self.booked(self.sw)
        first_uid = prop(attachment(self.one(_, self.psy)), "UID")
        with self.outbox() as sent:
            res = self.change(self.sw, appointment, start=self.later())
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["my_calendar"], [k for k, _ in sent])
        payload = self.one(sent, self.psy)
        ics = attachment(payload)
        self.assertEqual("REQUEST", prop(ics, "METHOD"))
        self.assertEqual("2", prop(ics, "SEQUENCE"))
        self.assertEqual(first_uid, prop(ics, "UID"), "a new event, not an update")
        self.assertEqual("20261021T023000Z", prop(ics, "DTSTART"))
        self.assertEqual("Editha Pascua moved a session on your calendar", payload["subject"])
        self.assertIn("An updated calendar invite is attached.", payload["textContent"])
        self.assertIn("When: Wed 21 Oct 2026, 10:30 AM to 11:30 AM", payload["textContent"])
        self.assertIn("Was: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", payload["textContent"])
        self.assertEqual(2, self.invite_row(appointment, self.psy).sequence)

    def test_the_sequence_goes_up_by_one_every_time_it_is_sent(self):
        appointment, _ = self.booked(self.sw)
        seen = []
        for hours in (1, -0.5, 0.5):                        # 10:30, 9:00, 10:00
            with self.outbox() as sent:
                self.change(self.admin, appointment, start=self.later(hours))
            seen.append(prop(attachment(self.one(sent, self.psy)), "SEQUENCE"))
        self.assertEqual(["2", "3", "4"], seen)

    def test_the_social_worker_hears_when_someone_else_moves_it_and_without_an_invite(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.change(self.admin, appointment, start=self.later())
        payload = self.one(sent, self.sw)
        self.assertNotIn("attachment", payload)
        self.assertEqual(f"Ada Admin moved a session for case {self.ref}", payload["subject"])
        self.assertIn("Was: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", payload["textContent"])
        self.assertEqual(2, len(sent))

    def test_a_longer_session_is_a_move_and_says_what_it_was(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.change(self.sw, appointment, duration_minutes=90)
        payload = self.one(sent, self.psy)
        ics = attachment(payload)
        self.assertEqual("20261021T030000Z", prop(ics, "DTEND"))
        self.assertIn("When: Wed 21 Oct 2026, 9:30 AM to 11:00 AM", payload["textContent"])
        self.assertIn("Was: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", payload["textContent"])

    def test_the_psychologist_moving_a_session_they_were_sent_an_invite_for_gets_the_update(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            res = self.change(self.psy, appointment, start=self.later())
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        payload = self.one(sent, self.psy)
        self.assertEqual(f"Calendar update: session {self.ref}", payload["subject"])
        self.assertIn(f"You moved the session for case {self.ref}. This email only carries "
                      "the updated calendar invite", payload["textContent"])
        self.assertNotIn("Marivic Bulan moved", payload["textContent"])
        ics = attachment(payload)
        self.assertEqual("REQUEST", prop(ics, "METHOD"))
        self.assertEqual("2", prop(ics, "SEQUENCE"))
        self.assertEqual("20261021T023000Z", prop(ics, "DTSTART"))
        self.assertIn("Was: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", payload["textContent"])
        # The social worker still hears that someone else moved it.
        self.assertEqual(f"Marivic Bulan moved a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])

    def test_the_psychologist_moving_one_they_were_never_sent_an_invite_for_gets_nothing(self):
        with self.outbox() as sent:
            appointment = Appointment.objects.get(pk=self.book(self.psy).data["id"])
            sent.clear()
            res = self.change(self.psy, appointment, start=self.later())
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual([], self.to(sent, self.psy))
        self.assertEqual(["child_sessions"], self.kinds_to(sent, self.sw))
        self.assertFalse(CalendarInvite.objects.exists())

    def test_a_psychologist_whose_invite_was_cancelled_is_not_sent_an_update_for_it(self):
        appointment, _ = self.booked(self.sw)
        CalendarInvite.objects.filter(appointment=appointment).update(cancelled=True)
        with self.outbox() as sent:
            self.change(self.psy, appointment, start=self.later())
        self.assertEqual([], self.to(sent, self.psy))

    def test_notes_purpose_and_an_unchanged_time_are_not_a_move(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.assertEqual(200, self.change(self.sw, appointment,
                                             notes="Bring the form").status_code)
            self.assertEqual(200, self.change(self.sw, appointment,
                                             purpose="follow_up").status_code)
            self.assertEqual(200, self.change(self.sw, appointment,
                                             start=self.start).status_code)
            self.assertEqual(200, self.change(self.sw, appointment,
                                             duration_minutes=60).status_code)
        self.assertEqual([], sent)
        self.assertEqual(1, self.invite_row(appointment, self.psy).sequence)

    def test_a_refused_move_mails_nobody_and_does_not_count_an_invite(self):
        appointment, _ = self.booked(self.sw)
        other = self._child(self.sw, "Ben", "Lim")
        Appointment.objects.create(child=other, psychologist=self.psy,
                                   start=self.later(), duration_minutes=60)
        with self.outbox() as sent:
            self.assertEqual(400, self.change(self.sw, appointment,
                                             start=self.later()).status_code)
            self.assertEqual(400, self.change(
                self.sw, appointment,
                start=timezone.make_aware(datetime(2026, 10, 14, 9, 30))).status_code)
        self.assertEqual([], sent)
        self.assertEqual(1, self.invite_row(appointment, self.psy).sequence)

    def test_a_session_whose_time_has_passed_is_not_mailed_when_it_is_edited(self):
        """One never marked completed stays "scheduled" in the past, and its
        start is exempt from the checks while it is unchanged, so a length or a
        psychologist can still be edited. An invite for last Wednesday's date,
        or a "booked" one to the psychologist it was handed to, helps nobody -
        the custodian's text has the same rule."""
        last_week = timezone.make_aware(datetime(2026, 10, 14, 9, 30))
        past = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=last_week,
            duration_minutes=60, booked_by=self.sw)
        CalendarInvite.objects.create(appointment=past, attendee=self.psy, sequence=1)
        with self.outbox() as sent:
            self.assertEqual(200, self.change(self.sw, past, duration_minutes=90).status_code)
            self.assertEqual(200, self.change(self.admin, past,
                                             psychologist=self.psy2).status_code)
        past.refresh_from_db()
        self.assertEqual((90, self.psy2.pk), (past.duration_minutes, past.psychologist_id))
        self.assertEqual([], sent)
        self.assertEqual(1, self.invite_row(past, self.psy).sequence)
        self.assertFalse(CalendarInvite.objects.filter(attendee=self.psy2).exists())

    def test_moving_a_cancelled_session_says_nothing(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox():
            self.cancel(self.sw, appointment)
        with self.outbox() as sent:
            res = self.change(self.admin, appointment, start=self.later())
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual([], sent)


class ChangedPsychologistTest(SessionMailBase):
    def test_the_one_it_left_is_sent_a_cancellation_and_the_one_it_came_to_a_booking(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            res = self.change(self.admin, appointment, psychologist=self.psy2)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(3, len(sent), [k for k, _ in sent])

        left = self.one(sent, self.psy)
        self.assertEqual("Ada Admin cancelled a session on your calendar", left["subject"])
        gone = attachment(left)
        self.assertEqual("CANCEL", prop(gone, "METHOD"))
        self.assertEqual("CANCELLED", prop(gone, "STATUS"))
        self.assertEqual("2", prop(gone, "SEQUENCE"))
        self.assertTrue(self.invite_row(appointment, self.psy).cancelled)

        came = self.one(sent, self.psy2)
        self.assertEqual("Ada Admin booked a session on your calendar", came["subject"])
        new = attachment(came)
        self.assertEqual("REQUEST", prop(new, "METHOD"))
        self.assertEqual("1", prop(new, "SEQUENCE"), "their first invite for it")
        self.assertEqual(prop(gone, "UID"), prop(new, "UID"))
        self.assertNotIn("Was:", came["textContent"])
        self.assertEqual((1, False), (self.invite_row(appointment, self.psy2).sequence,
                                      self.invite_row(appointment, self.psy2).cancelled))

        self.assertEqual(f"Ada Admin moved a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])

    def test_a_psychologist_change_alone_is_not_called_a_new_time(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.change(self.admin, appointment, psychologist=self.psy2)
        self.assertNotIn("Was:", self.one(sent, self.sw)["textContent"])

    def test_the_social_worker_is_told_who_it_went_to_and_who_had_it(self):
        """With no new time there is no "Was" row, and "moved" over a Case and a
        When that are the same as before says nothing about what moved."""
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.change(self.admin, appointment, psychologist=self.psy2)
        text = self.one(sent, self.sw)["textContent"]
        self.assertIn("Psychologist: Jose Rizal (was Marivic Bulan)", text)
        self.assertIn("When: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", text)

    def test_the_social_worker_is_not_told_about_a_psychologist_when_none_changed(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.change(self.admin, appointment, start=self.later())
        self.assertNotIn("Psychologist:", self.one(sent, self.sw)["textContent"])

    def test_the_one_it_left_is_told_the_session_was_given_on_not_called_off(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.change(self.admin, appointment, psychologist=self.psy2)
        left, came = self.one(sent, self.psy), self.one(sent, self.psy2)
        self.assertEqual("Ada Admin cancelled a session on your calendar", left["subject"])
        self.assertIn("The session itself goes ahead; it was given to Jose Rizal.",
                      left["textContent"])
        self.assertNotIn("goes ahead", came["textContent"])

    def test_the_cancellation_names_the_time_it_had_and_the_booking_the_time_it_has(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            res = self.change(self.admin, appointment, psychologist=self.psy2,
                             start=self.later())
        self.assertEqual(200, res.status_code, res.data)
        left, came = self.one(sent, self.psy), self.one(sent, self.psy2)
        self.assertEqual("20261021T013000Z", prop(attachment(left), "DTSTART"))
        self.assertIn("When: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", left["textContent"])
        self.assertEqual("20261021T023000Z", prop(attachment(came), "DTSTART"))
        self.assertIn("When: Wed 21 Oct 2026, 10:30 AM to 11:30 AM", came["textContent"])
        self.assertIn("Was: Wed 21 Oct 2026, 9:30 AM to 10:30 AM",
                      self.one(sent, self.sw)["textContent"])
        # The appointment itself is where it was moved to.
        appointment.refresh_from_db()
        self.assertEqual(self.later(), appointment.start)

    def test_there_and_back_again_the_sequence_never_goes_down(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox():
            self.change(self.admin, appointment, psychologist=self.psy2)
        with self.outbox() as sent:
            self.change(self.admin, appointment, psychologist=self.psy)
        back = attachment(self.one(sent, self.psy))
        self.assertEqual("REQUEST", prop(back, "METHOD"))
        self.assertEqual("3", prop(back, "SEQUENCE"))
        row = self.invite_row(appointment, self.psy)
        self.assertEqual((3, False), (row.sequence, row.cancelled))
        gone = self.invite_row(appointment, self.psy2)
        self.assertEqual((2, True), (gone.sequence, gone.cancelled))
        self.assertEqual("CANCEL", prop(attachment(self.one(sent, self.psy2)), "METHOD"))

    def test_a_psychologist_who_never_held_an_invite_is_still_told_it_left_them_by_someone_else(self):
        # Booked by the psychologist themselves: nothing was sent, so nothing
        # is on record - but a colleague taking it off them is news.
        with self.outbox():
            appointment = Appointment.objects.get(pk=self.book(self.psy).data["id"])
        self.assertFalse(CalendarInvite.objects.exists())
        with self.outbox() as sent:
            self.change(self.admin, appointment, psychologist=self.psy2)
        left = self.one(sent, self.psy)
        self.assertEqual("CANCEL", prop(attachment(left), "METHOD"))
        self.assertEqual("1", prop(attachment(left), "SEQUENCE"))


# --------------------------------------------------------------------------
# Cancelled, and what is not a change
# --------------------------------------------------------------------------

class CancelledTest(SessionMailBase):
    def test_cancelling_a_future_session_removes_it_from_the_psychologists_calendar(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            res = self.cancel(self.sw, appointment)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["my_calendar"], [k for k, _ in sent])
        payload = self.one(sent, self.psy)
        self.assertEqual("Editha Pascua cancelled a session on your calendar", payload["subject"])
        self.assertIn("A calendar update is attached.", payload["textContent"])
        self.assertIn("When: Wed 21 Oct 2026, 9:30 AM to 10:30 AM", payload["textContent"])
        ics = attachment(payload)
        self.assertEqual("CANCEL", prop(ics, "METHOD"))
        self.assertEqual("CANCELLED", prop(ics, "STATUS"))
        self.assertEqual("2", prop(ics, "SEQUENCE"))
        self.assertEqual("20261021T013000Z", prop(ics, "DTSTART"))
        row = self.invite_row(appointment, self.psy)
        self.assertEqual((2, True), (row.sequence, row.cancelled))

    def test_the_isa_cancelling_tells_the_social_worker_too(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.cancel(self.admin, appointment)
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        self.assertEqual(f"Ada Admin cancelled a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])
        self.assertNotIn("attachment", self.one(sent, self.sw))

    def test_the_psychologist_cancelling_a_session_they_hold_an_invite_for_gets_the_update(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.cancel(self.psy, appointment)
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        payload = self.one(sent, self.psy)
        self.assertEqual(f"Calendar update: session {self.ref}", payload["subject"])
        self.assertIn(f"You cancelled the session for case {self.ref}.", payload["textContent"])
        ics = attachment(payload)
        self.assertEqual(("CANCEL", "CANCELLED", "2"),
                         (prop(ics, "METHOD"), prop(ics, "STATUS"), prop(ics, "SEQUENCE")))
        self.assertEqual(f"Marivic Bulan cancelled a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])

    def test_the_psychologist_cancelling_one_they_booked_themselves_tells_only_the_social_worker(self):
        with self.outbox() as sent:
            appointment = Appointment.objects.get(pk=self.book(self.psy).data["id"])
            sent.clear()
            self.cancel(self.psy, appointment)
        self.assertEqual([], self.to(sent, self.psy))
        self.assertEqual(["child_sessions"], self.kinds_to(sent, self.sw))

    def test_cancelling_twice_says_it_once(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            self.cancel(self.sw, appointment)
            self.cancel(self.sw, appointment)
        self.assertEqual(1, len(sent))
        self.assertEqual(2, self.invite_row(appointment, self.psy).sequence)

    def test_the_psychologist_cancelling_again_after_it_was_cancelled_is_not_updated(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox():
            self.cancel(self.sw, appointment)
        with self.outbox() as sent:
            self.cancel(self.psy, appointment)
        self.assertEqual([], sent)

    def test_a_session_that_has_already_started_sends_nothing_when_it_is_cancelled(self):
        # Eight o'clock this morning; it is ten. The custodian's text follows
        # the same rule: a session called off after it began is paperwork.
        began = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=NOW - timedelta(hours=2),
            duration_minutes=60, booked_by=self.sw)
        CalendarInvite.objects.create(appointment=began, attendee=self.psy, sequence=1)
        with self.outbox() as sent:
            res = self.cancel(self.sw, began)
        self.assertEqual(200, res.status_code, res.data)
        began.refresh_from_db()
        self.assertEqual(Appointment.CANCELLED, began.status)
        self.assertEqual([], sent)
        self.assertEqual(1, self.invite_row(began, self.psy).sequence)

    def test_completed_and_no_show_are_outcomes_not_changes_and_send_nothing(self):
        for verb, status in (("complete", Appointment.COMPLETED),
                             ("no_show", Appointment.NO_SHOW)):
            held = Appointment.objects.create(
                child=self.child, psychologist=self.psy,
                start=NOW - timedelta(hours=2), duration_minutes=60, booked_by=self.sw)
            CalendarInvite.objects.create(appointment=held, attendee=self.psy, sequence=1)
            with self.outbox() as sent:
                res = self._as(self.psy).post(f"/api/appointments/{held.pk}/{verb}/")
            self.assertEqual(200, res.status_code, (verb, res.data))
            held.refresh_from_db()
            self.assertEqual(status, held.status)
            self.assertEqual([], sent, verb)
            self.assertEqual(1, self.invite_row(held, self.psy).sequence)


# --------------------------------------------------------------------------
# Counting the invites
# --------------------------------------------------------------------------

class InviteCountingTest(SessionMailBase):
    """`_invite` is called by `notify` once the email is certain to go, and is
    the one place a person's SEQUENCE is written."""

    def setUp(self):
        super().setUp()
        self.appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=self.start,
            duration_minutes=60, booked_by=self.sw)

    def test_each_invite_is_numbered_one_after_the_last_and_the_row_agrees(self):
        seen = []
        for method in ("REQUEST", "REQUEST", "CANCEL", "REQUEST"):
            name, ics = _invite(self.appointment, self.psy, method)()
            self.assertEqual("invite.ics", name)
            seen.append((prop(ics, "METHOD"), prop(ics, "SEQUENCE")))
            row = self.invite_row(self.appointment, self.psy)
            self.assertEqual(int(seen[-1][1]), row.sequence, "the file carries the number written")
            self.assertEqual(method == "CANCEL", row.cancelled)
        self.assertEqual([("REQUEST", "1"), ("REQUEST", "2"), ("CANCEL", "3"),
                          ("REQUEST", "4")], seen)
        self.assertEqual(1, CalendarInvite.objects.count())

    def test_sent_at_is_stamped_on_every_send_as_the_model_says(self):
        _invite(self.appointment, self.psy, "REQUEST")()
        first = self.invite_row(self.appointment, self.psy).sent_at
        with patch("django.utils.timezone.now", return_value=NOW + timedelta(hours=3)):
            _invite(self.appointment, self.psy, "REQUEST")()
        self.assertEqual(NOW + timedelta(hours=3),
                         self.invite_row(self.appointment, self.psy).sent_at)
        self.assertEqual(NOW, first)

    def test_an_invite_that_cannot_be_built_leaves_the_count_where_it_was(self):
        _invite(self.appointment, self.psy, "REQUEST")()
        with patch("scheduling.session_mail.build_ics", side_effect=ValueError("no")):
            with self.assertRaises(ValueError):
                _invite(self.appointment, self.psy, "CANCEL")()
            with self.assertRaises(ValueError):
                _invite(self.appointment, self.psy2, "REQUEST")()
        row = self.invite_row(self.appointment, self.psy)
        self.assertEqual((1, False), (row.sequence, row.cancelled),
                         "a cancellation that never went was counted")
        self.assertFalse(CalendarInvite.objects.filter(attendee=self.psy2).exists(),
                         "an invite nobody has was counted")


# --------------------------------------------------------------------------
# The event's identity
# --------------------------------------------------------------------------

class EventIdentityTest(SessionMailBase):
    """One appointment is one event in everybody's calendar, for good. The UID
    is drawn with the appointment's first invite and STORED
    (`CalendarInvite.uid`), so nothing about the deployment - its address
    included - can change it. It used to be worked out from the site's address
    on every send, which setting `SITE_URL` would have changed under every
    booked session, leaving each with a second event."""

    SHAPE = r"^appointment-{pk}-[0-9a-f]{{10}}@nacc-sys$"

    def uid_of(self, payload):
        return prop(attachment(payload), "UID")

    def old_uid(self, appointment):
        """What the code before the column worked out for `appointment` on the
        deployment these tests send as (its address is ORIGIN)."""
        return (f"appointment-{appointment.pk}-"
                f"{hashlib.sha256(ORIGIN.encode()).hexdigest()[:10]}@nacc-sys")

    def test_the_first_invite_draws_an_id_and_stores_it(self):
        appointment, sent = self.booked(self.sw)
        uid = self.uid_of(self.one(sent, self.psy))
        self.assertRegex(uid, self.SHAPE.format(pk=appointment.pk))
        self.assertEqual(uid, self.invite_row(appointment, self.psy).uid)

    def test_a_move_sends_the_same_id_again(self):
        appointment, sent = self.booked(self.sw)
        first = self.uid_of(self.one(sent, self.psy))
        with self.outbox() as sent:
            self.change(self.sw, appointment, start=self.later())
        self.assertEqual(first, self.uid_of(self.one(sent, self.psy)))
        with self.outbox() as sent:
            self.cancel(self.sw, appointment)
        self.assertEqual(first, self.uid_of(self.one(sent, self.psy)))
        self.assertEqual(first, self.invite_row(appointment, self.psy).uid)

    def test_handing_the_session_over_gives_the_new_psychologist_the_same_id(self):
        appointment, sent = self.booked(self.sw)
        first = self.uid_of(self.one(sent, self.psy))
        with self.outbox() as sent:
            self.change(self.admin, appointment, psychologist=self.psy2)
        self.assertEqual(first, self.uid_of(self.one(sent, self.psy)))     # the cancellation
        self.assertEqual(first, self.uid_of(self.one(sent, self.psy2)))    # their first invite
        self.assertEqual({first}, set(CalendarInvite.objects.filter(
            appointment=appointment).values_list("uid", flat=True)))

    def test_changing_the_sites_address_between_two_sends_does_not_change_it(self):
        appointment, sent = self.booked(self.sw)
        first = self.uid_of(self.one(sent, self.psy))
        with self.outbox() as sent, override_settings(
                SITE_URL="https://moved.example.gov.ph",
                CORS_ALLOWED_ORIGINS=["https://elsewhere.example.test"]):
            self.change(self.sw, appointment, start=self.later())
        payload = self.one(sent, self.psy)
        self.assertEqual(first, self.uid_of(payload))
        # The mail itself did follow the new address; only the identity held.
        self.assertIn("https://moved.example.gov.ph", payload["textContent"])
        self.assertEqual("2", prop(attachment(payload), "SEQUENCE"))

    def test_two_appointments_are_two_events(self):
        one, sent_one = self.booked(self.sw)
        two, sent_two = self.booked(self.sw, start=self.later(1))
        first, second = (self.uid_of(self.one(sent_one, self.psy)),
                         self.uid_of(self.one(sent_two, self.psy)))
        self.assertNotEqual(first, second)
        self.assertRegex(first, self.SHAPE.format(pk=one.pk))
        self.assertRegex(second, self.SHAPE.format(pk=two.pk))

    def test_the_tag_is_drawn_not_derived_so_two_deployments_never_share_one(self):
        """Two copies both number their appointments from 1. The address tag
        used to keep their ids apart; a random one does it without following
        the address."""
        tags = set()
        for _ in range(6):
            appointment = Appointment.objects.create(
                child=self.child, psychologist=self.psy, start=self.start,
                duration_minutes=60, booked_by=self.sw)
            _, ics = _invite(appointment, self.psy, "REQUEST")()
            uid = prop(ics, "UID")
            self.assertRegex(uid, self.SHAPE.format(pk=appointment.pk))
            self.assertNotEqual(legacy_uid(appointment), uid)
            tags.add(uid.split("-")[2])
        self.assertEqual(6, len(tags), "a tag repeated")

    def test_a_row_from_before_the_column_keeps_the_id_its_invite_went_out_under(self):
        """The invite is already in somebody's calendar under the value the old
        code worked out from the address. Carrying on under it is what makes
        the next move an update and not a second event."""
        appointment, _ = self.booked(self.sw)
        CalendarInvite.objects.filter(appointment=appointment).update(uid="")
        old = self.old_uid(appointment)
        with override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN]):
            self.assertEqual(old, legacy_uid(appointment))
        with self.outbox() as sent:
            self.change(self.sw, appointment, start=self.later())
        payload = self.one(sent, self.psy)
        self.assertEqual(old, self.uid_of(payload))
        self.assertEqual("2", prop(attachment(payload), "SEQUENCE"))
        self.assertEqual(old, self.invite_row(appointment, self.psy).uid, "written back")

    def test_the_old_id_survives_a_site_setting_arriving_in_the_same_release(self):
        """A deployment that sets SITE_URL when it takes this change: the
        calendars hold the id built from the CORS address, and that is the one
        carried on - not one built from the new setting."""
        appointment, _ = self.booked(self.sw)
        CalendarInvite.objects.filter(appointment=appointment).update(uid="")
        old = self.old_uid(appointment)
        with self.outbox() as sent, override_settings(SITE_URL="https://new.example.gov.ph"):
            self.change(self.sw, appointment, start=self.later())
        self.assertEqual(old, self.uid_of(self.one(sent, self.psy)))

    def test_a_hand_over_of_a_row_from_before_the_column_keeps_one_id_for_both(self):
        appointment, _ = self.booked(self.sw)
        CalendarInvite.objects.filter(appointment=appointment).update(uid="")
        old = self.old_uid(appointment)
        with self.outbox() as sent:
            self.change(self.admin, appointment, psychologist=self.psy2)
        self.assertEqual(old, self.uid_of(self.one(sent, self.psy)))
        self.assertEqual(old, self.uid_of(self.one(sent, self.psy2)))
        self.assertEqual({old}, set(CalendarInvite.objects.filter(
            appointment=appointment).values_list("uid", flat=True)))

    def test_a_row_from_before_the_column_is_not_what_a_new_invite_draws_from(self):
        """Only an appointment with an invite already out inherits the old id.
        A different appointment's first invite is drawn as a new one."""
        old_appointment, _ = self.booked(self.sw)
        CalendarInvite.objects.filter(appointment=old_appointment).update(uid="")
        new_appointment, sent = self.booked(self.sw, start=self.later(1))
        uid = self.uid_of(self.one(sent, self.psy))
        self.assertNotEqual(self.old_uid(new_appointment), uid)
        self.assertTrue(re.match(self.SHAPE.format(pk=new_appointment.pk), uid), uid)

    def test_an_invite_that_cannot_be_built_leaves_no_id_behind(self):
        """The first invite, failing: the row and the id drawn for it go with
        it, and the next try draws afresh."""
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=self.start,
            duration_minutes=60, booked_by=self.sw)
        with patch("scheduling.session_mail.build_ics", side_effect=ValueError("no")):
            with self.assertRaises(ValueError):
                _invite(appointment, self.psy, "REQUEST")()
        self.assertFalse(CalendarInvite.objects.filter(appointment=appointment).exists())
        _, ics = _invite(appointment, self.psy, "REQUEST")()
        self.assertEqual(prop(ics, "UID"), self.invite_row(appointment, self.psy).uid)
        self.assertEqual("1", prop(ics, "SEQUENCE"))

    def test_nothing_is_stored_by_a_copy_with_no_mail_service(self):
        with override_settings(BREVO_API_KEY="", CORS_ALLOWED_ORIGINS=[ORIGIN]), \
                self.assertLogs("accounts.email_notifications", level="WARNING"):
            res = self.book(self.sw)
        self.assertEqual(201, res.status_code, res.data)
        self.assertFalse(CalendarInvite.objects.exists())

    def test_the_id_fits_its_column_and_the_default_is_empty(self):
        field = CalendarInvite._meta.get_field("uid")
        self.assertEqual("", field.default)
        self.assertEqual("", field.db_default)
        self.assertLessEqual(len(f"appointment-{10 ** 12}-{'f' * 10}@nacc-sys"), field.max_length)


# --------------------------------------------------------------------------
# People with no name
# --------------------------------------------------------------------------

class NamelessStaffTest(SessionMailBase):
    """`username` is the email address on every way an account is made, and a
    name is optional; the accounts above always have both. A booking by
    somebody with no name must not put their address in a colleague's subject,
    nor a psychologist's in the invite's attendee name."""

    def setUp(self):
        super().setUp()
        make = lambda address, role: User.objects.create_user(  # noqa: E731
            email=address, username=address, password="pass12345", role=self.roles[role])
        self.nameless_sw = make("noname@gmail.com", Role.STAFF)
        self.nameless_psy = make("nopsy@gmail.com", Role.PSYCHOLOGIST)
        AvailabilityBlock.objects.create(
            psychologist=self.nameless_psy, weekday=2, start_time="09:00",
            end_time="12:00", capacity=4)

    ADDRESSES = ("noname", "nopsy", "@gmail")

    def assertNoAddress(self, sent):
        self.assertTrue(sent, "nothing was sent, so nothing was checked")
        for kind, payload in sent:
            written = json.dumps({k: v for k, v in payload.items()
                                  if k not in ("to", "sender", "attachment")},
                                 ensure_ascii=False)
            # The invite's attendee line is the one place the address belongs.
            invite = "\n".join(t for t in unfolded(attachment(payload))
                               if not t.startswith("ATTENDEE")) if "attachment" in payload else ""
            for part in self.ADDRESSES:
                self.assertNotIn(part, written, f"{kind} carries {part!r}")
                self.assertNotIn(part, invite, f"{kind}'s invite carries {part!r}")

    def test_a_social_worker_with_no_name_booking_is_a_colleague(self):
        child = self._child(self.nameless_sw, "Eli", "Tan")
        with self.outbox() as sent:
            res = self.book(self.nameless_sw, child=child)
        self.assertEqual(201, res.status_code, res.data)
        self.assertNoAddress(sent)
        payload = self.one(sent, self.psy)
        self.assertEqual("A colleague booked a session on your calendar", payload["subject"])

    def test_a_psychologist_with_no_name_is_a_colleague_to_the_social_worker(self):
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=self.nameless_psy)
        with self.outbox() as sent:
            res = self.book(self.nameless_psy)
        self.assertEqual(201, res.status_code, res.data)
        self.assertNoAddress(sent)
        self.assertEqual(f"A colleague booked a session for case {self.ref}",
                         self.one(sent, self.sw)["subject"])

    def test_the_attendee_of_an_invite_with_no_name_is_called_one_and_the_mail_is_by_address(self):
        Child.objects.filter(pk=self.child.pk).update(assigned_psychologist=self.nameless_psy)
        with self.outbox() as sent:
            res = self.book(self.admin, psychologist=self.nameless_psy)
        self.assertEqual(201, res.status_code, res.data)
        self.assertNoAddress(sent)
        payload = self.one(sent, self.nameless_psy)
        self.assertEqual([{"email": "nopsy@gmail.com"}], payload["to"])
        (attendee,) = [t for t in unfolded(attachment(payload)) if t.startswith("ATTENDEE")]
        self.assertEqual('ATTENDEE;CN="Attendee";ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;'
                         'RSVP=FALSE:mailto:nopsy@gmail.com', attendee)

    def test_a_hand_over_to_a_psychologist_with_no_name_is_not_an_address_in_the_row(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent:
            res = self.change(self.admin, appointment, psychologist=self.nameless_psy)
        self.assertEqual(200, res.status_code, res.data)
        self.assertNoAddress(sent)
        self.assertIn("Psychologist: A psychologist (was Marivic Bulan)",
                      self.one(sent, self.sw)["textContent"])
        self.assertIn("it was given to another psychologist.",
                      self.one(sent, self.psy)["textContent"])


class HostileNameTest(SessionMailBase):
    """A name typed to be read as something else (23 Oct 2026, security
    review): it is printed in a colleague's subject line, the page, the text
    part and the invite's attendee line, so each comes back as one plain line
    (`accounts.display.entered_name`)."""

    def _rename(self, user, first, last=""):
        user.first_name, user.last_name = first, last
        user.save()

    def _as_long_as_a_name_can_be(self, text):
        """`text` repeated and cut to the column. These tests are about a name
        far longer than the 60 characters a mail prints, but it has to be
        STORED to be read back from the account, and tbl_user.first_name and
        last_name are varchar(150): SQLite lets 200 characters in, PostgreSQL
        refuses the UPDATE (CI run 40069844463), so the tests were green where
        the column was not enforced and red where it is."""
        limit = self.psy._meta.get_field("first_name").max_length
        long = (text * (limit // len(text) + 1))[:limit]
        self.assertGreater(len(long), 60)
        return long

    def test_a_booking_by_somebody_whose_name_has_a_newline_and_an_override_is_one_plain_line(self):
        self._rename(self.sw, "Editha\r\nBcc: attacker@evil.example", "Pas\u202ecua")
        appointment, sent = self.booked(self.sw)
        payload = self.one(sent, self.psy)
        self.assertEqual(
            "Editha Bcc: attacker@evil.example Pascua booked a session on your calendar",
            payload["subject"])
        for part in ("subject", "htmlContent", "textContent"):
            self.assertNotIn("\u202e", payload[part])
            self.assertNotIn("\r", payload[part])
        (lead,) = [t for t in payload["textContent"].split("\n")
                   if "booked a session on your calendar for case" in t]
        self.assertTrue(lead.startswith("Editha Bcc: attacker@evil.example Pascua booked"), lead)

    def test_a_psychologists_long_name_is_cut_in_the_row_a_social_worker_reads(self):
        appointment, _ = self.booked(self.sw)
        self._rename(self.psy2, self._as_long_as_a_name_can_be("Jose "))
        with self.outbox() as sent:
            res = self.change(self.admin, appointment, psychologist=self.psy2)
        self.assertEqual(200, res.status_code, res.data)
        payload = self.one(sent, self.sw)
        (row,) = [t for t in payload["textContent"].split("\n") if t.startswith("Psychologist:")]
        self.assertTrue(row.endswith("\u2026 (was Marivic Bulan)"), row)
        self.assertEqual(len("Psychologist: ") + 60 + len(" (was Marivic Bulan)"), len(row))

    def test_the_invite_names_the_attendee_as_one_short_plain_line(self):
        self._rename(self.psy, "Marivic\nBulan\u202e",
                     self._as_long_as_a_name_can_be("Santos "))
        appointment, sent = self.booked(self.sw)
        (attendee,) = [t for t in unfolded(attachment(self.one(sent, self.psy)))
                       if t.startswith("ATTENDEE")]
        name = attendee.split('CN="', 1)[1].split('"', 1)[0]
        self.assertTrue(name.startswith("Marivic Bulan Santos"), name)
        self.assertLessEqual(len(name), 60)
        self.assertNotIn("\u202e", attendee)


# --------------------------------------------------------------------------
# Switched off, and no mail service
# --------------------------------------------------------------------------

class OptOutTest(SessionMailBase):
    def test_a_psychologist_who_switched_the_calendar_off_gets_nothing_and_nothing_is_counted(self):
        EmailOptOut.objects.create(user=self.psy, kind="my_calendar")
        appointment, sent = self.booked(self.sw)
        self.assertEqual([], sent)
        self.assertFalse(CalendarInvite.objects.exists(),
                         "an invite nobody was sent must not be counted")
        with self.outbox() as sent:
            self.change(self.admin, appointment, start=self.later())
            self.cancel(self.admin, appointment)
        self.assertEqual([], self.to(sent, self.psy))
        self.assertFalse(CalendarInvite.objects.exists())

    def test_it_stops_the_calendar_update_for_their_own_change_too(self):
        appointment, _ = self.booked(self.sw)
        EmailOptOut.objects.create(user=self.psy, kind="my_calendar")
        with self.outbox() as sent:
            self.change(self.psy, appointment, start=self.later())
        self.assertEqual([], self.to(sent, self.psy))
        self.assertEqual(["child_sessions"], self.kinds_to(sent, self.sw))
        self.assertEqual(1, self.invite_row(appointment, self.psy).sequence)

    def test_switching_it_back_on_starts_counting_from_the_first_invite_really_sent(self):
        EmailOptOut.objects.create(user=self.psy, kind="my_calendar")
        appointment, _ = self.booked(self.sw)
        EmailOptOut.objects.all().delete()
        with self.outbox() as sent:
            self.change(self.sw, appointment, start=self.later())
        self.assertEqual("1", prop(attachment(self.one(sent, self.psy)), "SEQUENCE"))

    def test_the_social_worker_can_switch_their_own_off_without_touching_the_psychologists(self):
        EmailOptOut.objects.create(user=self.sw, kind="child_sessions")
        with self.outbox() as sent:
            self.book(self.admin)
        self.assertEqual(["my_calendar"], [k for k, _ in sent])

    def test_one_psychologists_switch_is_not_anothers(self):
        EmailOptOut.objects.create(user=self.psy, kind="my_calendar")
        with self.outbox() as sent:
            self.book(self.sw, psychologist=self.psy2)
        self.assertEqual(["my_calendar"], self.kinds_to(sent, self.psy2))

    def test_an_account_that_cannot_sign_in_is_not_emailed(self):
        self.psy.status = User.ARCHIVED
        self.psy.save()
        with self.outbox() as sent:
            self.book(self.sw)
        self.assertEqual([], sent)
        self.assertFalse(CalendarInvite.objects.exists())


class ConsoleModeTest(SessionMailBase):
    """The local copy has no BREVO_API_KEY: one line in the log per email that
    would have gone, and not a row recording an invite that never did."""

    @override_settings(BREVO_API_KEY="")
    def test_a_booking_logs_the_psychologists_email_and_records_nothing(self):
        with patch("accounts.email_notifications._queue") as queue, \
                self.assertLogs("accounts.email_notifications", level="WARNING") as logged:
            res = self.book(self.sw)
        self.assertEqual(201, res.status_code, res.data)
        queue.assert_not_called()
        self.assertEqual(
            ["EMAIL (console) my_calendar to psy@t.ph: "
             "Editha Pascua booked a session on your calendar"],
            [r.getMessage() for r in logged.records])
        self.assertFalse(CalendarInvite.objects.exists())

    @override_settings(BREVO_API_KEY="")
    def test_the_social_worker_is_logged_too_when_someone_else_books(self):
        with patch("accounts.email_notifications._queue") as queue, \
                self.assertLogs("accounts.email_notifications", level="WARNING") as logged:
            self.book(self.admin)
        queue.assert_not_called()
        self.assertEqual({"my_calendar", "child_sessions"},
                         {r.getMessage().split()[2] for r in logged.records})
        self.assertFalse(CalendarInvite.objects.exists())

    @override_settings(BREVO_API_KEY="")
    def test_moves_and_cancellations_record_nothing_either(self):
        with self.assertLogs("accounts.email_notifications", level="WARNING"):
            appointment = Appointment.objects.get(pk=self.book(self.sw).data["id"])
        with patch("accounts.email_notifications._queue") as queue, \
                self.assertLogs("accounts.email_notifications", level="WARNING") as logged:
            self.change(self.admin, appointment, start=self.later())
            self.cancel(self.admin, appointment)
        queue.assert_not_called()
        self.assertEqual(4, len(logged.records))     # a move and a cancel, two people each
        self.assertFalse(CalendarInvite.objects.exists())


# --------------------------------------------------------------------------
# Sending, and never failing the booking
# --------------------------------------------------------------------------

class SendingTest(SessionMailBase):
    def test_a_committed_booking_hands_brevo_the_invite_on_a_daemon_thread(self):
        with patch.object(email_notifications.threading, "Thread") as thread, \
                override_settings(BREVO_API_KEY="test-key", CORS_ALLOWED_ORIGINS=[ORIGIN]), \
                self.captureOnCommitCallbacks(execute=True):
            res = self.book(self.sw)
        self.assertEqual(201, res.status_code, res.data)
        thread.assert_called_once()
        payload, description = thread.call_args.kwargs["args"]
        self.assertEqual("my_calendar email", description)
        self.assertTrue(thread.call_args.kwargs["daemon"])
        self.assertEqual("REQUEST", prop(attachment(payload), "METHOD"))

    def test_a_rolled_back_booking_mails_nobody_and_counts_no_invite(self):
        class Rollback(Exception):
            pass
        with patch.object(email_notifications.threading, "Thread") as thread, \
                override_settings(BREVO_API_KEY="test-key"):
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        self.assertEqual(201, self.book(self.sw).status_code)
                        raise Rollback
                except Rollback:
                    pass
        thread.assert_not_called()
        self.assertFalse(CalendarInvite.objects.exists())


class NeverRaisesTest(SessionMailBase):
    def test_an_invite_that_cannot_be_built_does_not_fail_the_booking_or_the_other_email(self):
        with self.outbox() as sent, \
                patch("scheduling.session_mail.build_ics", side_effect=ValueError("no")), \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            res = self.book(self.admin)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["child_sessions"], [k for k, _ in sent])
        self.assertTrue(Appointment.objects.filter(pk=res.data["id"]).exists())
        self.assertFalse(CalendarInvite.objects.exists(),
                         "an invite that was never mailed must not be counted")

    def test_a_failure_preparing_the_emails_is_logged_and_the_booking_stands(self):
        with self.outbox() as sent, \
                patch("scheduling.session_mail.entered_name", side_effect=ValueError), \
                self.assertLogs("scheduling.session_mail", level="ERROR") as logged:
            res = self.book(self.admin)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual([], sent)
        self.assertEqual(2, len(logged.records), "one per person, so one cannot hide the other")

    def test_the_move_and_the_cancel_survive_a_failure_too(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent, \
                patch("scheduling.session_mail.entered_name", side_effect=ValueError), \
                self.assertLogs("scheduling.session_mail", level="ERROR"):
            self.assertEqual(200, self.change(self.admin, appointment,
                                             start=self.later()).status_code)
            self.assertEqual(200, self.cancel(self.admin, appointment).status_code)
        self.assertEqual([], sent)
        appointment.refresh_from_db()
        self.assertEqual(Appointment.CANCELLED, appointment.status)

    def test_a_mistyped_event_is_logged_not_raised(self):
        appointment, _ = self.booked(self.sw)
        with self.outbox() as sent, self.assertLogs("scheduling.session_mail",
                                                    level="ERROR") as logged:
            self.assertIsNone(session_changed(appointment, "teleported", by=self.admin))
        self.assertEqual([], sent)
        self.assertIn("teleported", "\n".join(logged.output))


class DirectCallTest(SessionMailBase):
    """The rules without the endpoint's own permissions in the way."""

    def test_the_psychologist_who_is_the_actor_is_not_told_they_booked_it(self):
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=self.start, duration_minutes=60)
        with self.outbox() as sent:
            session_changed(appointment, "booked", by=self.psy)
        self.assertEqual([], self.to(sent, self.psy))
        self.assertEqual(["child_sessions"], self.kinds_to(sent, self.sw))

    def test_the_social_worker_who_is_the_actor_is_not_told_about_their_own_booking(self):
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=self.start, duration_minutes=60)
        with self.outbox() as sent:
            session_changed(appointment, "booked", by=self.sw)
        self.assertEqual([], self.to(sent, self.sw))
        self.assertEqual(["my_calendar"], self.kinds_to(sent, self.psy))

    def test_a_session_handed_over_by_the_new_psychologist_is_a_booking_for_nobody_else(self):
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy, start=self.start, duration_minutes=60)
        with self.outbox() as sent:
            session_changed(appointment, "moved", by=self.psy, was_psychologist=self.psy2)
        # The one it left is told by someone else; the one who did it is not.
        self.assertEqual("CANCEL", prop(attachment(self.one(sent, self.psy2)), "METHOD"))
        self.assertEqual([], self.to(sent, self.psy))

    def test_writing_through_the_orm_mails_nobody(self):
        """The seeders and the management commands write appointments this way
        and must stay quiet."""
        with self.outbox() as sent:
            appointment = Appointment.objects.create(
                child=self.child, psychologist=self.psy, start=self.start,
                duration_minutes=60, status=Appointment.SCHEDULED)
            Appointment.objects.filter(pk=appointment.pk).update(start=self.later())
            appointment.status = Appointment.CANCELLED
            appointment.save()
        self.assertEqual([], sent)
        self.assertFalse(CalendarInvite.objects.exists())


# --------------------------------------------------------------------------
# What leaves the building
# --------------------------------------------------------------------------

class PrivacyTest(SessionMailBase):
    """Rule 1, searched for rather than assumed. Every kind of change is made
    to a child whose name, birth date, case type, custodian, notes and purpose
    nothing else in the system would produce, and everything handed to the
    sender - the page, the text, and the invite decoded out of its base64 - is
    searched for them."""

    def test_nothing_about_the_child_is_in_any_email_or_invite(self):
        # A token in every other free-text column, read off the model.
        planted = planted_text()
        self.assertGreaterEqual(len(planted), 15, "the columns to plant were not found")
        Child.objects.filter(pk=self.child.pk).update(**planted)
        self.child.refresh_from_db()
        with self.outbox() as sent:
            created = self.book(self.sw, purpose="pre_assessment")                 # booked
            appointment = Appointment.objects.get(pk=created.data["id"])
            self.change(self.sw, appointment, start=self.later(),                   # moved
                       notes=NOTES + " again")
            self.change(self.admin, appointment, psychologist=self.psy2)            # handed over
            self.change(self.psy2, appointment, start=self.later(2))                # their own move
            self.cancel(self.sw, appointment)                                      # cancelled
            own = Appointment.objects.get(pk=self.book(self.psy, start=self.later(48),
                                                       purpose="follow_up").data["id"])
            self.cancel(self.psy, own)                                             # own, no invite
            self.cancel(self.admin, Appointment.objects.get(pk=self.book(
                self.admin, start=self.later(24 * 7)).data["id"]))        # next Wednesday
        # Every path ran, or the search below proves nothing.
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        invites = [p for _, p in sent if "attachment" in p]
        self.assertGreaterEqual(len(invites), 7)
        self.assertEqual({"REQUEST", "CANCEL"},
                         {prop(attachment(p), "METHOD") for p in invites})
        self.assertTrue([p for _, p in sent if "attachment" not in p])

        sentinels = ("Zyxwanita", "Qorvath", "Brunhilda", "Vexley", "school records",
                     "Foster Care", "Neglected", "pre_assessment", "Pre-Assessment",
                     "Pre-assessment", "follow_up", "Follow-up", "again",
                     BORN.isoformat(), BORN.strftime("%d %b %Y"), BORN.strftime("%B %d, %Y"),
                     BORN.strftime("%Y%m%d"), "Z. Qorvath", *planted.values())
        for kind, payload in sent:
            # Not ASCII-escaped, and in any letter case: json.dumps writes "ñ"
            # as an escape sequence a search for the name would never find.
            everything = (json.dumps(payload, ensure_ascii=False) + (
                attachment(payload) if "attachment" in payload else "")).lower()
            for sentinel in sentinels:
                self.assertNotIn(sentinel.lower(), everything, f"{kind} carries {sentinel!r}")
            for part in ("subject", "htmlContent", "textContent"):
                self.assertTrue(payload[part].strip(), f"{kind} has no {part}")

    def test_what_an_email_does_carry_is_the_reference_the_times_and_staff_names(self):
        _, sent = self.booked(self.sw)
        payload = self.one(sent, self.psy)
        ics = attachment(payload)
        for needle in (self.ref, "Wed 21 Oct 2026", "Editha Pascua"):
            self.assertIn(needle, payload["textContent"])
        self.assertIn(self.ref, ics)
        self.assertNotIn("Editha", ics, "the invite is about the session, not who booked it")
