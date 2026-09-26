"""Semaphore, the gateway this office is switching on.

It was the first one written and the only one with no tests of its own - the
PhilSMS and textbee files each pin their wire format, and Semaphore's was
asserted only as far as "form-encoded, at semaphore.co". What that hid:

* Its reader called any JSON reply without a "Failed" status a success. A
  validation error inside a 200, or an empty list, was reported as sent; and a
  list of sentences raised AttributeError out of a function documented as
  never raising.
* Semaphore silently discards a message that begins with TEST - accepted, not
  sent, not charged, and nothing in the reply says so.
* Its account check put the API key in the query string, and a network
  failure printed that URL, key included, onto the settings page.
* The verification code went through the ordinary queue. Semaphore has a
  route for codes that telcos do not hold behind bulk traffic.

Every one of those fails as a message that never arrives while the screen
says it went, which is the failure accounts/sms.py is arranged around.
"""
import json
import urllib.error
import urllib.parse
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from accounts.sms import PROVIDERS, check_gateway, send_sms
from accounts.tests.test_sms_philsms import FakeResponse

QUEUED = json.dumps([{
    "message_id": 1234567, "recipient": "639171234567",
    "message": "NACC SYS: probe", "sender_name": "NACC",
    "network": "Globe", "status": "Pending",
}])


def _form(request):
    return dict(urllib.parse.parse_qsl(request.data.decode()))


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="sem-key",
                   SMS_ENDPOINT="", SMS_SENDER_NAME="NACC")
class SemaphoreRequestShapeTest(SimpleTestCase):
    def _send(self, body=QUEUED, text="NACC SYS: probe", **kwargs):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            result = send_sms("0917 123 4567", text, "probe", **kwargs)
            request = opened.call_args[0][0] if opened.call_args else None
        return result, request

    def test_it_is_registered_as_a_provider(self):
        self.assertIn("semaphore", PROVIDERS)

    def test_it_posts_a_form_to_the_messages_route(self):
        _, request = self._send()
        self.assertEqual("POST", request.get_method())
        self.assertEqual("https://api.semaphore.co/api/v4/messages", request.full_url)
        self.assertEqual("application/x-www-form-urlencoded",
                         request.get_header("Content-type"))

    def test_the_key_the_message_and_the_sender_travel_in_the_body(self):
        _, request = self._send()
        form = _form(request)
        self.assertEqual("sem-key", form["apikey"])
        self.assertEqual("NACC SYS: probe", form["message"])
        self.assertEqual("NACC", form["sendername"])

    def test_the_number_goes_as_semaphore_writes_it(self):
        # Stored +639171234567; Semaphore echoes 639171234567.
        _, request = self._send()
        self.assertEqual("639171234567", _form(request)["number"])

    @override_settings(SMS_SENDER_NAME="")
    def test_no_sender_name_means_none_is_sent(self):
        _, request = self._send()
        self.assertNotIn("sendername", _form(request))

    def test_a_queued_message_is_reported_with_its_id(self):
        # The id is what finds it in Semaphore's dashboard.
        result, _ = self._send()
        self.assertTrue(result.ok)
        self.assertIn("1234567", result.detail)


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="sem-key",
                   SMS_ENDPOINT="", SMS_SENDER_NAME="NACC")
class SemaphoreCodeRouteTest(SimpleTestCase):
    TEMPLATE = "NACC SYS: your verification code is {otp}. It expires in 10 minutes."

    def _send(self, body=None, **overrides):
        body = body or json.dumps([{"message_id": 99, "status": "Pending",
                                    "code": 123456}])
        with override_settings(**overrides), \
                patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            result = send_sms("09171234567", self.TEMPLATE, "phone verification",
                              otp_code="123456")
            request = opened.call_args[0][0]
        return result, request

    def test_a_code_goes_by_the_code_route(self):
        _, request = self._send()
        self.assertEqual("https://api.semaphore.co/api/v4/otp", request.full_url)

    def test_semaphore_is_given_the_code_and_the_place_for_it(self):
        _, request = self._send()
        form = _form(request)
        self.assertEqual("123456", form["code"])
        self.assertIn("{otp}", form["message"])
        self.assertNotIn("123456", form["message"])

    def test_the_code_sent_is_handed_back(self):
        result, _ = self._send()
        self.assertTrue(result.ok)
        self.assertEqual("123456", result.code)

    def test_a_leading_zero_lost_to_json_is_still_the_same_code(self):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(json.dumps(
                [{"message_id": 1, "status": "Pending", "code": 12345}]))
            result = send_sms("09171234567", self.TEMPLATE, "probe", otp_code="012345")
        self.assertEqual("012345", result.code)

    def test_a_code_the_gateway_chose_itself_is_the_one_handed_back(self):
        result, _ = self._send(body=json.dumps(
            [{"message_id": 1, "status": "Pending", "code": 777111}]))
        self.assertEqual("777111", result.code)

    def test_an_overridden_endpoint_is_honoured_for_codes_too(self):
        # SMS_ENDPOINT points sending somewhere else - a mock, a relay. Codes
        # quietly going to the real gateway past it would be the surprise.
        result, request = self._send(body=json.dumps(
            [{"message_id": 1, "status": "Pending"}]),
            SMS_ENDPOINT="https://relay.example/messages")
        self.assertEqual("https://relay.example/messages", request.full_url)
        form = _form(request)
        self.assertIn("123456", form["message"])
        self.assertNotIn("code", form)
        self.assertEqual("123456", result.code)


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="sem-key",
                   SMS_ENDPOINT="", SMS_SENDER_NAME="NACC")
