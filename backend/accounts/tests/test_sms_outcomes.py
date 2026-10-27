"""What the sender makes of a gateway's answer, what it writes down, and when
it refuses to send at all.

The reply shapes here are Semaphore's real ones, captured on 27 Oct 2026 with
the account email replaced by a stand-in of the same length. They matter for
their LENGTH: Semaphore echoes the message, the account email and the sender
name back, so a reply is 400-460 characters, and the reader used to keep the
first 400. A reply cut mid-field is not JSON, and a delivered, paid text was
recorded as a failure (nine of eleven, that day). The older tests missed it
because their fake replies were short.
"""
import hashlib
import hmac
import http.client
import json
import socket
import urllib.error
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts import sms_catalogue
from accounts.models import Role, SmsSend
from accounts.sms import (LIMITED, REFUSED, SENT, UNKNOWN, SmsResult, segments,
                          send_sms)
from accounts.tests.test_sms_philsms import FakeResponse

User = get_user_model()
NUMBER = "09171234567"
STORED = "+639171234567"
# Any kind with no rules of its own, so a test of the global limits is not
# also a test of a kind's.
FREE = "session_reminder"

ACCOUNT_EMAIL = "xxxxxxxxxxxxxx@example.com"


def real_reply(text, status="Pending", otp=None, message_id=283519210):
    """A Semaphore reply in the shape captured live, for `text`."""
    record = {
        "message_id": message_id, "user_id": 82691, "user": ACCOUNT_EMAIL,
        "account_id": 82551, "account": "NACC", "recipient": "639690784006",
        "message": text, "sender_name": "NACCRACCO1", "network": "Smart",
        "status": status, "type": "Single" if otp is None else "Otp",
        "source": "Api", "created_at": "2026-10-27 18:16:08",
        "updated_at": "2026-10-27 18:16:08",
    }
    if otp is not None:
        record = {**record, "code": otp}
    return json.dumps([record], separators=(",", ":"))


class _Raises(FakeResponse):
    """A response whose body fails when read - a connection that dies after
    the request went."""

    def __init__(self, exc):
        super().__init__("")
        self._exc = exc

    def read(self, size=-1):
        raise self._exc


def _http_error(code, body):
    error = urllib.error.HTTPError("https://api.semaphore.co/x", code, "x", {}, None)
    error.read = lambda size=-1: body.encode()
    return error


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="sem-key",
                   SMS_ENDPOINT="", SMS_SENDER_NAME="NACCRACCO1")
class TheWholeReplyIsReadTest(TestCase):
    def _send(self, body, text, **kwargs):
        # A different number each time: sixteen replies to one would meet the
        # per-number limit, which is not what this class is about.
        self.sent = getattr(self, "sent", 0) + 1
        with patch("accounts.sms.urllib.request.urlopen",
                   return_value=FakeResponse(body)):
            return send_sms(f"0917555{self.sent:04d}", text, FREE, **kwargs)

    def test_a_real_reply_is_sent_with_its_message_id_whatever_its_length(self):
        lengths = []
        for kind in ("session_reminder", "config_test", "custodian_missed",
                     "custodian_moved", "assignment", "temporary_password",
                     "custodian_cancelled", "custodian_booked"):
            text = sms_catalogue.render_sample(kind)
            for status in ("Pending", "Sent"):
                body = real_reply(text, status)
                lengths.append(len(body))
                with self.subTest(kind=kind, status=status, reply=len(body)):
                    result = self._send(body, text)
                    self.assertEqual(SENT, result.outcome, result.detail)
                    self.assertTrue(result.ok)
                    self.assertEqual("283519210", result.message_id)
        # The point of the test: replies longer than the 400 the reader used to
        # keep, up to the longest seen live.
        self.assertGreater(min(lengths), 380)
        self.assertGreater(max(lengths), 440)

    def test_the_old_400_character_cut_would_have_failed_these(self):
        text = sms_catalogue.render_sample("custodian_booked")
        body = real_reply(text)
        self.assertGreater(len(body), 400)
        with self.assertRaises(ValueError):
            json.loads(body[:400])
        self.assertTrue(self._send(body, text).ok)

    def test_an_otp_reply_carries_its_code_and_is_sent(self):
        text = "NACC SYS: your verification code is {otp}. It expires in 10 minutes."
        body = real_reply(text.replace("{otp}", "482913"), otp="482913")
        with patch("accounts.sms.urllib.request.urlopen",
                   return_value=FakeResponse(body)):
            result = send_sms(NUMBER, text, "phone_code", otp_code="482913")
        self.assertEqual(SENT, result.outcome, result.detail)
        self.assertEqual("482913", result.code)
        self.assertEqual("283519210", result.message_id)

    def test_a_long_error_body_is_read_whole_but_shown_short(self):
        body = json.dumps({"message": "x" * 900})
        with patch("accounts.sms.urllib.request.urlopen",
                   side_effect=_http_error(400, body)):
            result = send_sms(NUMBER, "NACC SYS: probe", FREE)
        self.assertEqual(REFUSED, result.outcome)
        self.assertLess(len(result.detail), 700)


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="sem-key",
                   SMS_ENDPOINT="", SMS_SENDER_NAME="NACCRACCO1")
