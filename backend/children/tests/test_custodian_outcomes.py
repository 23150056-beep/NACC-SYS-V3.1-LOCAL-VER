"""The custodian's number code: what an unknown answer keeps, a refused one
gives back, and a number already confirmed is not sent another code
(27 Oct 2026). The rules for the user's own number are in
accounts/tests/test_sms_callers.py."""
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from accounts.sms import UNKNOWN, SmsResult
from children import custodian
from children.models import CustodianContactCheck
from children.tests.test_custodian import NUMBER, CustodianBase, fake_send

URL = "/api/custodian-contact/code/"


def unknown(*args, **kwargs):
    return SmsResult(False, "The connection dropped", outcome=UNKNOWN)


class CodeOutcomeTest(CustodianBase):
    def test_a_refused_code_gives_the_limits_back(self):
        with patch("accounts.sms.send_sms", return_value=SmsResult(False, "no")):
            res = self._as(self.sw).post(URL, {"number": NUMBER}, format="json")
        self.assertEqual(502, res.status_code)
        row = CustodianContactCheck.objects.get(requested_by=self.sw)
        self.assertEqual("", row.code)
        self.assertEqual(0, row.sent_in_window)
        self.assertIsNone(row.last_sent_at)

    def test_an_unknown_answer_keeps_the_code_and_says_it_may_have_gone(self):
        with patch("accounts.sms.send_sms", side_effect=unknown):
            res = self._as(self.sw).post(URL, {"number": NUMBER}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertTrue(res.data["unconfirmed"])
        row = CustodianContactCheck.objects.get(requested_by=self.sw)
        self.assertEqual(6, len(row.code))
        self.assertEqual(1, row.sent_in_window)
        self.assertIsNotNone(row.last_sent_at)
        with patch("accounts.sms.send_sms") as sender:
            again = self._as(self.sw).post(URL, {"number": NUMBER}, format="json")
            sender.assert_not_called()
        self.assertEqual(429, again.status_code)

    def test_the_kept_code_can_still_be_confirmed(self):
        with patch("accounts.sms.send_sms", side_effect=unknown):
            self._as(self.sw).post(URL, {"number": NUMBER}, format="json")
        code = CustodianContactCheck.objects.get(requested_by=self.sw).code
        res = self._as(self.sw).put(URL, {"number": NUMBER, "code": code}, format="json")
        self.assertEqual(200, res.status_code, res.data)


class AlreadyConfirmedTest(CustodianBase):
    def test_a_number_confirmed_within_the_hour_sends_nothing(self):
        self._confirm()
        with patch("accounts.sms.send_sms") as sender:
            res = self._as(self.sw).post(URL, {"number": NUMBER}, format="json")
            sender.assert_not_called()
        self.assertEqual(200, res.status_code, res.data)
        self.assertTrue(res.data["verified"])
        self.assertEqual(NUMBER, res.data["number"])

    def test_a_stale_confirmation_gets_a_new_code(self):
        self._confirm()
        CustodianContactCheck.objects.update(
            verified_at=timezone.now() - custodian.CONFIRMED_FOR - timedelta(minutes=1),
            last_sent_at=None)
        sent = []
        with patch("accounts.sms.send_sms", side_effect=fake_send(sent)):
            res = self._as(self.sw).post(URL, {"number": NUMBER}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(1, len(sent))

    def test_another_persons_confirmation_does_not_count(self):
        self._confirm()
        sent = []
        with patch("accounts.sms.send_sms", side_effect=fake_send(sent)):
            self._as(self.sw2).post(URL, {"number": NUMBER}, format="json")
        self.assertEqual(1, len(sent))
