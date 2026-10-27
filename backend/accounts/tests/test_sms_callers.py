"""What the callers of the sender do with its answer, and the case where
nothing should be sent at all (27 Oct 2026, phase 3 of the credit-safety plan).

"Refused" means nothing went out, so the code and the limits are given back.
"Unknown" means the answer was lost and the code may be on the handset: it is
kept, the counters stay, and the retry is held by the one-minute limit instead
of being a free second paid text. A number that is already verified is not sent
a code at all.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts import sms_notifications
from accounts.models import PhoneVerification, Role, SmsSend
from accounts.sms import UNKNOWN, SmsResult, limit_reason, send_sms
from accounts.sms_notifications import TooManyCodes, start_phone_verification

User = get_user_model()
URL = "/api/auth/me/phone/"
NUMBER = "+639171234567"


def unknown():
    return SmsResult(False, "The connection dropped", outcome=UNKNOWN)


class PhoneCodeOutcomeTest(TestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email="u@t.ph", username="u", password="x", role=role)

    def test_a_refused_code_gives_the_limits_back(self):
        with patch.object(sms_notifications, "send_sms",
                          return_value=SmsResult(False, "no")):
            start_phone_verification(self.user, NUMBER)
        row = PhoneVerification.objects.get(user=self.user)
        self.assertEqual("", row.code)
        self.assertEqual(0, row.sent_in_window)
        self.assertIsNone(row.last_sent_at)

    def test_an_unknown_answer_keeps_the_code_and_the_counters(self):
        with patch.object(sms_notifications, "send_sms", return_value=unknown()):
            result = start_phone_verification(self.user, NUMBER)
        self.assertEqual(UNKNOWN, result.outcome)
        row = PhoneVerification.objects.get(user=self.user)
        self.assertEqual(6, len(row.code))
        self.assertEqual(1, row.sent_in_window)
        self.assertIsNotNone(row.last_sent_at)
        # A retry is held by the one-minute limit: no free second paid text.
        with patch.object(sms_notifications, "send_sms") as sender:
            with self.assertRaises(TooManyCodes):
                start_phone_verification(self.user, NUMBER)
            sender.assert_not_called()

    def test_the_screen_is_told_it_may_have_been_sent(self):
        client = APIClient()
        client.force_authenticate(self.user)
        with patch.object(sms_notifications, "send_sms", return_value=unknown()):
            res = client.post(URL, {"phone": "0917 123 4567"}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertTrue(res.data["unconfirmed"])
        self.assertIn("may still arrive", res.data["detail"])
        # The code that may be on the handset is still the one to type.
        self.assertTrue(PhoneVerification.objects.get(user=self.user).code)


class AlreadyVerifiedTest(TestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email="u@t.ph", username="u", password="x", role=role,
            phone=NUMBER, phone_verified=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_the_same_verified_number_sends_nothing(self):
        with patch.object(sms_notifications, "send_sms") as sender:
            res = self.client.post(URL, {"phone": "0917 123 4567"}, format="json")
            sender.assert_not_called()
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("This number is already verified.", res.data["detail"])
        self.assertEqual(0, SmsSend.objects.count())
        self.assertFalse(PhoneVerification.objects.filter(user=self.user).exists())

    def test_a_different_number_still_gets_a_code(self):
        with patch.object(sms_notifications, "send_sms",
                          return_value=SmsResult(True, "ok")) as sender:
            res = self.client.post(URL, {"phone": "0918 765 4321"}, format="json")
            sender.assert_called_once()
        self.assertEqual(200, res.status_code, res.data)
        self.assertNotIn("already_verified", res.data)

    def test_the_same_number_unverified_still_gets_a_code(self):
        self.user.phone_verified = False
        self.user.save()
        with patch.object(sms_notifications, "send_sms",
                          return_value=SmsResult(True, "ok")) as sender:
            self.client.post(URL, {"phone": "0917 123 4567"}, format="json")
            sender.assert_called_once()


class LimitReasonTest(TestCase):
    """limit_reason is the question send_sms asks, asked ahead of time."""

    def test_it_agrees_with_the_sender(self):
        for _ in range(4):
            send_sms(NUMBER, "x event", "custodian_booked", subject="appointment:7")
        self.assertTrue(limit_reason(NUMBER, "custodian_moved",
                                     subject="appointment:7"))
        self.assertEqual("", limit_reason(NUMBER, "custodian_moved",
                                          subject="appointment:8"))
        refused = send_sms(NUMBER, "x event", "custodian_moved",
                           subject="appointment:7")
        self.assertEqual("limited", refused.outcome)

    def test_an_unreadable_number_or_unknown_kind_is_not_a_limit(self):
        self.assertEqual("", limit_reason("12345", "custodian_booked"))
        self.assertEqual("", limit_reason(NUMBER, "not_a_kind"))