class EachOutcomeTest(TestCase):
    def _send(self, number=NUMBER, text="NACC SYS: probe", **opener):
        with patch("accounts.sms.urllib.request.urlopen", **opener):
            return send_sms(number, text, FREE)

    def assertOutcome(self, outcome, result):
        self.assertEqual(outcome, result.outcome, result.detail)
        self.assertEqual(outcome == SENT, result.ok)
        self.assertEqual(outcome in (REFUSED, LIMITED), result.refused)

    # -- refused: nothing went out -------------------------------------------

    def test_no_key_is_refused(self):
        with override_settings(SMS_API_KEY=""):
            self.assertOutcome(REFUSED, self._send(return_value=FakeResponse("[]")))

    def test_an_invalid_number_is_refused_and_leaves_no_record(self):
        result = self._send(number="12345", return_value=FakeResponse("[]"))
        self.assertOutcome(REFUSED, result)
        self.assertEqual(0, SmsSend.objects.count())

    def test_a_message_opening_with_test_is_refused(self):
        result = self._send(text="TEST of the thing",
                            return_value=FakeResponse(real_reply("x")))
        self.assertOutcome(REFUSED, result)

    def test_a_4xx_is_refused(self):
        for code in (400, 401, 402, 403, 422, 429):
            with self.subTest(code=code):
                self.assertOutcome(REFUSED, self._send(
                    side_effect=_http_error(code, '{"message":"no"}')))

    def test_a_readable_reply_with_no_message_id_is_refused(self):
        for body in ("[]", '{"number":["invalid"]}', '["Nope."]'):
            with self.subTest(body=body):
                self.assertOutcome(REFUSED, self._send(
                    return_value=FakeResponse(body)))

    def test_no_active_sender_name_under_a_500_is_refused(self):
        body = json.dumps([{"senderName": "No active sender name found."}])
        self.assertOutcome(REFUSED, self._send(side_effect=_http_error(500, body)))

    def test_a_failed_or_refunded_status_is_refused(self):
        for status in ("Failed", "Refunded"):
            with self.subTest(status=status):
                self.assertOutcome(REFUSED, self._send(
                    return_value=FakeResponse(real_reply("x", status))))

    def test_a_connection_that_never_opened_is_refused(self):
        for reason in (ConnectionRefusedError(111, "refused"),
                       socket.gaierror(-2, "Name or service not known")):
            with self.subTest(reason=type(reason).__name__):
                self.assertOutcome(REFUSED, self._send(
                    side_effect=urllib.error.URLError(reason)))

    # -- unknown: it may have gone -------------------------------------------

    def test_timeouts_are_unknown_however_they_are_raised(self):
        for exc in (urllib.error.URLError(socket.timeout("timed out")),
                    urllib.error.URLError(TimeoutError("timed out"))):
            with self.subTest(how="as a reason", exc=repr(exc)):
                result = self._send(side_effect=exc)
                self.assertOutcome(UNKNOWN, result)
                self.assertIn("may have been sent", result.detail)
        with self.subTest(how="bare, from reading the reply"):
            self.assertOutcome(UNKNOWN, self._send(
                return_value=_Raises(socket.timeout("timed out"))))
        with self.subTest(how="bare, from opening"):
            self.assertOutcome(UNKNOWN, self._send(side_effect=TimeoutError()))

    def test_a_dropped_connection_is_unknown(self):
        for exc in (http.client.RemoteDisconnected("closed"),
                    ConnectionResetError(104, "reset")):
            with self.subTest(exc=type(exc).__name__):
                self.assertOutcome(UNKNOWN, self._send(side_effect=exc))
                self.assertOutcome(UNKNOWN, self._send(
                    side_effect=urllib.error.URLError(exc)))
                self.assertOutcome(UNKNOWN, self._send(return_value=_Raises(exc)))

    def test_a_2xx_that_is_not_json_is_unknown(self):
        self.assertOutcome(UNKNOWN, self._send(
            return_value=FakeResponse("<html>maintenance</html>")))

    def test_a_5xx_with_no_readable_refusal_is_unknown(self):
        for body in ("<html>Bad gateway</html>", ""):
            with self.subTest(body=body):
                self.assertOutcome(UNKNOWN, self._send(
                    side_effect=_http_error(502, body)))

    def test_any_other_exception_is_unknown_and_never_raised(self):
        self.assertOutcome(UNKNOWN, self._send(side_effect=RuntimeError("boom")))

    def test_an_unreadable_reply_says_it_may_have_been_sent(self):
        result = self._send(return_value=FakeResponse("nope"))
        self.assertIn("may have been sent", result.detail)

    # -- the other gateways --------------------------------------------------

    @override_settings(SMS_PROVIDER="philsms", SMS_ENDPOINT="https://x.test/send")
    def test_philsms_refusals_are_refused_and_timeouts_unknown(self):
        self.assertOutcome(REFUSED, self._send(return_value=FakeResponse(
            json.dumps({"status": "error", "message": "no"}))))
        self.assertOutcome(UNKNOWN, self._send(side_effect=TimeoutError()))
        self.assertOutcome(SENT, self._send(return_value=FakeResponse(
            json.dumps({"status": "success"}))))

    @override_settings(SMS_PROVIDER="textbee", SMS_ENDPOINT="https://x.test/send")
    def test_textbee_refusals_are_refused_and_dropped_connections_unknown(self):
        self.assertOutcome(REFUSED, self._send(return_value=FakeResponse(
            json.dumps({"data": {"success": False}}))))
        self.assertOutcome(UNKNOWN, self._send(
            side_effect=http.client.RemoteDisconnected("closed")))
        self.assertOutcome(SENT, self._send(return_value=FakeResponse("{}")))

    def test_a_result_defaults_its_outcome_from_ok(self):
        self.assertEqual(SENT, SmsResult(True).outcome)
        self.assertEqual(REFUSED, SmsResult(False).outcome)
        self.assertFalse(SmsResult(False, outcome=UNKNOWN).ok)
        self.assertFalse(SmsResult(False, outcome=UNKNOWN).refused)
        self.assertTrue(SmsResult(False, outcome=LIMITED).refused)