class SemaphoreRefusalTest(SimpleTestCase):
    def _send(self, body, text="NACC SYS: probe"):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            return send_sms("09171234567", text, "probe")

    def test_a_validation_error_inside_a_200_is_a_failure(self):
        result = self._send(json.dumps({"number": ["The number format is invalid."]}))
        self.assertFalse(result.ok)
        self.assertIn("The number format is invalid", result.detail)

    def test_a_list_of_sentences_is_a_failure_and_does_not_raise(self):
        result = self._send(json.dumps(["Your current balance is insufficient."]))
        self.assertFalse(result.ok)
        self.assertIn("insufficient", result.detail)

    def test_an_empty_list_is_not_a_delivery(self):
        self.assertFalse(self._send("[]").ok)

    def test_a_failed_status_is_a_failure(self):
        result = self._send(json.dumps([{"message_id": 1, "status": "Failed"}]))
        self.assertFalse(result.ok)
        self.assertIn("Failed", result.detail)

    def test_a_reply_that_cannot_be_read_is_not_called_sent(self):
        result = self._send("<html>maintenance</html>")
        self.assertFalse(result.ok)

    def test_a_refused_sender_name_says_what_to_do_about_it(self):
        result = self._send(json.dumps(
            {"sendername": ["The selected sendername is invalid."]}))
        self.assertFalse(result.ok)
        self.assertIn("registered", result.detail)

    def test_a_message_beginning_with_test_is_refused_before_the_network(self):
        # Semaphore would accept it, bill nothing, send nothing, say nothing.
        for text in ("TEST message", "  test: hello", "Test"):
            with self.subTest(text=text), \
                    patch("accounts.sms.urllib.request.urlopen") as opened:
                result = send_sms("09171234567", text, "probe")
                opened.assert_not_called()
            self.assertFalse(result.ok)
            self.assertIn("TEST", result.detail)

    def test_test_later_in_the_message_is_fine(self):
        self.assertTrue(self._send(QUEUED, text="NACC SYS: test message 123456").ok)

    def test_no_key_is_refused_before_the_network(self):
        with override_settings(SMS_API_KEY=""), \
                patch("accounts.sms.urllib.request.urlopen") as opened:
            result = send_sms("09171234567", "probe", "probe")
            opened.assert_not_called()
        self.assertFalse(result.ok)

    def _http_error(self, code, body):
        error = urllib.error.HTTPError(
            "https://api.semaphore.co/api/v4/messages", code, "err", {}, None)
        error.read = lambda: body.encode()
        with patch("accounts.sms.urllib.request.urlopen", side_effect=error):
            return send_sms("09171234567", "NACC SYS: probe", "probe")

    def test_a_422_sender_refusal_names_the_sender(self):
        result = self._http_error(422, json.dumps(
            {"sendername": ["The selected sendername is invalid."]}))
        self.assertFalse(result.ok)
        self.assertIn("'NACC'", result.detail)
        self.assertIn("sendername is invalid", result.detail)

    def test_a_rate_limit_says_to_wait(self):
        result = self._http_error(429, "Too Many Attempts.")
        self.assertIn("Wait a minute", result.detail)

    def test_an_unreachable_gateway_names_where_it_tried(self):
        # The old copy printed SMS_ENDPOINT, which is blank unless overridden.
        with patch("accounts.sms.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timed out")):
            result = send_sms("09171234567", "NACC SYS: probe", "probe")
        self.assertIn("api.semaphore.co", result.detail)


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="sem-key", SMS_ENDPOINT="")
class SemaphoreCheckTest(SimpleTestCase):
    def _check(self, body):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            result = check_gateway()
            request = opened.call_args[0][0]
        return result, request

    ACCOUNT = {"account_id": 90290, "account_name": "RACCO I",
               "status": "Active", "credit_balance": 500}

    def test_it_asks_the_account_route_and_sends_nothing(self):
        _, request = self._check(json.dumps(self.ACCOUNT))
        self.assertEqual("GET", request.get_method())
        self.assertIn("/api/v4/account", request.full_url)
        self.assertNotIn("/messages", request.full_url)

    def test_it_reports_the_account_and_the_balance_readably(self):
        result, _ = self._check(json.dumps(self.ACCOUNT))
        self.assertTrue(result.ok)
        self.assertIn("RACCO I", result.detail)
        self.assertIn("500 credits", result.detail)

    def test_no_credit_fails_the_check(self):
        # The key authenticates and nothing will send; printing a balance of
        # zero under a green tick is a misreading waiting to happen.
        result, _ = self._check(json.dumps({**self.ACCOUNT, "credit_balance": 0}))
        self.assertFalse(result.ok)
        self.assertIn("no credit", result.detail)

    def test_an_inactive_account_fails_the_check(self):
        result, _ = self._check(json.dumps({**self.ACCOUNT, "status": "Inactive"}))
        self.assertFalse(result.ok)

    def test_an_answer_that_cannot_be_read_confirms_nothing(self):
        # A maintenance page or a proxy's error page, served as a 200.
        result, _ = self._check("<html>maintenance</html>")
        self.assertFalse(result.ok)

    def test_a_refused_key_in_a_200_is_a_failure(self):
        result, _ = self._check(json.dumps(
            {"apikey": ["The selected apikey is invalid."]}))
        self.assertFalse(result.ok)
        self.assertIn("apikey is invalid", result.detail)

    def test_the_key_never_reaches_the_screen(self):
        # The account route takes the key in the query string, and a network
        # failure used to print the whole URL onto the settings page.
        with patch("accounts.sms.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timed out")):
            result = check_gateway()
        self.assertFalse(result.ok)
        self.assertNotIn("sem-key", result.detail)
        self.assertIn("api.semaphore.co", result.detail)
