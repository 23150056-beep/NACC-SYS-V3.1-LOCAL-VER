"""The endpoint a scheduler calls, and the guard on it.

This is the one route in the system that takes no login, because the caller is
a machine on a timer with no session. So the tests here are about the guard —
that it is off until configured, that it refuses a wrong token, and that
calling it twice cannot text anybody twice, which matters because the free
schedulers this is designed for retry.
"""
from datetime import datetime, time, timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts import sms_notifications
from accounts.models import Role
from accounts.sms import SmsResult
from children.models import Child
from scheduling.models import Appointment, SessionReminder
from scheduling.reminders import send_session_reminders

User = get_user_model()
URL = "/api/tasks/session-reminders/"
TOKEN = "a-long-random-token-for-the-scheduler"


class ReminderTaskBase(APITestCase):
    def setUp(self):
        Role.objects.create(role_name=Role.ADMINISTRATOR)
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=psy_role, phone="+639171234567", phone_verified=True)
        self.child = Child.objects.create(
            fullname="Ana", case_type="Adoption", assigned_psychologist=self.psy)
        tomorrow = timezone.localdate() + timedelta(days=1)
        for hour in (9, 11):
            Appointment.objects.create(
                child=self.child, psychologist=self.psy,
                start=timezone.make_aware(datetime.combine(tomorrow, time(hour, 0))),
                status=Appointment.SCHEDULED)
        cache.clear()


class TheGuardTest(ReminderTaskBase):
    def test_it_does_not_exist_until_a_token_is_configured(self):
        """Unset means off. An unconfigured deployment should not carry an
        extra unauthenticated route at all."""
        self.assertEqual(self.client.post(URL).status_code, 404)

    @override_settings(SESSION_REMINDER_TOKEN=TOKEN)
    def test_no_token_is_refused(self):
        self.assertEqual(self.client.post(URL).status_code, 403)

    @override_settings(SESSION_REMINDER_TOKEN=TOKEN)
    def test_a_wrong_token_is_refused_and_sends_nothing(self):
        resp = self.client.post(URL, HTTP_X_TASK_TOKEN="not-the-token")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(SessionReminder.objects.exists())

    @override_settings(SESSION_REMINDER_TOKEN=TOKEN)
    def test_the_right_token_works_either_way_it_is_sent(self):
        for header in ({"HTTP_X_TASK_TOKEN": TOKEN},
                       {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}):
            with self.subTest(header=list(header)[0]):
                SessionReminder.objects.all().delete()
                resp = self.client.post(URL, **header)
                self.assertEqual(resp.status_code, 200, resp.data)
                self.assertEqual(resp.data["sent"], 1)


@override_settings(SESSION_REMINDER_TOKEN=TOKEN)
class WhatItDoesTest(ReminderTaskBase):
    def test_one_message_covers_the_whole_day(self):
        """Two appointments, one text. Five texts about five sessions is how
        a sender gets muted."""
        resp = self.client.post(URL, HTTP_X_TASK_TOKEN=TOKEN)
        self.assertEqual(resp.data["sent"], 1)

    def test_calling_it_twice_does_not_text_twice(self):
        """The free schedulers this is built for retry, and a retry must not
        cost a second message."""
        first = self.client.post(URL, HTTP_X_TASK_TOKEN=TOKEN)
        second = self.client.post(URL, HTTP_X_TASK_TOKEN=TOKEN)
        self.assertEqual(first.data["sent"], 1)
        self.assertEqual(second.data["sent"], 0)
        self.assertEqual(second.data["skipped"], 1)

    def test_dry_run_sends_nothing_and_leaves_no_record(self):
        dry = self.client.post(URL + "?dry_run=1", HTTP_X_TASK_TOKEN=TOKEN)
        self.assertTrue(dry.data["dry_run"])
        self.assertEqual(dry.data["sent"], 0)
        # A dry run must not consume the day's slot, or the real run that
        # follows would find itself already recorded and send nothing.
        real = self.client.post(URL, HTTP_X_TASK_TOKEN=TOKEN)
        self.assertEqual(real.data["sent"], 1)

    def test_an_unverified_number_is_skipped(self):
        self.psy.phone_verified = False
        self.psy.save()
        resp = self.client.post(URL, HTTP_X_TASK_TOKEN=TOKEN)
        self.assertEqual(resp.data["sent"], 0)
        self.assertEqual(resp.data["skipped"], 1)

    def test_the_reply_counts_people_and_never_names_them(self):
        """This response lands in a CI log. A caseload has no business there."""
        resp = self.client.post(URL, HTTP_X_TASK_TOKEN=TOKEN)
        blob = str(resp.data)
        self.assertNotIn(self.child.fullname, blob)
        self.assertNotIn(self.psy.email, blob)
        self.assertNotIn(self.psy.phone, blob)


