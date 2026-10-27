"""The custodian's appointment texts and their day-before reminder, with the
credit-safety rules of 27 Oct 2026: a move to the start the custodian was last
told sends nothing, an appointment gets at most four event texts a day, the
reminder is skipped for someone just told, follows a move to another day, and
one text covers siblings. A lost gateway answer is kept and never resent.

The clock is pinned (CLAUDE.md "A test never reads the wall clock").
"""
from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from accounts import sms_notifications
from accounts.sms import UNKNOWN, SmsResult, send_sms
from children.models import Child
from children.tests.test_custodian import NUMBER, CustodianBase
from scheduling.models import Appointment, CustodianReminder
from scheduling.reminders import send_session_reminders
from scheduling.tests.test_api import give_referral
from scheduling.tests.test_reminder_task import ReminderTaskBase

NOON = timezone.make_aware(datetime(2026, 10, 7, 12, 0))


def at(day, hour, minute=0):
    return timezone.make_aware(datetime(2026, 10, day, hour, minute))


class Sent:
    """A stand-in for send_sms that records (number, text, kind)."""

    def __init__(self, outcome=None):
        self.calls = []
        self.outcome = outcome

    def __call__(self, number, text, kind, **kwargs):
        self.calls.append((number, text, kind))
        if self.outcome == UNKNOWN:
            return SmsResult(False, "The connection dropped", outcome=UNKNOWN)
        if self.outcome == "refused":
            return SmsResult(False, "refused")
        return SmsResult(True, "ok")


