"""The endpoint a scheduler calls, and the guard on it.

This is the one route in the system that takes no login, because the caller is
a machine on a timer with no session. So the tests here are about the guard —
that it is off until configured, that it refuses a wrong token, and that
calling it twice cannot text anybody twice, which matters because the free
schedulers this is designed for retry.
"""
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from scheduling.models import Appointment

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
        self.assertIsNone(cache.get(f"session-reminder:{self.psy.pk}:"
                                    f"{(timezone.localdate() + timedelta(days=1))}"))

    @override_settings(SESSION_REMINDER_TOKEN=TOKEN)
    def test_the_right_token_works_either_way_it_is_sent(self):
        for header in ({"HTTP_X_TASK_TOKEN": TOKEN},
                       {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}):
            with self.subTest(header=list(header)[0]):
                cache.clear()
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
