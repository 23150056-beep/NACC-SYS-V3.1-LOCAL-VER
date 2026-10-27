"""manage.py sms_live_check: the live check, tested with the network patched.

Nothing here reaches Semaphore: urlopen is replaced, time.sleep is replaced (the
command counts waiting by its sleeps, so no clock is read), and no real key is
used. The command itself is never run by `manage.py test` against a gateway.
"""
import json
import urllib.parse
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts import sms_catalogue
from accounts.management.commands.sms_live_check import is_mismatch
from accounts.models import SmsSend
from accounts.tests.test_sms_philsms import FakeResponse

KEY = "live-check-secret-key-xyz"
NUMBER = "09171234567"
LOCAL_DB = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:",
                        "HOST": "", "ATOMIC_REQUESTS": False, "AUTOCOMMIT": True,
                        "CONN_MAX_AGE": 0, "CONN_HEALTH_CHECKS": False, "OPTIONS": {},
                        "TIME_ZONE": None, "USER": "", "PASSWORD": "", "PORT": "",
                        "TEST": {}}}
SETTINGS = dict(SMS_PROVIDER="semaphore", SMS_API_KEY=KEY, SMS_ENDPOINT="",
                SMS_SENDER_NAME="NACC", DEBUG=True)


class Gateway:
    """A fake Semaphore: a POST queues a message, a GET reports its status."""

    def __init__(self, status="Sent", statuses=None, send_body=None):
        self.status = status
        self.statuses = statuses or {}        # message_id -> list of statuses in turn
        self.send_body = send_body
        self.posts = []
        self.gets = []
        self.next_id = 5000

    def __call__(self, request, timeout=None):
        url = request.full_url
        if request.get_method() == "POST":
            self.posts.append(request)
            if self.send_body is not None:
                return FakeResponse(self.send_body)
            self.next_id += 1
            return FakeResponse(json.dumps([{
                "message_id": self.next_id, "status": "Pending",
                "recipient": "639171234567", "message": "x"}]))
        self.gets.append(url)
        message_id = url.split("?")[0].rsplit("/", 1)[1]
        queue = self.statuses.get(int(message_id))
        status = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else self.status
        return FakeResponse(json.dumps([{"message_id": int(message_id), "status": status}]))


@override_settings(**SETTINGS)
class LiveCheckTest(TestCase):
    def run_command(self, *args, typed=NUMBER, gateway=None):
        gateway = gateway or Gateway()
        out = StringIO()
        with patch("accounts.sms.urllib.request.urlopen", gateway), \
                patch("accounts.management.commands.sms_live_check.time.sleep") as nap, \
                patch("builtins.input", return_value=typed) as asked:
            try:
                call_command("sms_live_check", "--to", NUMBER, *args, stdout=out)
                error = None
            except CommandError as exc:
                error = exc
        return out.getvalue(), error, gateway, nap, asked

    # -- refusals ----------------------------------------------------------------

    @override_settings(SMS_PROVIDER="console")
    def test_refuses_the_console_provider(self):
        _, error, gateway, _, _ = self.run_command()
        self.assertIn("console", str(error))
        self.assertEqual([], gateway.posts)
        self.assertEqual(0, SmsSend.objects.count())

    @override_settings(SMS_PROVIDER="philsms")
    def test_refuses_a_gateway_that_cannot_be_asked_for_status(self):
        _, error, gateway, _, _ = self.run_command()
        self.assertIn("Semaphore-only", str(error))
        self.assertEqual([], gateway.posts)

    @override_settings(SMS_API_KEY="")
    def test_refuses_a_missing_key(self):
        _, error, gateway, _, _ = self.run_command()
        self.assertIn("SMS_API_KEY", str(error))
        self.assertEqual([], gateway.posts)

    def test_refuses_a_hosted_database(self):
        hosted = {"default": dict(LOCAL_DB["default"], HOST="ep-x.neon.tech")}
        with override_settings(DATABASES=hosted):
            _, error, gateway, _, asked = self.run_command()
        self.assertIn("hosted", str(error))
        self.assertEqual([], gateway.posts)
        asked.assert_not_called()

    def test_refuses_an_unknown_kind(self):
        _, error, gateway, _, _ = self.run_command("--only", "not_a_kind")
        self.assertIn("not_a_kind", str(error))
        self.assertEqual([], gateway.posts)

    # -- dry run and confirmation ------------------------------------------------

    def test_a_dry_run_prints_the_table_and_sends_nothing(self):
        out, error, gateway, _, asked = self.run_command("--dry-run")
        self.assertIsNone(error)
        self.assertEqual([], gateway.posts)
        self.assertEqual([], gateway.gets)
        asked.assert_not_called()
        self.assertEqual(0, SmsSend.objects.count())
        for kind in sms_catalogue.ENTRIES:
            self.assertIn(kind, out)
        self.assertIn("Estimated total", out)

    def test_a_wrong_typed_number_aborts_with_nothing_sent(self):
        out, error, gateway, _, _ = self.run_command(typed="09179999999")
        self.assertIsNone(error)
        self.assertIn("Nothing was sent", out)
        self.assertEqual([], gateway.posts)
        self.assertEqual(0, SmsSend.objects.count())

    # -- the run -----------------------------------------------------------------

    @override_settings(SMS_PER_NUMBER_DAILY=1)
    def test_sends_one_of_each_kind_past_the_limits_and_reports_clean(self):
        out, error, gateway, nap, _ = self.run_command()
        self.assertIsNone(error, out)
        self.assertEqual(len(sms_catalogue.ENTRIES), len(gateway.posts))
        self.assertEqual(len(sms_catalogue.ENTRIES), SmsSend.objects.count())
        self.assertEqual(set(sms_catalogue.ENTRIES), set(SmsSend.objects.values_list("kind", flat=True)))
        self.assertFalse(SmsSend.objects.exclude(outcome="sent").exists())
        self.assertEqual({"live-check"}, set(SmsSend.objects.values_list("subject", flat=True)))
        # Every code message went to the code route with a six-digit code.
        otp = [p for p in gateway.posts if p.full_url.endswith("/otp")]
        self.assertEqual(2, len(otp))
        for request in otp:
            form = dict(urllib.parse.parse_qsl(request.data.decode()))
            self.assertRegex(form["code"], r"^\d{6}$")
        self.assertNotIn("MISMATCH", out)
        self.assertIn("handset", out)
        self.assertNotIn(KEY, out)
        self.assertTrue(nap.called)

    def test_only_narrows_the_list(self):
        out, error, gateway, _, _ = self.run_command("--only", "config_test", "phone_code")
        self.assertIsNone(error, out)
        self.assertEqual(2, len(gateway.posts))

    def test_it_polls_until_the_status_settles(self):
        gateway = Gateway(statuses={5001: ["Pending", "Queued", "Sent"]})
        out, error, gateway, nap, _ = self.run_command("--only", "config_test", gateway=gateway)
        self.assertIsNone(error, out)
        self.assertEqual(3, len(gateway.gets))
        self.assertIn("Sent", out)

    def test_it_gives_up_waiting_after_the_wait(self):
        gateway = Gateway(status="Pending")
        out, error, gateway, nap, _ = self.run_command(
            "--only", "config_test", "--wait", "30", gateway=gateway)
        self.assertIsNone(error, out)
        self.assertEqual(3, len(gateway.gets))       # now, +15 s, +30 s
        self.assertIn("Pending", out)

    @override_settings(SMS_API_KEY=KEY)
    def test_a_recorded_refusal_that_semaphore_sent_is_a_mismatch(self):
        # A reply the system cannot read as sent: no message_id. Semaphore,
        # asked for an id, would say Sent - the 27 Oct fault.
        gateway = Gateway(send_body=json.dumps([{"status": "Pending"}]))
        out, error, gateway, _, _ = self.run_command("--only", "config_test", gateway=gateway)
        self.assertIsNotNone(error)
        self.assertIn("MISMATCH", out)
        self.assertNotIn(KEY, out)
        self.assertNotIn(KEY, str(error))

    def test_a_row_recorded_sent_that_semaphore_failed_is_a_mismatch(self):
        gateway = Gateway(status="Failed")
        out, error, gateway, _, _ = self.run_command("--only", "config_test", gateway=gateway)
        self.assertIsNotNone(error)
        self.assertIn("MISMATCH", out)