class TheRecordOfWhoWasToldTest(ReminderTaskBase):
    """`manage.py send_session_reminders` is a fresh process every run, and
    the default cache lives in one process's memory. A record kept there was
    empty on every run, so "safe to run twice" was not true for the one
    command the local copy actually uses."""

    def test_it_outlives_the_cache(self):
        first = send_session_reminders()
        cache.clear()           # what a new process starts with
        second = send_session_reminders()
        self.assertEqual(first["sent"], 1)
        self.assertEqual(second["sent"], 0)
        self.assertEqual(second["skipped"], 1)

    def test_a_refused_send_is_not_recorded_and_is_retried(self):
        refused = SmsResult(False, "The gateway did not queue the message")
        with patch.object(sms_notifications, "send_sms", return_value=refused):
            first = send_session_reminders()
        self.assertEqual((first["sent"], first["failed"]), (0, 1))
        self.assertFalse(SessionReminder.objects.exists())
        self.assertIn("did not queue", " ".join(first["lines"]))

        second = send_session_reminders()
        self.assertEqual(second["sent"], 1)
        self.assertEqual(SessionReminder.objects.get().session_count, 2)

    def test_a_sent_reminder_is_stamped(self):
        send_session_reminders()
        self.assertIsNotNone(SessionReminder.objects.get().sent_at)

    def test_a_claim_whose_run_died_is_taken_over(self):
        """Claimed, never stamped sent: the process died mid-send. Reading
        that row as "already told" lost the reminder for good."""
        SessionReminder.objects.create(
            psychologist=self.psy, day=timezone.localdate() + timedelta(days=1),
            session_count=2)
        SessionReminder.objects.update(
            claimed_at=timezone.now() - SessionReminder.CLAIM_LEASE
            - timedelta(minutes=1))
        report = send_session_reminders()
        self.assertEqual(report["sent"], 1)
        self.assertIsNotNone(SessionReminder.objects.get().sent_at)

    def test_a_claim_still_inside_its_lease_is_left_alone(self):
        """Another run may be sending it right now."""
        SessionReminder.objects.create(
            psychologist=self.psy, day=timezone.localdate() + timedelta(days=1),
            session_count=2)
        with patch.object(sms_notifications, "send_sms") as sender:
            report = send_session_reminders()
            sender.assert_not_called()
        self.assertEqual(report["skipped"], 1)
        self.assertIn("another run", " ".join(report["lines"]))

    def test_the_command_has_sent_before_it_exits(self):
        """It used to queue on a daemon thread, which dies with the process -
        the command printed "queued" and exited before anything was sent."""
        out = StringIO()
        with patch.object(sms_notifications, "send_sms",
                          return_value=SmsResult(True, "ok")) as sender:
            call_command("send_session_reminders", stdout=out)
            sender.assert_called_once()
        self.assertIn("1 reminder(s) sent", out.getvalue())

    def test_the_command_fails_when_a_reminder_was_refused(self):
        """A non-zero exit, so whatever schedules it sees the failure."""
        refused = SmsResult(False, "refused")
        with patch.object(sms_notifications, "send_sms", return_value=refused):
            with self.assertRaises(CommandError) as caught:
                call_command("send_session_reminders", stdout=StringIO())
        self.assertIn("1 failed", str(caught.exception))

    @override_settings(SESSION_REMINDER_TOKEN=TOKEN)
    def test_the_endpoint_counts_failures_without_naming_anyone(self):
        refused = SmsResult(False, "refused")
        with patch.object(sms_notifications, "send_sms", return_value=refused):
            resp = self.client.post(URL, HTTP_X_TASK_TOKEN=TOKEN)
        self.assertEqual(resp.data["failed"], 1)
        self.assertNotIn(self.psy.email, str(resp.data))