@override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="sem-key",
                   SMS_ENDPOINT="", SMS_SENDER_NAME="NACCRACCO1")
class TheSpendRecordTest(TestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email="sw@t.ph", username="sw", password="x", role=role)

    def _send(self, kind=FREE, text="NACC SYS: probe", body=None, **kwargs):
        with patch("accounts.sms.urllib.request.urlopen",
                   return_value=FakeResponse(body or real_reply(text))):
            return send_sms(NUMBER, text, kind, **kwargs)

    def test_a_normal_send_costs_its_segments_and_keeps_the_message_id(self):
        self._send()
        row = SmsSend.objects.get()
        self.assertEqual((FREE, "normal", 1, "sent", "283519210"),
                         (row.kind, row.route, row.credits, row.outcome,
                          row.message_id))

    def test_a_code_costs_two_credits_on_the_code_route(self):
        text = sms_catalogue.render("phone_code")
        body = real_reply(text.replace("{otp}", "482913"), otp="482913")
        with patch("accounts.sms.urllib.request.urlopen",
                   return_value=FakeResponse(body)):
            send_sms(NUMBER, text, "phone_code", otp_code="482913",
                     requested_by=self.user, subject="user:5")
        row = SmsSend.objects.get()
        self.assertEqual(("otp", 2, "sent", self.user.pk, "user:5"),
                         (row.route, row.credits, row.outcome,
                          row.requested_by_id, row.subject))

    def test_a_code_through_an_overridden_endpoint_is_a_normal_one_credit_send(self):
        text = sms_catalogue.render("phone_code")
        with override_settings(SMS_ENDPOINT="https://x.test/send"), \
                patch("accounts.sms.urllib.request.urlopen",
                      return_value=FakeResponse(real_reply("x"))):
            send_sms(NUMBER, text, "phone_code", otp_code="482913")
        row = SmsSend.objects.get()
        self.assertEqual(("normal", 1), (row.route, row.credits))

    def test_the_console_provider_is_recorded_at_no_cost(self):
        with override_settings(SMS_PROVIDER="console"):
            self.assertTrue(send_sms(NUMBER, "NACC SYS: probe", FREE).ok)
        row = SmsSend.objects.get()
        self.assertEqual((0, "sent"), (row.credits, row.outcome))

    def test_a_refusal_costs_nothing_and_an_unknown_is_charged_as_if_sent(self):
        with patch("accounts.sms.urllib.request.urlopen",
                   return_value=FakeResponse("[]")):
            send_sms(NUMBER, "NACC SYS: probe", FREE)
        with patch("accounts.sms.urllib.request.urlopen",
                   side_effect=TimeoutError()):
            send_sms(NUMBER, "NACC SYS: probe", FREE)
        refused, unknown = SmsSend.objects.order_by("id")
        self.assertEqual(("refused", 0), (refused.outcome, refused.credits))
        self.assertEqual(("unknown", 1), (unknown.outcome, unknown.credits))

    def test_the_number_and_the_text_are_not_stored(self):
        self._send(text="NACC SYS: a very particular sentence", requested_by=self.user)
        row = SmsSend.objects.get()
        every_value = " ".join(str(getattr(row, f.name))
                               for f in SmsSend._meta.fields)
        for secret in ("9171234567", "particular", "639171234567"):
            self.assertNotIn(secret, every_value)
        expected = hmac.new(settings.SECRET_KEY.encode(), STORED.encode(),
                            hashlib.sha256).hexdigest()
        self.assertEqual((expected, "4567"), (row.number_hash, row.number_tail))

    def test_the_same_number_written_two_ways_is_one_hash(self):
        self._send()
        with patch("accounts.sms.urllib.request.urlopen",
                   return_value=FakeResponse(real_reply("x"))):
            send_sms("+63 917 123 4567", "NACC SYS: probe", FREE)
        self.assertEqual(1, SmsSend.objects.values("number_hash").distinct().count())

    def test_a_row_is_written_even_when_a_limit_refuses(self):
        with override_settings(SMS_PER_NUMBER_DAILY=1):
            self._send()
            result = self._send()
        self.assertEqual(LIMITED, result.outcome)
        refused = SmsSend.objects.order_by("id").last()
        self.assertEqual(("limited", 0), (refused.outcome, refused.credits))

    def test_an_unknown_kind_is_refused_and_nothing_is_sent(self):
        with patch("accounts.sms.urllib.request.urlopen") as opened, \
                self.assertLogs("accounts.sms", "ERROR"):
            result = send_sms(NUMBER, "NACC SYS: probe", "made_up")
        opened.assert_not_called()
        self.assertEqual(REFUSED, result.outcome)

    def test_a_record_that_cannot_be_written_never_fails_the_send(self):
        with patch("accounts.models.SmsSend.objects.create",
                   side_effect=RuntimeError("disk")):
            self.assertTrue(self._send().ok)

    def test_segments_are_counted_the_way_a_telco_does(self):
        self.assertEqual(1, segments("a" * 160))
        self.assertEqual(2, segments("a" * 161))
        self.assertEqual(2, segments("a" * 306))
        self.assertEqual(3, segments("a" * 307))
        self.assertEqual(1, segments("é" * 70 + "—"[:0]))
        self.assertEqual(1, segments("—" * 70))
        self.assertEqual(2, segments("—" * 71))
        self.assertEqual(1, segments("^" * 80))
        self.assertEqual(2, segments("^" * 81))