@override_settings(SMS_PROVIDER="console", SMS_PER_NUMBER_DAILY=2, SMS_DAILY_CREDIT_LIMIT=10)
class LiveCheckRowsAndLimitsTest(TestCase):
    def _rows(self, number, n, kind="config_test", credits=1):
        from accounts.sms import LIVE_CHECK_SUBJECT, number_fingerprint
        from accounts.phone import normalise_ph_mobile
        h, tail = number_fingerprint(normalise_ph_mobile(number))
        for _ in range(n):
            SmsSend.objects.create(kind=kind, route="normal", credits=credits,
                                   outcome="sent", subject=LIVE_CHECK_SUBJECT,
                                   number_hash=h, number_tail=tail)

    def test_live_check_rows_do_not_block_the_number_or_the_kind(self):
        from accounts.sms import send_sms
        self._rows(NUMBER, 3, "config_test", credits=0)
        self._rows(NUMBER, 1, "temporary_password", credits=0)
        for kind, text in (("config_test", "NACC SYS: test message 1. ok"),
                           ("temporary_password", "NACC SYS: pw")):
            result = send_sms(NUMBER, text, kind)
            self.assertEqual("sent", result.outcome, (kind, result.detail))

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY=KEY, SMS_ENDPOINT="")
    def test_they_still_count_toward_the_agency_credits(self):
        from accounts.sms import send_sms
        self._rows("09179998888", 9, credits=1)
        with patch("accounts.sms.urllib.request.urlopen", Gateway()):
            result = send_sms(NUMBER, "NACC SYS: test message 1. ok", "config_test")
            self.assertEqual("sent", result.outcome)       # 9 + 1 = 10, at the limit
            result = send_sms(NUMBER, "NACC SYS: pw", "temporary_password")
        self.assertEqual("limited", result.outcome)
        self.assertIn("daily text message limit", result.detail)


class MismatchRuleTest(TestCase):
    def test_the_rule(self):
        self.assertFalse(is_mismatch("sent", "1", "Sent"))
        self.assertFalse(is_mismatch("sent", "1", "Pending"))
        self.assertFalse(is_mismatch("refused", "1", "Failed"))
        self.assertTrue(is_mismatch("refused", "1", "Sent"))
        self.assertTrue(is_mismatch("unknown", "1", "Queued"))
        self.assertTrue(is_mismatch("sent", "1", "Refunded"))
        self.assertTrue(is_mismatch("sent", "", "Sent"))
        self.assertFalse(is_mismatch("sent", "1", ""))      # no verdict either way
