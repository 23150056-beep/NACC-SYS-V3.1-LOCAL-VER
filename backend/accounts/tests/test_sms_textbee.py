"""A gateway you can run on your own phone.

The two aggregators both want a business account and a top-up bigger than this
office will send in three years. textbee takes a different shape: the app runs
on an Android handset, the SIM in it sends the message, and the API is a relay
that tells the phone what to send. A personal account and a personal number
are the whole setup.

That changes which failures matter. There is no balance to run out of and no
sender name to get approved; what breaks instead is a phone that is switched
off, out of signal, or never linked in the first place. So the check here
counts LINKED DEVICES rather than credits - "the key is valid but no phone is
attached" is the state this gateway actually fails in, and it looks identical
to working right up until somebody needs a message.

Its wire format disagrees with both aggregators again: the key is an x-api-key
header rather than a bearer token or a body field, and the recipient is an
ARRAY, so a string here sends nothing and reports nothing wrong.
"""
import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from accounts.sms import PROVIDERS, check_gateway, send_sms
from accounts.tests.test_sms_philsms import FakeResponse

SENT_OK = json.dumps({"data": {"success": True, "recipientCount": 1}})


@override_settings(SMS_PROVIDER="textbee", SMS_API_KEY="tb-key", SMS_ENDPOINT="")
class TextbeeRequestShapeTest(SimpleTestCase):
    def _send(self, body=SENT_OK):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            result = send_sms("09171234567", "NACC SYS: probe", "probe")
            request = opened.call_args[0][0]
        return result, request

    def test_it_is_registered_as_a_provider(self):
        self.assertIn("textbee", PROVIDERS)

    def test_it_posts_to_the_gateway_send_route(self):
        _, request = self._send()
        self.assertEqual("POST", request.get_method())
        self.assertTrue(request.full_url.endswith("/gateway/send-sms"),
                        request.full_url)

    def test_the_key_travels_as_x_api_key(self):
        # Not a bearer token and not a body field. Both of the other gateways
        # do it differently and all three fail as an unhelpful 401.
        _, request = self._send()
        self.assertEqual("tb-key", request.get_header("X-api-key"))

    def test_the_recipient_is_a_LIST_not_a_string(self):
        # The one that would bite silently: the API takes an array, and a bare
        # string is accepted by json.dumps and delivered to nobody.
        _, request = self._send()
        body = json.loads(request.data)
        self.assertIsInstance(body["recipients"], list)
        self.assertEqual(["+639171234567"], body["recipients"])

    def test_the_number_keeps_its_plus(self):
        # PhilSMS documents it without; this one's examples keep E.164 intact.
        # The stored form is already right, so the rule here is to leave it be.
        _, request = self._send()
        self.assertTrue(json.loads(request.data)["recipients"][0].startswith("+"))

    def test_the_message_is_carried(self):
        _, request = self._send()
        self.assertEqual("NACC SYS: probe", json.loads(request.data)["message"])

    def test_a_success_is_reported_as_sent(self):
        result, _ = self._send()
        self.assertTrue(result.ok)

    def test_no_key_is_refused_before_the_network(self):
        with override_settings(SMS_API_KEY=""):
            with patch("accounts.sms.urllib.request.urlopen") as opened:
                result = send_sms("09171234567", "probe", "probe")
                opened.assert_not_called()
        self.assertFalse(result.ok)


@override_settings(SMS_PROVIDER="textbee", SMS_API_KEY="tb-key", SMS_ENDPOINT="")
class TextbeeCheckCountsPhonesTest(SimpleTestCase):
    """Credits are not the thing that runs out here. A phone is."""

    def _check(self, body):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.return_value = FakeResponse(body)
            result = check_gateway()
            request = opened.call_args[0][0]
        return result, request

    def test_it_asks_for_the_device_list_and_sends_nothing(self):
        _, request = self._check(json.dumps({"data": []}))
        self.assertTrue(request.full_url.endswith("/gateway/devices"),
                        request.full_url)
        self.assertNotIn("send-sms", request.full_url)
        self.assertEqual("GET", request.get_method())

    def test_it_authenticates_the_way_the_sender_does(self):
        _, request = self._check(json.dumps({"data": []}))
        self.assertEqual("tb-key", request.get_header("X-api-key"))

    def test_a_linked_phone_is_reported(self):
        result, _ = self._check(json.dumps(
            {"data": [{"_id": "abc", "model": "Redmi 9", "enabled": True}]}))
        self.assertTrue(result.ok)
        self.assertIn("1", result.detail)

    def test_a_valid_key_with_NO_phone_is_not_a_pass(self):
        # The failure this gateway actually has. A key that authenticates and
        # a gateway that cannot send are the same screen otherwise.
        result, _ = self._check(json.dumps({"data": []}))
        self.assertFalse(result.ok)
        self.assertIn("no", result.detail.lower())

    def test_a_refused_key_says_so(self):
        with patch("accounts.sms.urllib.request.urlopen") as opened:
            opened.side_effect = _http_error(401, '{"message":"Unauthorized"}')
            result = check_gateway()
        self.assertFalse(result.ok)


def _http_error(code, body):
    import io
    import urllib.error

    def raise_it(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.textbee.dev/api/v1/gateway/devices", code, "err", {},
            io.BytesIO(body.encode()))
    return raise_it


@override_settings(SMS_API_KEY="k", SMS_ENDPOINT="")
class EachGatewayKeepsItsOwnWireFormatTest(SimpleTestCase):
    """Three providers now, and none of them agree about anything."""

    def _request_for(self, provider):
        with override_settings(SMS_PROVIDER=provider):
            with patch("accounts.sms.urllib.request.urlopen") as opened:
                opened.return_value = FakeResponse(SENT_OK)
                send_sms("09171234567", "probe", "probe")
                return opened.call_args[0][0]

    def test_they_do_not_bleed_into_each_other(self):
        textbee = self._request_for("textbee")
        philsms = self._request_for("philsms")
        semaphore = self._request_for("semaphore")

        self.assertIn("textbee.dev", textbee.full_url)
        self.assertIn("philsms.com", philsms.full_url)
        self.assertIn("semaphore.co", semaphore.full_url)

        self.assertEqual("k", textbee.get_header("X-api-key"))
        self.assertEqual("Bearer k", philsms.get_header("Authorization"))
        self.assertIn("apikey=k", semaphore.data.decode())