class EventTextsTest(CustodianBase):
    def setUp(self):
        super().setUp()
        give_referral(self.child, self.sw)
        self.start = at(10, 10)
        self.patcher = patch("django.utils.timezone.now", return_value=NOON)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _queue(self):
        return patch.object(sms_notifications, "queue_sms", return_value=True)

    def _book(self, start=None):
        with self._queue() as queue:
            with self.captureOnCommitCallbacks(execute=True):
                res = self._as(self.psy).post("/api/appointments/", {
                    "child": self.child.id, "psychologist": self.psy.id,
                    "start": (start or self.start).isoformat(),
                    "duration_minutes": 60, "purpose": "session"}, format="json")
        self.assertEqual(201, res.status_code, res.data)
        return res, queue

    def _move(self, appt, start):
        with self._queue() as queue:
            res = self._as(self.psy).patch(
                f"/api/appointments/{appt.id}/", {"start": start.isoformat()},
                format="json")
        self.assertEqual(200, res.status_code, res.data)
        return res, queue

    def test_booking_records_what_the_custodian_was_told(self):
        self._texts_on()
        res, queue = self._book()
        self.assertEqual("queued", res.data["custodian_text"])
        queue.assert_called_once()
        appt = Appointment.objects.get()
        self.assertEqual(self.start, appt.custodian_told_start)
        self.assertEqual(NOON, appt.custodian_told_at)

    def test_recording_it_is_not_an_edit_of_the_booking(self):
        self._texts_on()
        appt = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                          start=self.start, purpose="session")
        before = Appointment.objects.get(pk=appt.pk).updated_at
        with self._queue():
            self.assertEqual("queued", sms_notifications.notify_custodian(appt, "booked"))
        self.assertEqual(before, Appointment.objects.get(pk=appt.pk).updated_at)

    def test_a_move_to_the_start_already_told_sends_nothing(self):
        self._texts_on()
        self._book()
        appt = Appointment.objects.get()
        later = self.start + timedelta(days=1)
        # What the custodian was last told is the new start (the screen's
        # earlier text was queued for it), so this move changes nothing.
        Appointment.objects.filter(pk=appt.pk).update(custodian_told_start=later)
        res, queue = self._move(appt, later)
        self.assertEqual("unchanged", res.data["custodian_text"])
        queue.assert_not_called()

    def test_a_move_to_a_different_start_is_texted_and_recorded(self):
        self._texts_on()
        self._book()
        appt = Appointment.objects.get()
        later = self.start + timedelta(days=1)
        res, queue = self._move(appt, later)
        self.assertEqual("queued", res.data["custodian_text"])
        queue.assert_called_once()
        self.assertEqual(later, Appointment.objects.get().custodian_told_start)
        # ...and moving it back is a change from what they were last told.
        back, queue = self._move(appt, self.start)
        self.assertEqual("queued", back.data["custodian_text"])

    def test_an_appointment_with_no_told_start_counts_as_different(self):
        self._texts_on()
        appt = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                          start=self.start, purpose="session")
        res, queue = self._move(appt, self.start + timedelta(hours=3))
        self.assertEqual("queued", res.data["custodian_text"])

    def test_the_fifth_event_text_in_a_day_is_held_and_the_screen_is_told(self):
        self._texts_on()
        appt = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                          start=self.start, purpose="session")
        for kind in ("custodian_booked", "custodian_moved", "custodian_moved",
                     "custodian_moved"):
            send_sms(NUMBER, "NACC RACCO I: an event", kind,
                     subject=f"appointment:{appt.pk}")
        with self._queue() as queue:
            res = self._as(self.psy).post(f"/api/appointments/{appt.id}/cancel/")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("limit", res.data["custodian_text"])
        self.assertIn("allowed", res.data["custodian_text_detail"])
        queue.assert_not_called()

    def test_the_numbers_daily_limit_carries_its_own_sentence(self):
        self._texts_on()
        appt = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                          start=self.start, purpose="session")
        for n in range(settings.SMS_PER_NUMBER_DAILY):
            send_sms(NUMBER, "NACC RACCO I: an event", "custodian_booked",
                     subject=f"appointment:{1000 + n}")
        with self._queue() as queue:
            res = self._as(self.psy).post(f"/api/appointments/{appt.id}/cancel/")
        self.assertEqual("limit", res.data["custodian_text"])
        self.assertIn("This number has already been sent", res.data["custodian_text_detail"])
        queue.assert_not_called()

    def test_the_fourth_is_still_sent(self):
        self._texts_on()
        appt = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                          start=self.start, purpose="session")
        for _ in range(3):
            send_sms(NUMBER, "NACC RACCO I: an event", "custodian_moved",
                     subject=f"appointment:{appt.pk}")
        with self._queue() as queue:
            res = self._as(self.psy).post(f"/api/appointments/{appt.id}/cancel/")
        self.assertEqual("queued", res.data["custodian_text"])
        queue.assert_called_once()

    def test_a_limit_on_one_appointment_does_not_hold_another(self):
        self._texts_on()
        for _ in range(4):
            send_sms(NUMBER, "NACC RACCO I: an event", "custodian_moved",
                     subject="appointment:99999")
        res, queue = self._book()
        self.assertEqual("queued", res.data["custodian_text"])

    def test_with_texts_off_the_answer_says_so(self):
        self._patch(custodian_contact="0917 123 4567")
        res, queue = self._book()
        self.assertEqual("off", res.data["custodian_text"])
        queue.assert_not_called()

    def test_the_answer_to_a_read_carries_no_such_field(self):
        self._texts_on()
        self._book()
        res = self._as(self.psy).get("/api/appointments/")
        self.assertNotIn("custodian_text", res.data[0])


