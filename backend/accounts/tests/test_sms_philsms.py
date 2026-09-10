"""A second gateway, because the first one is not a given.

Semaphore was chosen first and could not be opened. That is the situation
accounts/sms.py was shaped for - "whoever is chosen first is unlikely to be
chosen forever" - so this adds PhilSMS beside it rather than in place of it,
and SMS_PROVIDER still picks.

The two APIs disagree in every way an integration can quietly get wrong:
Semaphore is form-encoded with the key in the body, PhilSMS is JSON with a
bearer token; Semaphore returns a list, PhilSMS an object; and PhilSMS wants
the number with no leading plus. Each of those is a test here, because each of
them fails as a 200 with nothing sent rather than as an exception.
"""
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from accounts.sms import PROVIDERS, send_sms


class FakeResponse:
    def __init__(self, body):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


OK_BODY = json.dumps({"status": "success", "data": {"uid": "abc123"}})


@override_settings(SMS_PROVIDER="philsms", SMS_API_KEY="test-token",
                   SMS_ENDPOINT="https://app.philsms.com/api/v3/sms/send",
                   SMS_SENDER_NAME="NACC")
class PhilSmsRequestShapeTest(SimpleTestCase):
    def _send(self, body=OK_BODY):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            result = send_sms("09171234567", "NACC SYS: probe", "probe")
            request = opened.call_args[0][0] if opened.call_args else None
        return result, request

    def test_it_is_registered_as_a_provider(self):
        self.assertIn("philsms", PROVIDERS)

    def test_it_posts_json_to_the_configured_endpoint(self):
        _, request = self._send()
        self.assertEqual("POST", request.get_method())
        self.assertEqual("https://app.philsms.com/api/v3/sms/send", request.full_url)
        self.assertEqual("application/json", request.get_header("Content-type"))

    def test_the_key_travels_as_a_bearer_token_not_in_the_body(self):
        # Semaphore puts the key in the form body. Sending it that way here
        # authenticates nothing and the gateway answers 401.
        _, request = self._send()
        self.assertEqual("Bearer test-token", request.get_header("Authorization"))
        self.assertNotIn("test-token", request.data.decode())

    def test_the_number_is_sent_without_a_leading_plus(self):
        # Stored as +639171234567; PhilSMS documents 639171234567. This is the
        # kind of difference that comes back as "accepted" and never arrives.
        _, request = self._send()
        self.assertEqual("639171234567", json.loads(request.data)["recipient"])

    def test_the_sender_name_and_message_are_carried(self):
        _, request = self._send()
        body = json.loads(request.data)
        self.assertEqual("NACC", body["sender_id"])
        self.assertEqual("NACC SYS: probe", body["message"])
        self.assertEqual("plain", body["type"])

    def test_a_success_is_reported_as_sent(self):
        result, _ = self._send()
        self.assertTrue(result.ok)


@override_settings(SMS_PROVIDER="philsms", SMS_API_KEY="test-token",
                   SMS_ENDPOINT="https://app.philsms.com/api/v3/sms/send")
class PhilSmsFailureTest(SimpleTestCase):
    def _send(self, body):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            return send_sms("09171234567", "NACC SYS: probe", "probe")

    def test_an_error_inside_a_200_is_a_failure(self):
        # The whole reason this provider needs its own parser: PhilSMS answers
        # HTTP 200 and puts the refusal in the body, so a status-code check
        # alone reports a message that was never sent as sent.
        result = self._send(json.dumps(
            {"status": "error", "message": "Insufficient balance."}))
        self.assertFalse(result.ok)
        self.assertIn("Insufficient balance", result.detail)

    def test_the_gateways_own_words_reach_the_test_button(self):
        result = self._send(json.dumps(
            {"status": "error", "message": "Sender ID not approved."}))
        self.assertIn("Sender ID not approved", result.detail)

    def test_a_body_that_is_not_json_does_not_raise(self):
        result = self._send("<html>maintenance</html>")
        self.assertIsNotNone(result.detail)

    def test_no_key_is_refused_before_the_network(self):
        with override_settings(SMS_API_KEY=""):
            with patch("accounts.sms.urllib.request.urlopen") as opened:
                result = send_sms("09171234567", "probe", "probe")
                opened.assert_not_called()
        self.assertFalse(result.ok)


@override_settings(SMS_PROVIDER="philsms", SMS_API_KEY="test-token")
class TheProviderSettingStillChoosesTest(SimpleTestCase):
    def _request_made(self):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(OK_BODY)
            send_sms("09171234567", "probe", "probe")
            return opened.call_args[0][0]

    def test_switching_provider_switches_gateway(self):
        """The claim accounts/sms.py makes: moving gateway is one setting.

        Asserted on the request that actually goes out rather than on which
        function got called - the two gateways are told apart by their wire
        format, which is the thing that has to be right.
        """
        philsms = self._request_made()
        self.assertIn("philsms.com", philsms.full_url)
        self.assertEqual("application/json", philsms.get_header("Content-type"))

        with override_settings(SMS_PROVIDER="semaphore"):
            semaphore = self._request_made()
        self.assertIn("semaphore.co", semaphore.full_url)
        self.assertEqual("application/x-www-form-urlencoded",
                         semaphore.get_header("Content-type"))

    def test_neither_gateway_needs_SMS_ENDPOINT_to_be_set(self):
        # It used to default to Semaphore's URL for every provider, so picking
        # PhilSMS and leaving it alone posted JSON at Semaphore and read the
        # refusal as a PhilSMS fault. Both tests above run with it unset.
        from django.conf import settings as live
        self.assertEqual("", live.SMS_ENDPOINT)

    def test_an_unknown_provider_falls_back_to_the_log(self):
        # Never a crash and never a silent no-op: a typo in SMS_PROVIDER writes
        # the message to the log rather than losing it.
        with override_settings(SMS_PROVIDER="typo"):
            self.assertTrue(send_sms("09171234567", "probe", "probe").ok)
