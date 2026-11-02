"""Recording that the custodian agreed texts their number once (owner, 2 Nov
2026), replacing the one-time code.

Nothing in the SMS chain is patched: the real endpoint, `queue_sms`,
`send_sms` and the `SmsSend` row, with SMS_PROVIDER=console. Only the thread
`queue_sms` starts is run inline (a thread cannot see a test's transaction),
and the commit callbacks are executed, as in test_sw_answer_and_booking_texts.
The clock is pinned: the limits count from it.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from accounts import sms_catalogue
from accounts.models import SmsSend
from accounts.sms import fits_one_segment
from children import custodian
from children.models import Child
from children.tests.payloads import complete
from children.tests.test_custodian import NUMBER, CustodianBase
from scheduling.models import Appointment
from scheduling.tests.test_api import give_referral

EXPECTED = ("NACC RACCO I: This number will now get appointment texts for the "
            "child in your care: date, time and place only. Not you? Tell the "
            "RACCO I office.")


class _Inline:
    def __init__(self, target=None, args=(), kwargs=None, **_):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class ConsentTextBase(CustodianBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.now = timezone.make_aware(datetime(2026, 11, 2, 12, 0))
        for patcher in (
                override_settings(SMS_PROVIDER="console"),
                patch("django.utils.timezone.now", side_effect=lambda: self.now),
                patch("accounts.sms.threading", SimpleNamespace(Thread=_Inline)),
                patch("accounts.sms.connection.close")):
            patcher.enable() if hasattr(patcher, "enable") else patcher.start()
            self.addCleanup(patcher.disable if hasattr(patcher, "disable") else patcher.stop)
        SmsSend.objects.all().delete()
        self.logs = self.assertLogs("accounts.sms", "INFO")
        self.captured = self.logs.__enter__()
        self.addCleanup(self._close_logs)

    def _close_logs(self):
        try:
            self.logs.__exit__(None, None, None)
        except AssertionError:
            pass  # nothing was logged

    def kinds(self):
        return list(SmsSend.objects.order_by("pk").values_list("kind", flat=True))

    def lines(self):
        return [r for r in self.captured.output if "SMS (console)" in r]

    def save(self, user=None, **fields):
        with self.captureOnCommitCallbacks(execute=True):
            res = self._patch(user=user, **fields)
        self.assertEqual(200, res.status_code, res.data)
        return res

    def agree(self, number="0917 123 4567", **extra):
        return self.save(custodian_contact=number, custodian_sms_consent=True, **extra)


class TheRuleTest(ConsentTextBase):
    def test_a_number_and_consent_turn_texts_on_with_nothing_confirmed(self):
        self.agree()
        self.child.refresh_from_db()
        self.assertIsNone(self.child.custodian_contact_verified_at)
        self.assertTrue(custodian.texts_allowed(self.child))

    def test_appointment_texts_go_to_a_consented_number_with_no_verified_at(self):
        self.agree()
        give_referral(self.child, self.sw)
        SmsSend.objects.all().delete()
        start = timezone.localtime() + timedelta(days=3)
        with self.captureOnCommitCallbacks(execute=True):
            res = self._as(self.psy).post("/api/appointments/", {
                "child": self.child.id, "psychologist": self.psy.id,
                "start": start.replace(hour=10, minute=0, second=0, microsecond=0).isoformat(),
                "duration_minutes": 60, "purpose": "session"}, format="json")
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["custodian_booked"], self.kinds())
        self.assertTrue(Appointment.objects.exists())


class TheConfirmationTextTest(ConsentTextBase):
    def test_the_wording_is_one_plain_segment_and_names_nobody(self):
        text = sms_catalogue.render("custodian_consent")
        self.assertEqual(EXPECTED, text)
        self.assertTrue(fits_one_segment(text))
        self.assertFalse(text.upper().startswith("TEST"))
        entry = sms_catalogue.get("custodian_consent")
        self.assertEqual((sms_catalogue.NORMAL, sms_catalogue.CUSTODIAN),
                         (entry.route, entry.recipient))

    def test_it_is_sent_once_when_a_record_is_created_with_consent(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = self._as(self.sw).post("/api/children/", complete(
                custodian_contact="0917 123 4567", custodian_sms_consent=True),
                format="json")
        self.assertEqual(201, res.status_code, res.data)
        self.assertTrue(res.data["custodian_consent_text"])
        self.assertEqual(["custodian_consent"], self.kinds())
        row = SmsSend.objects.get()
        self.assertEqual("normal", row.route)
        self.assertEqual(1, len(self.lines()))
        self.assertIn(EXPECTED, self.lines()[0])

    def test_a_new_record_without_consent_sends_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = self._as(self.sw).post("/api/children/", complete(
                custodian_contact="0917 123 4567"), format="json")
        self.assertEqual(201, res.status_code, res.data)
        self.assertFalse(res.data["custodian_consent_text"])
        self.assertEqual([], self.kinds())

    def test_ticking_consent_on_an_existing_record_sends_it_and_says_so(self):
        res = self.agree()
        self.assertTrue(res.data["custodian_consent_text"])
        self.assertEqual(["custodian_consent"], self.kinds())

    def test_an_unrelated_edit_of_a_consented_record_sends_nothing(self):
        self.agree()
        res = self.save(medical_notes="Asthma.", custodian_contact="0917 123 4567",
                        custodian_sms_consent=True, custodian_name=self.child.custodian_name)
        self.assertFalse(res.data["custodian_consent_text"])
        self.assertEqual(["custodian_consent"], self.kinds())

    def test_a_new_number_with_consent_ticked_again_sends_to_the_new_number(self):
        self.agree()
        res = self.agree("0918 765 4321")
        self.assertTrue(res.data["custodian_consent_text"])
        self.assertEqual(["custodian_consent", "custodian_consent"], self.kinds())
        self.assertEqual(2, len(self.lines()))
        self.assertIn("+639187654321", self.lines()[1])

    def test_a_new_custodian_with_consent_given_again_sends(self):
        self.agree()
        self.now += timedelta(hours=25)      # the same number: one a day, see below
        res = self.save(custodian_name="Mercedes Soriano (foster parent)",
                        custodian_sms_consent=True)
        self.assertTrue(res.data["custodian_consent_text"])
        self.assertEqual(2, len(self.kinds()))

    def test_a_new_custodian_without_consent_given_again_sends_nothing(self):
        self.agree()
        res = self.save(custodian_name="Mercedes Soriano (foster parent)")
        self.assertFalse(res.data["custodian_consent_text"])
        self.assertEqual(1, len(self.kinds()))

    def test_withdrawing_consent_sends_nothing(self):
        self.agree()
        res = self.save(custodian_sms_consent=False)
        self.assertFalse(res.data["custodian_consent_text"])
        self.assertEqual(["custodian_consent"], self.kinds())

    def test_a_psychologists_save_sends_nothing(self):
        self.agree()
        res = self.save(user=self.psy, medical_notes="Asthma.",
                        custodian_name=self.child.custodian_name)
        self.assertFalse(res.data["custodian_consent_text"])
        self.assertEqual(1, len(self.kinds()))

    def test_a_closed_case_is_not_a_fresh_agreement(self):
        # The API does not open a closed case for editing (404), so the rule
        # itself is asked: consent on a closed record is no agreement to act on.
        Child.objects.filter(pk=self.child.pk).update(
            status=Child.INACTIVE, custodian_contact=NUMBER, custodian_sms_consent=True)
        self.child.refresh_from_db()
        self.assertFalse(custodian.is_fresh_agreement(("", "", False), self.child))
        from accounts import sms_notifications
        self.assertFalse(sms_notifications.notify_custodian_consent(self.child, self.sw))
        self.assertEqual([], self.kinds())

    def test_the_same_number_is_not_texted_twice_in_a_day(self):
        self.agree()
        self.save(custodian_sms_consent=False)
        res = self.save(custodian_sms_consent=True)
        self.assertFalse(res.data["custodian_consent_text"], "held by the one-a-day rule")
        self.assertEqual(["custodian_consent"], self.kinds())
        self.save(custodian_sms_consent=False)
        self.now += timedelta(hours=25)
        res = self.save(custodian_sms_consent=True)
        self.assertTrue(res.data["custodian_consent_text"])
        self.assertEqual(2, len(self.kinds()))

    def test_the_text_never_contains_the_childs_name(self):
        self.agree()
        text = "\n".join(self.lines())
        for part in ("Ana", "Cruz", self.child.fullname, self.child.custodian_name):
            self.assertNotIn(part, text.replace("NACC RACCO I", ""))

    def test_the_number_is_the_custodians(self):
        self.agree()
        self.assertIn(NUMBER, self.lines()[0])