@override_settings(SMS_PROVIDER="console")
class TheLimitsTest(TestCase):
    """The console provider records sends at no credit, which is enough to
    drive every limit that counts texts; the credit limit has its own class."""

    def setUp(self):
        role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email="sw@t.ph", username="sw", password="x", role=role)

    def _send(self, kind=FREE, number=NUMBER, **kwargs):
        text = ("NACC SYS: code {otp}." if kwargs.get("otp_code")
                else "NACC SYS: probe")
        return send_sms(number, text, kind, **kwargs)

    def _numbers(self, n):
        return [f"0917123{i:04d}" for i in range(n)]

    def _age(self, **by):
        SmsSend.objects.update(created_at=timezone.now() - timedelta(**by))

    # -- one number ----------------------------------------------------------

    @override_settings(SMS_PER_NUMBER_DAILY=3)
    def test_one_number_gets_only_so_many_texts_a_day_of_any_kind(self):
        kinds = ["session_reminder", "config_test", "custodian_missed"]
        for n, kind in enumerate(kinds):
            subject = f"appointment:{n}"       # separate subjects: not a kind rule
            self.assertTrue(self._send(kind, subject=subject).ok, kind)
            self._age(minutes=61)              # past every short window
        refused = self._send("session_reminder")
        self.assertEqual(LIMITED, refused.outcome)
        self.assertTrue(refused.refused)
        self.assertIn("tomorrow", refused.detail)
        self.assertTrue(self._send("session_reminder", number="09170000001").ok)

    @override_settings(SMS_PER_NUMBER_DAILY=2)
    def test_a_day_later_the_allowance_is_back(self):
        self._send()
        self._send()
        self.assertEqual(LIMITED, self._send().outcome)
        self._age(hours=24, minutes=1)
        self.assertTrue(self._send().ok)

    @override_settings(SMS_PER_NUMBER_DAILY=1)
    def test_only_texts_that_may_have_gone_count(self):
        with patch("accounts.sms._send_console",
                   return_value=SmsResult(False, "no")), \
                patch.dict("accounts.sms.PROVIDERS",
                           {"console": lambda *a, **k: SmsResult(False, "no")}):
            self.assertEqual(REFUSED, self._send().outcome)
        self.assertTrue(self._send().ok)         # the refused one used nothing
        self.assertEqual(LIMITED, self._send().outcome)
        # ...and a limited one does not push the allowance further out.
        self.assertEqual(LIMITED, self._send().outcome)

    @override_settings(SMS_PER_NUMBER_DAILY=1)
    def test_an_unknown_counts_because_it_may_have_been_delivered(self):
        with patch.dict("accounts.sms.PROVIDERS", {"console": lambda *a, **k:
                                                   SmsResult(False, "?", outcome=UNKNOWN)}):
            self.assertEqual(UNKNOWN, self._send().outcome)
        self.assertEqual(LIMITED, self._send().outcome)

    # -- the whole agency ----------------------------------------------------

    @override_settings(SMS_PROVIDER="semaphore", SMS_API_KEY="k",
                       SMS_ENDPOINT="", SMS_SENDER_NAME="N",
                       SMS_DAILY_CREDIT_LIMIT=3)
    def test_the_agency_has_a_daily_credit_limit(self):
        def go(number, kind=FREE, **kw):
            with patch("accounts.sms.urllib.request.urlopen",
                       return_value=FakeResponse(real_reply("x"))) as opened:
                text = ("NACC SYS: code {otp}." if kw.get("otp_code")
                        else "NACC SYS: probe")
                result = send_sms(number, text, kind, **kw)
            return result, opened.called
        n = self._numbers(5)
        self.assertTrue(go(n[0])[0].ok)           # 1
        self.assertTrue(go(n[1])[0].ok)           # 2
        # A code is two credits: 2 + 2 > 3, so it is refused before the network.
        code, reached = go(n[2], "phone_code", otp_code="123456")
        self.assertEqual(LIMITED, code.outcome)
        self.assertFalse(reached)
        self.assertIn("daily", code.detail)
        self.assertTrue(go(n[3])[0].ok)           # 3 - exactly the limit
        refused, reached = go(n[4])
        self.assertEqual(LIMITED, refused.outcome)
        self.assertFalse(reached)

    @override_settings(SMS_DAILY_CREDIT_LIMIT=1)
    def test_the_console_costs_nothing_so_it_never_meets_the_credit_limit(self):
        for number in self._numbers(4):
            self.assertTrue(self._send(number=number).ok)

    # -- a kind's own rules --------------------------------------------------

    def test_a_temporary_password_notice_once_per_ten_minutes_per_number(self):
        self.assertTrue(self._send("temporary_password").ok)
        self.assertEqual(LIMITED, self._send("temporary_password").outcome)
        self.assertTrue(self._send("temporary_password", number="09170000002").ok)
        self._age(minutes=11)
        self.assertTrue(self._send("temporary_password").ok)

    def test_an_assignment_notice_is_held_per_number_and_subject(self):
        self.assertTrue(self._send("assignment", subject="child:1").ok)
        self.assertEqual(LIMITED, self._send("assignment", subject="child:1").outcome)
        # Another child to the same psychologist is a different notice.
        self.assertTrue(self._send("assignment", subject="child:2").ok)
        self._age(minutes=61)
        self.assertTrue(self._send("assignment", subject="child:1").ok)

    def test_the_test_text_is_one_a_minute_and_five_an_hour(self):
        for i in range(5):
            self.assertTrue(self._send("config_test").ok, i)
            self.assertEqual(LIMITED, self._send("config_test").outcome, i)
            self._age(minutes=2)
        self.assertEqual(LIMITED, self._send("config_test").outcome)
        self._age(minutes=61)
        self.assertTrue(self._send("config_test").ok)

    def test_the_four_custodian_events_share_four_a_day_per_appointment(self):
        for kind in ("custodian_booked", "custodian_moved", "custodian_moved",
                     "custodian_cancelled"):
            self.assertTrue(self._send(kind, subject="appointment:7").ok, kind)
        over = self._send("custodian_missed", subject="appointment:7")
        self.assertEqual(LIMITED, over.outcome)
        # Another appointment of the same custodian has its own four.
        self.assertTrue(self._send("custodian_moved", subject="appointment:8").ok)
        self._age(hours=24, minutes=1)
        self.assertTrue(self._send("custodian_moved", subject="appointment:7").ok)

    def test_kinds_with_no_rules_are_held_only_by_the_global_limits(self):
        for _ in range(5):
            self.assertTrue(self._send("custodian_reminder", subject="appointment:1").ok)

    def test_the_two_code_kinds_share_two_an_hour_to_one_number(self):
        self.assertTrue(self._send("phone_code", otp_code="111111").ok)
        self.assertTrue(self._send("custodian_code", otp_code="222222").ok)
        for kind in ("phone_code", "custodian_code"):
            result = self._send(kind, otp_code="333333")
            self.assertEqual(LIMITED, result.outcome, kind)
        self._age(minutes=61)
        self.assertTrue(self._send("phone_code", otp_code="444444").ok)

    def test_the_two_code_kinds_share_four_a_day_to_one_number(self):
        for i in range(4):
            self.assertTrue(self._send("phone_code", otp_code=f"{i}00000").ok, i)
            self._age(minutes=61)             # clear the hourly rule each time
        self.assertEqual(LIMITED, self._send("custodian_code", otp_code="555555").outcome)

    def test_one_requester_gets_ten_codes_a_day_whoever_the_numbers_are(self):
        for number in self._numbers(10):
            self.assertTrue(self._send("custodian_code", number=number,
                                       otp_code="111111", requested_by=self.user).ok)
        over = self._send("phone_code", number="09179999999", otp_code="111111",
                          requested_by=self.user)
        self.assertEqual(LIMITED, over.outcome)
        # Somebody else is not held to this one's count, and a code sent with no
        # requester at all is held by the number rules only.
        other = User.objects.create_user(
            email="o@t.ph", username="o", password="x", role=self.user.role)
        self.assertTrue(self._send("phone_code", number="09179999999",
                                   otp_code="111111", requested_by=other).ok)
        self.assertTrue(self._send("phone_code", number="09178888888",
                                   otp_code="111111").ok)

    # -- the escape hatch ----------------------------------------------------

    @override_settings(SMS_PER_NUMBER_DAILY=1)
    def test_enforce_limits_false_goes_past_every_limit_and_is_still_recorded(self):
        self._send("temporary_password")
        self.assertEqual(LIMITED, self._send("temporary_password").outcome)
        again = self._send("temporary_password", enforce_limits=False)
        self.assertTrue(again.ok)
        self.assertEqual(2, SmsSend.objects.filter(outcome="sent").count())

    def test_a_limit_that_cannot_be_checked_does_not_stop_the_text(self):
        with patch("accounts.sms._limit_reached", side_effect=RuntimeError("db")):
            self.assertTrue(self._send().ok)
