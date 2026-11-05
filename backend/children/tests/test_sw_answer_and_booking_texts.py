"""The social worker is texted in two cases that used to leave them untold
(owner's test on the demo, 28 Oct 2026):

* a psychologist accepts a child that has NO case referral on file - nothing can
  be booked, so nothing is waited for and the acceptance is told at once;
* a session is booked for the child's social worker's record AFTER the
  acceptance was already told (Book later, a referral filed afterwards, the
  calendar) - `session_booked_sw`.

Nothing in the SMS chain is patched: the real endpoints, `queue_sms`,
`send_sms` and the `SmsSend` row, with SMS_PROVIDER=console. Only the thread
`queue_sms` starts is run inline (a thread cannot see a test's transaction),
and the commit callbacks are executed. The clock is pinned by the fixture.
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings

from accounts.email_notifications import mail_overdue_answers
from accounts.models import SmsSend
from children.models import AssignmentRequest
from children.tests.payloads import planted_text
from children.tests.test_answer_email import AnswerMailBase
from clinical.models import CaseReferral
from scheduling.models import Appointment
from scheduling.tests.test_api import give_referral

NUMBER = "09171234567"


class _Inline:
    """Stands in for threading.Thread inside accounts.sms only."""

    def __init__(self, target=None, args=(), kwargs=None, **_):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


class RealChainBase(AnswerMailBase):
    def setUp(self):
        super().setUp()
        self.sw.phone, self.sw.phone_verified = NUMBER, True
        self.sw.save()
        for patcher in (
                override_settings(SMS_PROVIDER="console"),
                patch("accounts.sms.threading", SimpleNamespace(Thread=_Inline)),
                # The sender closes its thread's connection; inline, that is the
                # test's own.
                patch("accounts.sms.connection.close")):
            patcher.enable() if hasattr(patcher, "enable") else patcher.start()
            self.addCleanup(patcher.disable if hasattr(patcher, "disable") else patcher.stop)
        SmsSend.objects.all().delete()
        self.log_cm = self.assertLogs("accounts.sms", "INFO")
        self.captured = self.log_cm.__enter__()
        self.addCleanup(self._close_logs)

    def _close_logs(self):
        try:
            self.log_cm.__exit__(None, None, None)
        except AssertionError:
            pass  # nothing was logged

    def kinds(self):
        return list(SmsSend.objects.order_by("pk").values_list("kind", flat=True))

    def text_lines(self):
        return [r for r in self.captured.output if "SMS (console)" in r]

    def no_referral(self):
        CaseReferral.objects.filter(child=self.child).delete()

    def file_referral(self):
        give_referral(self.child, self.sw)

    def accept_now(self, req, user=None):
        with self.outbox() as sent:
            res = self._as(user or self.psy).post(
                f"/api/assignment-requests/{req.pk}/accept/")
        self.assertEqual(200, res.status_code, res.data)
        return sent

    def booking(self, by, **kw):
        with self.outbox():
            res = self.book(by, **kw)
        self.assertEqual(201, res.status_code, res.data)
        return Appointment.objects.get(pk=res.data["id"])


class NoReferralTest(RealChainBase):
    def test_accept_texts_at_once_and_nothing_follows_until_a_booking(self):
        self.no_referral()
        req = self.ask()
        sent = self.accept_now(req)
        self.assertEqual(["case_accepted"], self.kinds())
        self.assertEqual(["case_accepted"], [k for k, _ in sent])
        req.refresh_from_db()
        self.assertFalse(self.owed(req))
        # The dialog's call, then the sweep: both find it taken.
        with self.outbox() as later:
            self.assertEqual({"mailed": False}, self.mail_answer(req).data)
        self.assertEqual([], later)
        self.assertEqual(["case_accepted"], self.kinds())
        self.grow_old(req, 11)
        cache.clear()
        with self.outbox():
            mail_overdue_answers()
        self.assertEqual(["case_accepted"], self.kinds())
        # The social worker files the referral; the psychologist books.
        self.file_referral()
        self.booking(self.psy)
        self.assertEqual(["case_accepted", "session_booked_sw"], self.kinds())
        self.assertEqual(["sent", "sent"],
                         list(SmsSend.objects.order_by("pk").values_list("outcome", flat=True)))

    def test_with_a_referral_acceptance_is_still_owed(self):
        req = self.ask()
        self.accept_now(req)
        self.assertTrue(self.owed(req))
        self.assertEqual([], self.kinds())


class SessionBookedTest(RealChainBase):
    def test_booking_inside_the_dialog_is_one_combined_text_and_no_booked_text(self):
        req = self.accepted()
        self.booking(self.psy)
        self.assertEqual(["case_accepted_booked"], self.kinds())
        with self.outbox():
            self.mail_answer(req)
        self.assertEqual(["case_accepted_booked"], self.kinds())

    def test_book_later_then_calendar_booking_texts_the_booking(self):
        req = self.accepted()
        with self.outbox():
            self.assertEqual({"mailed": True}, self.mail_answer(req).data)
        self.assertEqual(["case_accepted"], self.kinds())
        self.booking(self.psy)
        self.assertEqual(["case_accepted", "session_booked_sw"], self.kinds())
        line, = [l for l in self.text_lines() if "session_booked_sw" in l]
        self.assertIn(
            f"NACC SYS: a session for case {self.ref} is booked for Wed 21 Oct, "
            "9:30 AM. Sign in to see the schedule.", line)
        row = SmsSend.objects.get(kind="session_booked_sw")
        self.assertEqual(self.psy, row.requested_by)

    def test_isa_booking_also_texts_the_social_worker(self):
        req = self.accepted()
        with self.outbox():
            self.mail_answer(req)
        self.booking(self.admin)
        self.assertEqual(["case_accepted", "session_booked_sw"], self.kinds())

    def test_the_social_worker_booking_themself_texts_nothing(self):
        req = self.accepted()
        with self.outbox():
            self.mail_answer(req)
        self.booking(self.sw)
        self.assertEqual(["case_accepted"], self.kinds())

    def test_an_unverified_number_gets_nothing(self):
        self.sw.phone_verified = False
        self.sw.save()
        req = self.accepted()
        with self.outbox():
            self.mail_answer(req)
        self.booking(self.psy)
        self.assertEqual([], self.kinds())

    def test_a_moved_or_cancelled_session_is_not_texted(self):
        req = self.accepted()
        with self.outbox():
            self.mail_answer(req)
        appt = self.booking(self.psy)
        with self.outbox():
            self.change(self.psy, appt, start=self.later())
        self.assertEqual(["case_accepted", "session_booked_sw"], self.kinds())

    def test_no_text_names_the_child(self):
        tokens = list(planted_text().values()) + [
            self.child.first_name, self.child.last_name, self.child.fullname]
        for column, value in planted_text().items():
            setattr(self.child, column, value)
        self.child.save()
        self.no_referral()
        req = self.ask()
        self.accept_now(req)
        self.file_referral()
        self.booking(self.psy)
        lines = self.text_lines()
        self.assertEqual(2, len(lines))
        for line in lines:
            for token in tokens:
                self.assertNotIn(token, line)
            self.assertIn(self.ref, line)