class TheReminderTest(CustodianBase):
    """The day-before reminder. `now` is noon on 7 Oct; the reminder is for
    8 Oct."""

    def setUp(self):
        super().setUp()
        give_referral(self.child, self.sw)
        self.patcher = patch("django.utils.timezone.now", return_value=NOON)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self._texts_on()

    def _appointment(self, hour=9, minute=0, child=None, day=8):
        return Appointment.objects.create(
            child=child or self.child, psychologist=self.psy,
            start=at(day, hour, minute), purpose="session")

    def _run(self, sender):
        with patch.object(sms_notifications, "send_sms", side_effect=sender):
            return send_session_reminders()

    def _sibling(self, name, consent=True):
        child = Child.objects.create(
            first_name=name, last_name="Cruz", fullname=f"{name} Cruz",
            case_type="Foster Care", custodian_name="Rosa", social_worker=self.sw,
            assigned_psychologist=self.psy, custodian_contact=NUMBER,
            custodian_sms_consent=consent, custodian_contact_verified_at=NOON)
        return child

    def test_one_appointment_gets_the_single_wording(self):
        self._appointment()
        sender = Sent()
        self._run(sender)
        self.assertEqual(["custodian_reminder"], [k for n, t, k in sender.calls])

    def test_someone_told_when_booked_is_skipped(self):
        appt = self._appointment()
        Appointment.objects.filter(pk=appt.pk).update(
            custodian_told_at=NOON - timedelta(hours=2))
        sender = Sent()
        report = self._run(sender)
        self.assertEqual([], sender.calls)
        self.assertFalse(CustodianReminder.objects.exists())
        self.assertTrue(any("told when booked" in line for line in report["lines"]))

    def test_someone_told_long_ago_is_reminded(self):
        appt = self._appointment()
        Appointment.objects.filter(pk=appt.pk).update(
            custodian_told_at=NOON - timedelta(hours=13))
        sender = Sent()
        self._run(sender)
        self.assertEqual(1, len(sender.calls))

    def test_an_appointment_moved_to_another_day_is_reminded_for_the_new_day(self):
        appt = self._appointment(day=8)
        sender = Sent()
        self._run(sender)
        self.assertEqual(1, len(sender.calls))
        self.assertEqual(1, CustodianReminder.objects.filter(sent_at__isnull=False).count())

        with patch.object(sms_notifications, "queue_sms", return_value=True):
            res = self._as(self.psy).patch(
                f"/api/appointments/{appt.id}/", {"start": at(9, 9).isoformat()},
                format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertFalse(CustodianReminder.objects.exists())

        # The next evening: tomorrow is now the 9th.
        with patch("django.utils.timezone.now", return_value=NOON + timedelta(days=1)):
            self._run(sender)
        self.assertEqual(2, len(sender.calls))
        self.assertIn("9:00 AM", sender.calls[1][1])

    def test_a_move_within_the_same_day_keeps_its_reminder_row(self):
        appt = self._appointment(day=8)
        self._run(Sent())
        with patch.object(sms_notifications, "queue_sms", return_value=True):
            self._as(self.psy).patch(
                f"/api/appointments/{appt.id}/", {"start": at(8, 14).isoformat()},
                format="json")
        self.assertEqual(1, CustodianReminder.objects.count())

    def test_two_siblings_get_one_text_and_both_rows_are_stamped(self):
        self._appointment(10, 30)
        self._appointment(9, 0, child=self._sibling("Ben"))
        sender = Sent()
        report = self._run(sender)
        self.assertEqual(1, len(sender.calls))
        number, text, kind = sender.calls[0]
        self.assertEqual("custodian_reminder_group", kind)
        self.assertIn("9:00 AM and 10:30 AM", text)
        self.assertIn("appointments tomorrow", text)
        self.assertEqual(2, CustodianReminder.objects.filter(sent_at__isnull=False).count())
        self.assertEqual(1, report["sent"])
        # Re-running sends nothing.
        self._run(sender)
        self.assertEqual(1, len(sender.calls))

    def test_the_group_send_is_about_the_first_appointment(self):
        first = self._appointment(10, 30)
        earlier = self._appointment(9, 0, child=self._sibling("Ben"))
        with patch.object(sms_notifications, "send_sms", return_value=SmsResult(True, "ok")) as s:
            send_session_reminders()
        self.assertEqual(f"appointment:{earlier.pk}", s.call_args.kwargs["subject"])

    def test_three_siblings_are_listed_with_and(self):
        self._appointment(9, 0)
        self._appointment(10, 30, child=self._sibling("Ben"))
        self._appointment(13, 0, child=self._sibling("Cia"))
        sender = Sent()
        self._run(sender)
        self.assertEqual(1, len(sender.calls))
        self.assertIn("9:00 AM, 10:30 AM and 1:00 PM", sender.calls[0][1])

    def test_five_siblings_get_the_count_and_the_first_time(self):
        self._appointment(9, 0)
        for i, name in enumerate(("Ben", "Cia", "Dan", "Eli")):
            self._appointment(10 + i, 0, child=self._sibling(name))
        sender = Sent()
        self._run(sender)
        self.assertEqual(1, len(sender.calls))
        number, text, kind = sender.calls[0]
        self.assertEqual("custodian_reminder_group_many", kind)
        self.assertIn("5 appointments", text)
        self.assertIn("the first at 9:00 AM", text)
        self.assertEqual(5, CustodianReminder.objects.filter(sent_at__isnull=False).count())

    def test_a_sibling_with_texts_off_is_not_in_the_group(self):
        self._appointment(9, 0)
        self._appointment(10, 30, child=self._sibling("Ben"))
        off = self._sibling("Cia", consent=False)
        self._appointment(13, 0, child=off)
        sender = Sent()
        self._run(sender)
        self.assertEqual(1, len(sender.calls))
        self.assertIn("9:00 AM and 10:30 AM", sender.calls[0][1])
        self.assertNotIn("1:00 PM", sender.calls[0][1])
        self.assertFalse(CustodianReminder.objects.filter(appointment__child=off).exists())

    def test_different_numbers_are_different_texts(self):
        self._appointment(9, 0)
        other = self._sibling("Ben")
        Child.objects.filter(pk=other.pk).update(custodian_contact="+639181112222")
        self._appointment(10, 0, child=other)
        sender = Sent()
        self._run(sender)
        self.assertEqual(2, len(sender.calls))

    def test_a_refused_group_deletes_every_claim_and_is_retried(self):
        self._appointment(9, 0)
        self._appointment(10, 30, child=self._sibling("Ben"))
        report = self._run(Sent("refused"))
        self.assertEqual(1, report["failed"])
        self.assertFalse(CustodianReminder.objects.exists())
        retry = Sent()
        self._run(retry)
        self.assertEqual(1, len(retry.calls))

    def test_an_unknown_answer_is_stamped_reported_and_never_resent(self):
        self._appointment(9, 0)
        self._appointment(10, 30, child=self._sibling("Ben"))
        first = Sent(UNKNOWN)
        report = self._run(first)
        self.assertEqual((0, 0, 1), (report["sent"], report["failed"], report["unconfirmed"]))
        self.assertTrue(any(line.startswith("UNCONFIRMED ") for line in report["lines"]))
        self.assertEqual(2, CustodianReminder.objects.filter(sent_at__isnull=False).count())
        again = Sent()
        self._run(again)
        self.assertEqual([], again.calls)

    def test_the_report_names_case_references_not_children(self):
        self._appointment(9, 0)
        self._appointment(10, 30, child=self._sibling("Ben"))
        report = self._run(Sent())
        blob = " ".join(report["lines"])
        self.assertNotIn("Ben", blob)
        self.assertNotIn("Ana", blob)
        self.assertEqual(1, len([l for l in report["lines"] if l.startswith("sent")]))


class PsychologistReminderOutcomeTest(ReminderTaskBase):
    def test_an_unknown_answer_is_stamped_and_not_resent(self):
        sender = Sent(UNKNOWN)
        with patch.object(sms_notifications, "send_sms", side_effect=sender):
            first = send_session_reminders()
            second = send_session_reminders()
        self.assertEqual((0, 0, 1), (first["sent"], first["failed"], first["unconfirmed"]))
        self.assertTrue(any(l.startswith("UNCONFIRMED ") for l in first["lines"]))
        self.assertEqual(1, len(sender.calls))
        self.assertEqual(1, second["skipped"])

    def test_the_command_does_not_fail_on_an_unknown_answer(self):
        out = StringIO()
        with patch.object(sms_notifications, "send_sms", side_effect=Sent(UNKNOWN)):
            call_command("send_session_reminders", stdout=out)
        self.assertIn("1 unconfirmed", out.getvalue())

    def test_the_endpoint_counts_unconfirmed(self):
        from django.test import override_settings
        with override_settings(SESSION_REMINDER_TOKEN="a-long-random-token-for-the-scheduler"):
            with patch.object(sms_notifications, "send_sms", side_effect=Sent(UNKNOWN)):
                res = self.client.post("/api/tasks/session-reminders/",
                                       HTTP_X_TASK_TOKEN="a-long-random-token-for-the-scheduler")
        self.assertEqual(1, res.data["unconfirmed"])
        self.assertEqual(0, res.data["failed"])
