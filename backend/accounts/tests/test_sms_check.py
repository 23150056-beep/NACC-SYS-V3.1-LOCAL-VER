"""Checking the gateway without spending a message.

PhilSMS gives five free credits on signup and there is no sandbox. Five is
exactly enough for one pass of each message this system sends, which means
they are the wrong thing to spend on finding out whether the API key was
pasted correctly.

Every gateway worth using answers "who am I and what is my balance" for free,
so that is what the settings page asks first. Sending is still there; it is
just no longer the only way to learn that the key is wrong.

The gateway's own documentation also warns that repeatedly sending nearly
identical text to the same number gets classified as spam by the telcos - and
the test button sent a fixed sentence every time. That is tested here too.
"""
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.sms import check_gateway
from accounts.tests.test_sms_philsms import FakeResponse

User = get_user_model()
URL = "/api/sms-test/"


@override_settings(SMS_PROVIDER="philsms", SMS_API_KEY="test-token",
                   SMS_ENDPOINT="")
class TheCheckCostsNothingTest(SimpleTestCase):
    def _check(self, body):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            result = check_gateway()
            request = opened.call_args[0][0]
        return result, request

    def test_it_asks_for_the_balance_and_sends_no_message(self):
        _, request = self._check(json.dumps(
            {"status": "success", "data": {"remaining_unit": 5}}))
        self.assertTrue(request.full_url.endswith("/balance"))
        self.assertNotIn("/sms/send", request.full_url)
        self.assertEqual("GET", request.get_method())

    def test_it_authenticates_the_same_way_the_sender_does(self):
        # If the check passes with a key the sender would be refused with, it
        # is worse than no check at all.
        _, request = self._check(json.dumps({"status": "success", "data": {}}))
        self.assertEqual("Bearer test-token", request.get_header("Authorization"))

    def test_it_reports_what_is_left(self):
        result, _ = self._check(json.dumps(
            {"status": "success", "data": {"remaining_unit": 5, "used_unit": 0}}))
        self.assertTrue(result.ok)
        self.assertIn("5", result.detail)

    def test_a_refused_key_is_a_failure_with_the_gateways_words(self):
        result, _ = self._check(json.dumps(
            {"status": "error", "message": "Unauthenticated."}))
        self.assertFalse(result.ok)
        self.assertIn("Unauthenticated", result.detail)

    def test_no_key_is_refused_before_the_network(self):
        with override_settings(SMS_API_KEY=""):
            with patch("accounts.sms.urllib.request.urlopen") as opened:
                result = check_gateway()
                opened.assert_not_called()
        self.assertFalse(result.ok)


class TheConsoleHasNothingToCheckTest(SimpleTestCase):
    @override_settings(SMS_PROVIDER="console")
    def test_it_says_so_rather_than_pretending_to_pass(self):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            result = check_gateway()
            opened.assert_not_called()
        self.assertFalse(result.ok)
        self.assertIn("no gateway", result.detail.lower())


class SmsCheckEndpointTest(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="pass1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR))
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=Role.objects.create(role_name=Role.STAFF))

    def test_only_an_administrator_may_check(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(403, self.client.get(URL).status_code)

    @override_settings(SMS_PROVIDER="console")
    def test_it_needs_no_mobile_number_on_the_account(self):
        # Unlike sending, which texts the caller. Checking the key should not
        # require verifying a handset first - that ordering is what makes a
        # misconfigured gateway hard to diagnose.
        self.assertIsNone(self.admin.phone or None)
        self.client.force_authenticate(self.admin)
        resp = self.client.get(URL)
        self.assertEqual(200, resp.status_code)
        for key in ("ok", "detail", "provider"):
            self.assertIn(key, resp.data)


@override_settings(SMS_PROVIDER="console")
class TheTestMessageIsNotIdenticalEveryTimeTest(APITestCase):
    """Their documentation warns that near-identical repeats to one number are
    classified as spam. The test button is the one message somebody sends over
    and over."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="pass1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR),
            phone="+639998887777", phone_verified=True)
        self.client.force_authenticate(self.admin)

    def test_two_tests_do_not_send_the_same_text(self):
        with patch("accounts.views.send_sms") as sender:
            sender.return_value.ok = True
            sender.return_value.detail = "ok"
            self.client.post(URL)
            self.client.post(URL)
            first, second = [c[0][1] for c in sender.call_args_list]
        self.assertNotEqual(first, second)

    def test_it_still_fits_one_segment(self):
        from accounts.sms import SINGLE_SEGMENT
        with patch("accounts.views.send_sms") as sender:
            sender.return_value.ok = True
            sender.return_value.detail = "ok"
            self.client.post(URL)
            text = sender.call_args[0][1]
        self.assertLessEqual(len(text), SINGLE_SEGMENT)
