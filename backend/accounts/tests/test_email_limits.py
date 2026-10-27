"""The limit on the emails one account can cause (owner's request, 25 Oct 2026;
design: docs/superpowers/specs/2026-10-21-email-notifications-design.md, "Round
four"; accounts/email_notifications.py `notify`).

Picking one psychologist and then another on a record, back and forth, mailed
"asked" and "withdrawn" to both on every save, and rescheduling a session over
and over mailed an invite each time. The budget belongs to the ACTOR - the
person whose action caused the email - in three rolling windows: to one
colleague in an hour, to everyone in an hour, to everyone in 24 hours. Past a
limit the email is HELD, the colleague is told that more updates are waiting
(once an hour, three times in 24 hours at most) and the ISA is told once an hour
that the account reached it.

The tests turn the limits on with small numbers (3 to one colleague, 5 in an
hour, 8 in a day) and keep the clock pinned. `SettingsTest` runs the real
settings file to prove the defaults, because the suite itself runs with every
limit off, as the export throttle is.
"""
import ast
import importlib.util
import os
import sys
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from accounts import email_notifications
from accounts.email_notifications import (
    OVERFLOW, OVERFLOW_PER_DAY, SEND_RECORD_KEEP, case_answered, case_request, case_taken_over,
    mail_overdue_answers, maybe_sweep, notify, prune_send_records,
    records_transferred, request_withdrawn)
from accounts.models import EmailOptOut, EmailSendRecord, Role
from accounts.tests.test_email_notifications import NOW, MailBase, User
from activity.models import ActivityLog
from children.models import AssignmentRequest, Child
from children.tests.payloads import complete, planted_text
from config import settings as project_settings
from scheduling.reminders import send_session_reminders
from scheduling.tests import test_session_mail as session_mail

NOTICE = "More updates are waiting for you in NACC SYS"
LIMITS = dict(EMAIL_LIMIT_PER_RECIPIENT_HOUR=3, EMAIL_LIMIT_PER_HOUR=5, EMAIL_LIMIT_PER_DAY=8)
BACKEND = Path(__file__).resolve().parents[2]


@contextmanager
def clock_at(moment):
    """The pinned clock, moved. Nested inside the fixture's own pin."""
    with patch("django.utils.timezone.now", return_value=moment):
        yield


class LimitBase(MailBase):
    """The mail fixture with the limits on. `withdrawn()` is the cheapest way to
    cause one email: the withdrawal of a request, to one psychologist, from one
    actor."""

    def withdrawn(self, to=None, by=None, times=1):
        for _ in range(times):
            request_withdrawn(to or self.psy, self.child, by=by or self.sw)

    def records(self, actor=None, recipient=None, kind=None):
        rows = EmailSendRecord.objects.filter(actor=actor or self.sw)
        if recipient is not None:
            rows = rows.filter(recipient=recipient)
        if kind is not None:
            rows = rows.filter(kind=kind)
        return rows

    def limit_lines(self, actor=None):
        return ActivityLog.objects.filter(
            kind=ActivityLog.KIND_EMAIL_LIMIT, **({"actor": actor} if actor else {}))

    def backdated(self, count, ago, recipient=None, actor=None, kind="request_withdrawn"):
        for _ in range(count):
            EmailSendRecord.objects.create(
                actor=actor or self.sw, recipient=recipient or self.psy, kind=kind,
                created_at=NOW - ago)


# --------------------------------------------------------------------------
# The settings
# --------------------------------------------------------------------------

class SettingsTest(SimpleTestCase):
    """The settings file is run again, in a module of its own, as the server
    runs it: the suite's copy has every limit off."""

    NAMES = ("EMAIL_LIMIT_PER_RECIPIENT_HOUR", "EMAIL_LIMIT_PER_HOUR", "EMAIL_LIMIT_PER_DAY")

    def load(self, testing=False, **env):
        # A blank value stands in for "not set" and also keeps a developer's
        # backend/.env from supplying one: load_dotenv never overrides a name
        # that is already in the environment.
        wanted = {name: "" for name in self.NAMES} | env
        argv = ["manage.py", "test"] if testing else ["manage.py", "runserver"]
        with patch.dict(os.environ, wanted), patch.object(sys, "argv", argv):
            spec = importlib.util.spec_from_file_location(
                "settings_probe", Path(project_settings.__file__))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        return tuple(getattr(module, name) for name in self.NAMES)

    def test_the_defaults_are_twenty_to_a_colleague_sixty_an_hour_and_150_a_day(self):
        self.assertEqual((20, 60, 150), self.load())

    def test_the_documented_variables_set_each_one(self):
        self.assertEqual((7, 11, 13), self.load(
            EMAIL_LIMIT_PER_RECIPIENT_HOUR="7", EMAIL_LIMIT_PER_HOUR="11",
            EMAIL_LIMIT_PER_DAY="13"))

    def test_zero_is_kept_and_means_no_limit_of_that_kind(self):
        self.assertEqual((0, 60, 150), self.load(EMAIL_LIMIT_PER_RECIPIENT_HOUR="0"))

    def test_a_value_that_is_not_a_whole_number_keeps_the_default_and_says_so(self):
        for bad in ("many", "-5", "2.5"):
            with self.subTest(bad), self.assertLogs("config.settings", "WARNING") as logged:
                self.assertEqual((20, 60, 150), self.load(EMAIL_LIMIT_PER_RECIPIENT_HOUR=bad))
            self.assertIn("EMAIL_LIMIT_PER_RECIPIENT_HOUR is ignored", logged.output[0])

    def test_every_limit_is_off_under_the_test_runner(self):
        self.assertEqual((None, None, None), self.load(testing=True))
        # ... and this run really is one, so the rest of the suite is unlimited.
        self.assertEqual(
            (None, None, None),
            tuple(getattr(project_settings, n, None) for n in self.NAMES))


# --------------------------------------------------------------------------
# The limits
# --------------------------------------------------------------------------

@override_settings(**LIMITS)
class LimitTest(LimitBase):
    def test_the_next_email_to_one_colleague_is_held_and_they_are_told_once(self):
        with self.outbox() as sent:
            self.withdrawn(times=3)
            self.assertEqual(["request_withdrawn"] * 3, self.kinds_to(sent, self.psy))
            self.withdrawn(times=4)                    # the 4th is held, so are the rest
        self.assertEqual(["request_withdrawn"] * 3 + [OVERFLOW], self.kinds_to(sent, self.psy))
        self.assertEqual(3, self.records(recipient=self.psy, kind="request_withdrawn").count())
        self.assertEqual(1, self.records(recipient=self.psy, kind=OVERFLOW).count())

    def test_the_notice_says_who_and_nothing_else(self):
        with self.outbox() as sent:
            self.withdrawn(times=4)
        payload = self.to(sent, self.psy)[-1][1]
        self.assertEqual(NOTICE, payload["subject"])
        self.assertEqual([{"email": "psy@t.ph", "name": "Marivic Bulan"}], payload["to"])
        text = payload["textContent"]
        self.assertIn("Editha Pascua has made more changes that affect you lately than "
                      "NACC SYS sends emails for. Sign in to see them: your Dashboard "
                      "and the notifications list show everything. Emails about their "
                      "changes start again once they have slowed down: within the "
                      "hour, or within a day at the longest.", text)
        self.assertIn(f"Open NACC SYS: https://nacc.example.test/?for={self.psy.pk}", text)
        self.assertNotIn("attachment", payload)
        self.assertNotIn(self._ref(self.child), json_of(payload))

    def test_the_notice_names_a_nameless_actor_as_a_colleague(self):
        User.objects.filter(pk=self.sw.pk).update(first_name="", last_name="")
        self.sw.refresh_from_db()
        with self.outbox() as sent:
            self.withdrawn(times=4)
        text = self.to(sent, self.psy)[-1][1]["textContent"]
        self.assertIn("A colleague has made more changes", text)
        self.assertNotIn("sw@t.ph", text, "the address is not a name")

    def test_a_second_colleague_is_still_mailed_while_the_first_is_held(self):
        with self.outbox() as sent:
            self.withdrawn(self.psy, times=4)
            self.withdrawn(self.psy2, times=2)
        self.assertEqual(["request_withdrawn"] * 3 + [OVERFLOW], self.kinds_to(sent, self.psy))
        self.assertEqual(["request_withdrawn"] * 2, self.kinds_to(sent, self.psy2))

    def test_the_hourly_limit_holds_across_colleagues(self):
        """2 + 2 + 1 = 5, and no colleague is at their own limit of 3."""
        with self.outbox() as sent:
            self.withdrawn(self.psy, times=2)
            self.withdrawn(self.psy2, times=2)
            records_transferred(self.other_sw, self.sw, 2, by=self.sw)
            self.assertEqual(5, len([k for k, _ in sent if k != OVERFLOW]))
            self.withdrawn(self.psy)                   # 2 to psy so far: held by the hour
            records_transferred(self.other_sw, self.sw, 2, by=self.sw)
        self.assertEqual(["request_withdrawn"] * 2 + [OVERFLOW], self.kinds_to(sent, self.psy))
        self.assertEqual(["request_withdrawn"] * 2, self.kinds_to(sent, self.psy2))
        self.assertEqual(["records_transferred", OVERFLOW], self.kinds_to(sent, self.other_sw))
        self.assertEqual(5, self.records().exclude(kind=OVERFLOW).count())

    def test_the_daily_limit_counts_what_was_sent_hours_ago(self):
        """Seven earlier in the day, none in the last hour: the 8th flows, the
        9th is held by the day. Rows a day and a minute old are not counted."""
        self.backdated(7, timedelta(hours=5), recipient=self.psy2)
        self.backdated(6, timedelta(hours=24, minutes=1), recipient=self.psy2)
        with self.outbox() as sent:
            self.withdrawn(self.psy)
            self.assertEqual(["request_withdrawn"], self.kinds_to(sent, self.psy))
            self.withdrawn(self.psy)
        self.assertEqual(["request_withdrawn", OVERFLOW], self.kinds_to(sent, self.psy))

    def test_a_row_23_hours_old_still_counts_toward_the_day(self):
        """The day limit is 8. Eight emails 23 hours ago and none since: the
        next is held. A window of 12 hours would let it through, and so would
        every test that only tries 5 and 25 hours."""
        self.backdated(8, timedelta(hours=23), recipient=self.psy2)
        with self.outbox() as sent:
            self.withdrawn(self.psy)
        self.assertEqual([OVERFLOW], self.kinds_to(sent, self.psy))

    def test_a_second_actor_held_toward_the_same_colleague_is_noticed_too(self):
        """The notice is once per ACTOR and colleague: the first account's
        notice does not stand in for a second account's, who is also causing
        updates the colleague is not getting emails about."""
        with self.outbox() as sent:
            self.withdrawn(self.psy, times=4)                        # the social worker
            self.withdrawn(self.psy, by=self.other_sw, times=4)      # and another
        notices = [p["textContent"] for k, p in self.to(sent, self.psy) if k == OVERFLOW]
        self.assertEqual(2, len(notices))
        self.assertTrue(any("Editha" in n for n in notices))
        self.assertTrue(any("Rosa" in n for n in notices))

    def test_the_window_rolls_an_hour_later_and_a_new_notice_can_go(self):
        with self.outbox() as sent:
            self.withdrawn(times=4)
            self.assertEqual(1, self.limit_lines().count())
            with clock_at(NOW + timedelta(minutes=59)):
                self.withdrawn()                         # still held, and not told again
            self.assertEqual(["request_withdrawn"] * 3 + [OVERFLOW], self.kinds_to(sent, self.psy))
            with clock_at(NOW + timedelta(hours=1, seconds=1)):
                self.withdrawn(times=3)                  # an hour on: flowing again
                self.withdrawn()                         # the new hour's 4th is held
        self.assertEqual(
            ["request_withdrawn"] * 3 + [OVERFLOW] + ["request_withdrawn"] * 3 + [OVERFLOW],
            self.kinds_to(sent, self.psy))
        self.assertEqual(2, self.limit_lines().count(), "and the ISA is told again")

    def test_an_email_exactly_an_hour_old_is_out_of_the_window(self):
        self.backdated(3, timedelta(hours=1))
        with self.outbox() as sent:
            self.withdrawn()
        self.assertEqual(["request_withdrawn"], self.kinds_to(sent, self.psy))
        EmailSendRecord.objects.all().delete()
        self.backdated(3, timedelta(hours=1) - timedelta(seconds=1))
        with self.outbox() as sent:
            self.withdrawn()
        self.assertEqual([OVERFLOW], self.kinds_to(sent, self.psy), "a second younger counts")

    def test_a_notice_is_not_sent_again_within_the_hour_however_long_it_is_held(self):
        with self.outbox() as sent:
            self.withdrawn(times=3)
            for minutes in (1, 20, 40, 59):
                with clock_at(NOW + timedelta(minutes=minutes)):
                    self.withdrawn()
        self.assertEqual(1, self.kinds_to(sent, self.psy).count(OVERFLOW))

    def test_no_notice_when_the_colleague_switched_off_the_held_kind(self):
        """The recipient's own switch comes first: an email they do not want is
        not held, so there is nothing to tell them is waiting, and the ISA has
        nothing to hear about either."""
        with self.outbox() as sent:
            self.withdrawn(times=3)
            EmailOptOut.objects.create(user=self.psy, kind="request_withdrawn")
            self.withdrawn(times=2)
        self.assertEqual(["request_withdrawn"] * 3, self.kinds_to(sent, self.psy))
        self.assertEqual(0, self.limit_lines().count())

    def test_the_same_actor_is_held_for_a_kind_that_is_switched_on(self):
        """The contrast: with the switch on, the same position holds and tells."""
        with self.outbox() as sent:
            self.withdrawn(times=3)
            EmailOptOut.objects.create(user=self.psy, kind="case_request")
            case_request(self.child, self.psy, by=self.sw)       # switched off: nothing
            EmailOptOut.objects.filter(user=self.psy).delete()
            case_request(self.child, self.psy, by=self.sw)       # on: held and told
        self.assertEqual(["request_withdrawn"] * 3 + [OVERFLOW], self.kinds_to(sent, self.psy))

    def test_the_notice_does_not_count_toward_the_limits(self):
        """psy is held and told; psy2 can still have 5 - 3 = 2 more. Had the
        notice counted, the hour would already be at 4 and psy2 would get 1."""
        with self.outbox() as sent:
            self.withdrawn(self.psy, times=4)
            self.assertEqual(1, self.kinds_to(sent, self.psy).count(OVERFLOW))
            self.withdrawn(self.psy2, times=2)
            self.withdrawn(self.psy2)
        self.assertEqual(["request_withdrawn"] * 2 + [OVERFLOW], self.kinds_to(sent, self.psy2))

    def test_an_actors_budget_is_their_own(self):
        with self.outbox() as sent:
            self.withdrawn(self.psy, times=4)                        # the social worker: held
            self.withdrawn(self.psy, by=self.other_sw, times=3)      # another worker: fine
            self.withdrawn(self.psy, by=self.admin)                  # the ISA: fine
        self.assertEqual(["request_withdrawn"] * 3 + [OVERFLOW] + ["request_withdrawn"] * 4,
                         self.kinds_to(sent, self.psy))
        self.assertEqual(3, self.records(actor=self.sw, kind="request_withdrawn").count())
        self.assertEqual(3, self.records(actor=self.other_sw).count())
        self.assertEqual(1, self.records(actor=self.admin).count())

    def test_no_actor_is_never_limited_and_leaves_no_row(self):
        with self.outbox() as sent:
            for _ in range(12):
                self.assertTrue(notify(
                    "request_withdrawn", self.psy, subject="s", heading="h", paragraphs=["p"]))
        self.assertEqual(12, len(sent))
        self.assertFalse(EmailSendRecord.objects.exists())
        self.assertFalse(self.limit_lines().exists())

    def test_an_actor_who_was_never_saved_is_not_counted_either(self):
        with self.outbox() as sent:
            for _ in range(5):
                notify("request_withdrawn", self.psy, subject="s", heading="h",
                       paragraphs=["p"], by=User())
        self.assertEqual(5, len(sent))
        self.assertFalse(EmailSendRecord.objects.exists())

    def test_an_email_that_was_never_going_to_be_sent_is_not_counted(self):
        """Switched off, in the wrong role, or an account that cannot sign in."""
        EmailOptOut.objects.create(user=self.psy, kind="request_withdrawn")
        with self.outbox() as sent:
            self.withdrawn(self.psy, times=6)                        # switched off
            self.withdrawn(self.sw, times=6)                         # not a psychologist
        self.assertEqual([], sent)
        self.assertFalse(EmailSendRecord.objects.exists())

    def test_a_failure_to_count_stops_the_email_not_the_save(self):
        with self.outbox() as sent, patch(
                "accounts.email_notifications.EmailSendRecord.objects.create",
                side_effect=RuntimeError("table is gone")):
            with self.assertLogs("accounts.email_notifications", "ERROR"):
                self.assertFalse(notify("request_withdrawn", self.psy, subject="s",
                                        heading="h", paragraphs=["p"], by=self.sw))
        self.assertEqual([], sent)


# --------------------------------------------------------------------------
# The notice has a budget of its own
# --------------------------------------------------------------------------

@override_settings(EMAIL_LIMIT_PER_RECIPIENT_HOUR=3, EMAIL_LIMIT_PER_HOUR=0,
                   EMAIL_LIMIT_PER_DAY=0)
class NoticeBudgetTest(LimitBase):
    """The notice is not counted by the limits, so on its own "once an hour" let
    an account that stayed held keep mailing every colleague it touched, every
    hour, for as long as it tried: 150 a day plus 24 notices to each of a dozen
    colleagues. A colleague hears of it `OVERFLOW_PER_DAY` times in 24 hours,
    no more, and the limits themselves are what keep the account quiet."""

    HOUR_AND_A_BIT = timedelta(hours=1, seconds=1)

    def keep_trying(self, colleagues, hours):
        """Each hour the window has rolled, three emails to each colleague flow
        and the fourth is held. Returns what was sent."""
        with self.outbox() as sent:
            for hour in range(hours):
                with clock_at(NOW + hour * self.HOUR_AND_A_BIT):
                    for colleague in colleagues:
                        self.withdrawn(colleague, times=4)
        return sent

    def test_a_colleague_held_every_hour_is_told_only_a_few_times_a_day(self):
        self.assertEqual(3, OVERFLOW_PER_DAY)
        sent = self.keep_trying([self.psy], hours=8)
        kinds = self.kinds_to(sent, self.psy)
        self.assertEqual(8 * 3, kinds.count("request_withdrawn"), "the limit itself is untouched")
        self.assertEqual(OVERFLOW_PER_DAY, kinds.count(OVERFLOW))
        self.assertEqual(OVERFLOW_PER_DAY, self.records(recipient=self.psy, kind=OVERFLOW).count())

    def test_the_notices_come_in_the_first_hours_not_spread_over_the_day(self):
        sent = self.keep_trying([self.psy], hours=6)
        positions = [n for n, k in enumerate(self.kinds_to(sent, self.psy)) if k == OVERFLOW]
        # Each hour sends 3 emails and then, in the first three hours, the notice.
        self.assertEqual([3, 7, 11], positions)

    def test_every_colleague_has_a_budget_of_their_own(self):
        sent = self.keep_trying([self.psy, self.psy2], hours=6)
        for colleague in (self.psy, self.psy2):
            self.assertEqual(OVERFLOW_PER_DAY, self.kinds_to(sent, colleague).count(OVERFLOW),
                             colleague.email)

    def test_the_day_after_the_first_notice_has_aged_out_one_more_can_go(self):
        self.keep_trying([self.psy], hours=6)                       # three notices used
        with clock_at(NOW + timedelta(hours=24, seconds=1)), self.outbox() as sent:
            self.withdrawn(self.psy, times=4)
        self.assertEqual(["request_withdrawn"] * 3 + [OVERFLOW], self.kinds_to(sent, self.psy))
        self.assertEqual(OVERFLOW_PER_DAY + 1, self.records(kind=OVERFLOW).count())
        with clock_at(NOW + timedelta(hours=24, minutes=5)), self.outbox() as more:
            self.withdrawn(self.psy, times=4)
        self.assertNotIn(OVERFLOW, self.kinds_to(more, self.psy), "and not twice in the hour")

    def test_the_notice_does_not_promise_the_hour(self):
        with self.outbox() as sent:
            self.withdrawn(times=4)
        text = self.to(sent, self.psy)[-1][1]["textContent"]
        self.assertNotIn("start again within the hour", text)
        self.assertIn("within a day at the longest", text)


# --------------------------------------------------------------------------
# Each limit on its own, and the settings that switch them off
# --------------------------------------------------------------------------

class EachLimitTest(LimitBase):
    def sent_with(self, **limits):
        """{address: how many emails got through} when one account keeps mailing
        two colleagues in turn, thirty times, with only `limits` switched on."""
        configured = dict(EMAIL_LIMIT_PER_RECIPIENT_HOUR=None, EMAIL_LIMIT_PER_HOUR=None,
                          EMAIL_LIMIT_PER_DAY=None) | limits
        with override_settings(**configured), self.outbox() as sent:
            for n in range(30):
                self.withdrawn((self.psy, self.psy2)[n % 2])
        return {user.email: self.kinds_to(sent, user).count("request_withdrawn")
                for user in (self.psy, self.psy2)}

    def test_only_the_colleague_limit(self):
        self.assertEqual({"psy@t.ph": 4, "psy2@t.ph": 4},
                         self.sent_with(EMAIL_LIMIT_PER_RECIPIENT_HOUR=4))

    def test_only_the_hourly_limit(self):
        self.assertEqual({"psy@t.ph": 3, "psy2@t.ph": 3},
                         self.sent_with(EMAIL_LIMIT_PER_HOUR=6))

    def test_only_the_daily_limit(self):
        self.assertEqual({"psy@t.ph": 4, "psy2@t.ph": 3},
                         self.sent_with(EMAIL_LIMIT_PER_DAY=7))

    def test_zero_and_none_mean_no_limit_of_that_kind(self):
        for off in (0, None):
            with self.subTest(off=off):
                EmailSendRecord.objects.all().delete()
                self.assertEqual({"psy@t.ph": 15, "psy2@t.ph": 15}, self.sent_with(
                    EMAIL_LIMIT_PER_RECIPIENT_HOUR=off, EMAIL_LIMIT_PER_HOUR=off,
                    EMAIL_LIMIT_PER_DAY=off))

    def test_one_limit_off_leaves_the_others_working(self):
        self.assertEqual({"psy@t.ph": 2, "psy2@t.ph": 2}, self.sent_with(
            EMAIL_LIMIT_PER_RECIPIENT_HOUR=0, EMAIL_LIMIT_PER_HOUR=4, EMAIL_LIMIT_PER_DAY=0))

    def test_rows_are_written_even_with_every_limit_off(self):
        """So a limit switched on later counts from a true picture; the rows
        age out after a day either way."""
        with override_settings(EMAIL_LIMIT_PER_RECIPIENT_HOUR=0, EMAIL_LIMIT_PER_HOUR=0,
                               EMAIL_LIMIT_PER_DAY=None), self.outbox():
            self.withdrawn(times=3)
        self.assertEqual(3, self.records().count())
        self.assertFalse(self.limit_lines().exists())


# --------------------------------------------------------------------------
# What a held email does not do
# --------------------------------------------------------------------------

@override_settings(**LIMITS)
class HeldEmailTest(LimitBase):
    def test_a_held_email_never_calls_the_attachment(self):
        attach = Mock(return_value=("invite.ics", "BEGIN:VCALENDAR"))
        with self.outbox() as sent:
            for _ in range(3):
                self.assertTrue(notify("my_calendar", self.psy, subject="s", heading="h",
                                       paragraphs=["p"], attachment=attach, by=self.sw))
            self.assertEqual(3, attach.call_count)
            self.assertFalse(notify("my_calendar", self.psy, subject="s", heading="h",
                                    paragraphs=["p"], attachment=attach, by=self.sw))
        self.assertEqual(3, attach.call_count, "the held one built no attachment")
        self.assertEqual(3, len([p for _k, p in self.to(sent, self.psy) if "attachment" in p]))

    def test_a_held_email_is_not_built_at_all(self):
        with self.outbox():
            self.withdrawn(times=3)
            with patch("accounts.email_notifications.render",
                       wraps=email_notifications.render) as render:
                self.withdrawn()
        # The only page rendered is the notice: the held email itself is not.
        self.assertEqual(1, render.call_count)
        self.assertEqual(NOTICE, render.call_args.args[0])

    def test_held_emails_leave_no_row_but_the_notice(self):
        with self.outbox():
            self.withdrawn(times=3)
            before = EmailSendRecord.objects.count()
            self.withdrawn(times=5)
        self.assertEqual(before + 1, EmailSendRecord.objects.count(), "just the notice")


@override_settings(EMAIL_LIMIT_PER_RECIPIENT_HOUR=3, EMAIL_LIMIT_PER_HOUR=50,
                   EMAIL_LIMIT_PER_DAY=100)
class HeldSessionTest(session_mail.SessionMailBase):
    """Through the real appointment endpoints: a social worker moves one session
    again and again. The psychologist gets an invite for the booking and the
    first two moves; the next ones change the calendar and mail nothing, and the
    invite's SEQUENCE stays at what was really sent."""

    def test_a_held_move_does_not_bump_the_invites_sequence(self):
        appointment, booking = self.booked(self.sw)
        starts = [self.later(0.5), self.later(1), self.later(1.5),
                  self.start - timedelta(minutes=30)]
        with self.outbox() as moves:
            codes = [self.change(self.sw, appointment, start=s).status_code for s in starts]
        self.assertEqual([200] * 4, codes, "the moves themselves were never refused")
        appointment.refresh_from_db()
        self.assertEqual(starts[-1], appointment.start, "the last, held move was saved")
        self.assertEqual(3, self.invite_row(appointment, self.psy).sequence,
                         "booked and moved twice; the other two were held")
        mails = self.to(booking + moves, self.psy)
        self.assertEqual(["my_calendar"] * 3 + [OVERFLOW], [k for k, _ in mails])
        self.assertEqual(3, len([p for _k, p in mails if "attachment" in p]))
        self.assertEqual(["1", "2", "3"], [
            session_mail.prop(session_mail.attachment(p), "SEQUENCE")
            for k, p in mails if k == "my_calendar"])
        # An hour later the number carries on from the last one really sent.
        with clock_at(session_mail.NOW + timedelta(hours=1, seconds=1)), self.outbox() as later:
            self.assertEqual(200, self.change(self.sw, appointment,
                                              start=self.later(1)).status_code)
        self.assertEqual(4, self.invite_row(appointment, self.psy).sequence)
        self.assertEqual("4", session_mail.prop(session_mail.attachment(
            self.one(later, self.psy)), "SEQUENCE"))

    def test_a_first_session_booked_while_held_deals_with_the_acceptance(self):
        """The psychologist who accepted books the first session while they are
        at their limit toward this social worker. One email would have carried
        both; it is held, the social worker gets the notice, and the booking's
        own email is not tried on top (the same limit would hold it). The
        acceptance is not owed any more, so no sweep sends it later."""
        req = AssignmentRequest.objects.create(
            child=self.child, psychologist=self.psy, requested_by=self.sw,
            status=AssignmentRequest.ACCEPTED, decided_at=session_mail.NOW,
            decided_by=self.psy)
        for _ in range(3):
            EmailSendRecord.objects.create(
                actor=self.psy, recipient=self.sw, kind="child_sessions",
                created_at=session_mail.NOW)
        with self.outbox() as sent:
            res = self.book(self.psy)
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual([OVERFLOW], self.kinds_to(sent, self.sw))
        req.refresh_from_db()
        self.assertIsNotNone(req.answer_mailed_at)
        with clock_at(session_mail.NOW + timedelta(hours=2)), self.outbox() as later:
            self.assertEqual(0, mail_overdue_answers())
        self.assertEqual([], later)

    def test_a_booking_is_counted_against_the_one_who_made_it(self):
        _appointment, sent = self.booked(self.admin)
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        rows = EmailSendRecord.objects.order_by("kind")
        self.assertEqual([(self.admin.pk, "child_sessions", self.sw.pk),
                          (self.admin.pk, "my_calendar", self.psy.pk)],
                         [(r.actor_id, r.kind, r.recipient_id) for r in rows])

    def test_a_psychologists_own_calendar_update_is_their_own_email(self):
        """They are the actor and the recipient at once, and it counts against
        their budget like any other."""
        appointment, _sent = self.booked(self.sw)
        EmailSendRecord.objects.all().delete()
        with self.outbox() as sent:
            self.assertEqual(200, self.change(self.psy, appointment,
                                              start=self.later(1)).status_code)
        self.assertEqual(["my_calendar"], self.kinds_to(sent, self.psy))
        (mine,) = EmailSendRecord.objects.filter(recipient=self.psy)
        self.assertEqual((self.psy.pk, self.psy.pk), (mine.actor_id, mine.recipient_id))


# --------------------------------------------------------------------------
# Real endpoints
# --------------------------------------------------------------------------

class PingPongTest(LimitBase):
    """A social worker edits a record's psychologist back and forth between two
    psychologists - "asked", "withdrawn", "asked" - until the limit."""

    def ping_pong(self, rounds=8):
        with self.outbox() as sent:
            for round_ in range(rounds):
                for psychologist in (self.psy, self.psy2):
                    res = self._as(self.sw).patch(
                        f"/api/children/{self.child.id}/",
                        {"psychologist": psychologist.id}, format="json")
                    self.assertEqual(200, res.status_code, (round_, res.data))
        return sent

    def check(self, sent, limit):
        for psychologist in (self.psy, self.psy2):
            kinds = self.kinds_to(sent, psychologist)
            self.assertLessEqual(len([k for k in kinds if k != OVERFLOW]), limit, kinds)
            self.assertEqual(1, kinds.count(OVERFLOW), kinds)
            self.assertEqual(OVERFLOW, kinds[-1], "the notice is the last word")
        # The record itself followed every edit: the last one asked is waiting.
        pending = AssignmentRequest.objects.get(
            child=self.child, status=AssignmentRequest.PENDING)
        self.assertEqual(self.psy2.pk, pending.psychologist_id)
        # The ISA hears of the social worker once.
        (line,) = self.limit_lines()
        self.assertEqual(self.sw.pk, line.actor_id)

    @override_settings(EMAIL_LIMIT_PER_RECIPIENT_HOUR=3, EMAIL_LIMIT_PER_HOUR=50,
                       EMAIL_LIMIT_PER_DAY=100)
    def test_each_psychologist_gets_the_limit_and_one_notice_and_the_saves_stand(self):
        sent = self.ping_pong()
        self.check(sent, 3)
        for psychologist in (self.psy, self.psy2):
            self.assertEqual(
                ["case_request", "request_withdrawn", "case_request", OVERFLOW],
                self.kinds_to(sent, psychologist),
                "asked, withdrawn, asked - and then the notice")

    @override_settings(**LIMITS)
    def test_the_hourly_limit_holds_the_same_ping_pong_sooner(self):
        sent = self.ping_pong()
        self.check(sent, 3)
        self.assertEqual(5, len([k for k, _ in sent if k != OVERFLOW]))


class OtherDomainFunctionsTest(LimitBase):
    """Each of the five domain functions, and the sweep, passes the actor."""

    def actors(self, function, *args, **kwargs):
        with patch("accounts.email_notifications.notify") as notify_mock:
            function(*args, **kwargs)
        self.assertEqual(1, notify_mock.call_count, function.__name__)
        return notify_mock.call_args.kwargs["by"]

    def test_the_five_domain_functions_pass_the_actor(self):
        self.assertIs(self.sw, self.actors(case_request, self.child, self.psy, by=self.sw))
        self.assertIs(self.sw, self.actors(request_withdrawn, self.psy, self.child, by=self.sw))
        self.assertIs(self.admin, self.actors(
            records_transferred, self.other_sw, self.sw, 2, by=self.admin))
        self.assertIs(self.other_sw, self.actors(
            case_taken_over, self.child, self.sw, by=self.other_sw))

    def test_an_answer_passes_the_one_who_answered(self):
        req = self._ask(self.psy)
        self.assertEqual(200, self._answer(req, "decline", reason="Full").status_code)
        req.refresh_from_db()
        self.assertIs(self.psy, self.actors(case_answered, req, by=self.psy))

    def test_the_sweep_counts_an_acceptance_against_the_psychologist_who_accepted(self):
        req = self._ask(self.psy)
        self.assertEqual(200, self._answer(req, "accept").status_code)
        with override_settings(**LIMITS), self.outbox() as sent, clock_at(
                NOW + email_notifications.ANSWER_MAIL_GRACE + timedelta(minutes=1)):
            self.assertEqual(1, mail_overdue_answers())
        self.assertEqual(["case_accepted"], self.kinds_to(sent, self.sw))
        (row,) = EmailSendRecord.objects.filter(kind="case_accepted")
        self.assertEqual((self.psy.pk, self.sw.pk), (row.actor_id, row.recipient_id))

    def test_an_acceptance_held_by_the_limit_is_dealt_with_and_not_sent_later(self):
        """The psychologist is already at their limit toward this social worker.
        The sweep claims the acceptance, the email is held and the social worker
        is told that more updates are waiting; the claim stands, so a sweep
        after the window has reopened does not send it. A held email is held
        for every kind (a decline has no claim to give back), and a retry would
        be held again each minute and crowd newer acceptances out of the ten the
        sweep takes."""
        req = self._ask(self.psy)
        self.assertEqual(200, self._answer(req, "accept").status_code)
        self.backdated(3, timedelta(minutes=5), actor=self.psy, recipient=self.sw,
                       kind="child_sessions")
        with override_settings(**LIMITS), self.outbox() as sent, clock_at(
                NOW + email_notifications.ANSWER_MAIL_GRACE + timedelta(minutes=1)):
            self.assertEqual(1, mail_overdue_answers())
        self.assertEqual([OVERFLOW], self.kinds_to(sent, self.sw))
        req.refresh_from_db()
        self.assertIsNotNone(req.answer_mailed_at)
        with override_settings(**LIMITS), self.outbox() as later, clock_at(
                NOW + timedelta(hours=3)):
            self.assertEqual(0, mail_overdue_answers())
        self.assertEqual([], later)

    def test_the_dialogs_call_for_a_held_acceptance_says_it_was_dealt_with(self):
        """`mailed` is the claim, as it is when the social worker has switched
        the email off: true does not mean an email left."""
        req = self._ask(self.psy)
        self.assertEqual(200, self._answer(req, "accept").status_code)
        self.backdated(3, timedelta(minutes=5), actor=self.psy, recipient=self.sw,
                       kind="child_sessions")
        with override_settings(**LIMITS), self.outbox() as sent:
            res = self._as(self.psy).post(f"/api/assignment-requests/{req.id}/mail-answer/")
        self.assertEqual((200, {"mailed": True}), (res.status_code, res.json()))
        self.assertEqual([OVERFLOW], self.kinds_to(sent, self.sw))

    def test_every_call_to_notify_in_the_project_says_who_caused_it(self):
        """A caller that leaves `by` out sends an email nothing can limit. Found
        by reading the source, so a sixth domain function is held to it too."""
        # utf-8-sig: children/views.py opens with a byte-order mark, which the
        # compiler accepts and a plain utf-8 read hands to `ast` as a character.
        skipped = {".venv", "__pycache__", "tests", "migrations", "staticfiles", "media"}
        calls = []
        for folder, dirs, files in os.walk(BACKEND):
            dirs[:] = [d for d in dirs if d not in skipped]
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = Path(folder) / name
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                    if isinstance(node, ast.Call) and (
                            getattr(node.func, "id", None) == "notify"
                            or getattr(node.func, "attr", None) == "notify"):
                        calls.append((path.relative_to(BACKEND).as_posix(), node.lineno,
                                      "by" in {k.arg for k in node.keywords}))
        self.assertGreaterEqual(len(calls), 7, "the calls were not found")
        self.assertEqual([], [(p, n) for p, n, has_by in calls if not has_by])


# --------------------------------------------------------------------------
# A copy with no mail service
# --------------------------------------------------------------------------

@override_settings(BREVO_API_KEY="", **LIMITS)
class ConsoleModeTest(LimitBase):
    """The local copy: nothing is sent, every email is a log line - and the limit
    can be watched working there, so sends are counted and holds are logged."""

    def test_sends_are_logged_and_counted_and_the_held_one_says_why(self):
        with self.assertLogs("accounts.email_notifications", "WARNING") as logged:
            self.withdrawn(times=4)
        lines = [r.getMessage() for r in logged.records]
        subject = f"Case {self._ref(self.child)} is no longer waiting for you"
        self.assertEqual(
            [f"EMAIL (console) request_withdrawn to psy@t.ph: {subject}"] * 3
            + [f"EMAIL (held: limit) request_withdrawn to psy@t.ph: {subject}",
               f"EMAIL (console) overflow to psy@t.ph: {NOTICE}"],
            lines)
        self.assertEqual(3, self.records(kind="request_withdrawn").count())
        self.assertEqual(1, self.records(kind=OVERFLOW).count())
        self.assertEqual(1, self.limit_lines().count())

    def test_nothing_is_queued_and_the_notice_is_not_repeated(self):
        with patch("accounts.email_notifications._queue") as queue, \
                self.assertLogs("accounts.email_notifications", "WARNING") as logged:
            self.withdrawn(times=7)
        queue.assert_not_called()
        self.assertEqual(
            1, len([r for r in logged.records if "overflow" in r.getMessage()]))
        self.assertEqual(
            4, len([r for r in logged.records if r.getMessage().startswith("EMAIL (held")]))


# --------------------------------------------------------------------------
# The ISA is told
# --------------------------------------------------------------------------

@override_settings(**LIMITS)
class IsaLineTest(LimitBase):
    def test_one_security_line_for_the_account_in_an_hour(self):
        with self.outbox():
            self.withdrawn(times=3)
            self.assertEqual(0, self.limit_lines().count(), "not before the limit is reached")
            self.withdrawn(times=6)
        (line,) = self.limit_lines()
        self.assertEqual(
            (self.sw.pk, ActivityLog.UPDATED, ActivityLog.SECURITY, "User", self.sw.pk,
             "Editha Pascua reached the email limit", "email_limit", None),
            (line.actor_id, line.action, line.category, line.entity_type, line.entity_id,
             line.entity_label, line.kind, line.recipient_id))
        self.assertEqual("Editha Pascua", line.actor_label)

    def test_the_line_names_no_colleague_and_no_case(self):
        with self.outbox():
            self.withdrawn(times=4)
        (line,) = self.limit_lines()
        for text in (line.entity_label, line.actor_label):
            for leak in ("Marivic", "Bulan", "psy@", self._ref(self.child), "Ana", "Cruz"):
                self.assertNotIn(leak, text)

    def test_each_account_that_reaches_a_limit_gets_a_line_of_its_own(self):
        with self.outbox():
            self.withdrawn(self.psy, times=4)
            self.withdrawn(self.psy, by=self.other_sw, times=4)
            self.withdrawn(self.psy, by=self.admin, times=4)
        self.assertEqual({self.sw.pk, self.other_sw.pk, self.admin.pk},
                         {line.actor_id for line in self.limit_lines()})
        self.assertEqual(3, self.limit_lines().count())

    def test_the_isa_is_told_once_however_many_colleagues_are_held(self):
        with self.outbox() as sent:
            self.withdrawn(self.psy, times=4)
            self.withdrawn(self.psy2, times=3)           # the 5 an hour is reached here
        self.assertEqual(1, self.limit_lines().count())
        self.assertEqual(["request_withdrawn"] * 2 + [OVERFLOW], self.kinds_to(sent, self.psy2))

    def test_a_notice_that_fails_does_not_stop_the_isa_being_told(self):
        with self.outbox(), patch(
                "accounts.email_notifications._send_overflow_notice",
                side_effect=RuntimeError("the page would not render")), \
                self.assertLogs("accounts.email_notifications", "ERROR"):
            self.withdrawn(times=4)
        self.assertEqual(1, self.limit_lines().count())

    def test_a_line_that_fails_does_not_stop_the_colleague_being_told(self):
        with self.outbox() as sent, patch(
                "accounts.email_notifications._tell_the_isa",
                side_effect=RuntimeError("the log is full")), \
                self.assertLogs("accounts.email_notifications", "ERROR"):
            self.withdrawn(times=4)
        self.assertEqual(["request_withdrawn"] * 3 + [OVERFLOW], self.kinds_to(sent, self.psy))

    def test_the_line_is_in_the_accounts_own_drawer_but_not_in_the_bell(self):
        """It is written as often as once an hour for as long as the account
        goes on, which would fill the ISA's 50 lines; the console shows it."""
        with self.outbox():
            self.withdrawn(times=4)
        self.assertEqual(1, self.limit_lines().count())
        feed = self._as(self.admin).get("/api/activity/")
        self.assertEqual(200, feed.status_code)
        self.assertEqual([], [row for row in feed.json()
                              if row["entity_label"].endswith("reached the email limit")])
        drawer = self._as(self.admin).get(f"/api/users/{self.sw.pk}/activity/")
        self.assertEqual(200, drawer.status_code)
        self.assertEqual(
            ["Editha Pascua reached the email limit"],
            [row["entity_label"] for row in drawer.json()
             if row["entity_label"].endswith("reached the email limit")])

    def test_a_long_or_untidy_name_is_cut_to_one_plain_line(self):
        User.objects.filter(pk=self.sw.pk).update(
            first_name="Editha\nPascua " + "X" * 120, last_name="\u202eLast")
        self.sw.refresh_from_db()
        with self.outbox():
            self.withdrawn(times=4)
        (line,) = self.limit_lines()
        self.assertLessEqual(len(line.entity_label), 125)
        self.assertNotIn("\n", line.entity_label)
        self.assertNotIn("‮", line.entity_label)
        self.assertTrue(line.entity_label.endswith(" reached the email limit"))


# --------------------------------------------------------------------------
# Nothing about the child
# --------------------------------------------------------------------------

@override_settings(**LIMITS)
class PrivacyTest(LimitBase):
    """The notice and the ISA's line carry the account's name and nothing about
    a case: not the child, not the reference, not what the held email was."""

    def test_neither_the_notice_nor_the_line_carries_the_child(self):
        child = Child.objects.create(
            first_name="Zyxwanita", last_name="Qorvath", fullname="Zyxwanita Qorvath",
            birth_date=self.born, case_type="Foster Care", case_category="Neglected",
            custodian_name="Brunhilda Vexley", referral_reason="Quillon caseload is full",
            social_worker=self.sw, **planted_text())
        with self.outbox() as sent:
            for _ in range(5):
                request_withdrawn(self.psy, child, by=self.sw)
        notice = self.to(sent, self.psy)[-1]
        self.assertEqual((OVERFLOW, NOTICE), (notice[0], notice[1]["subject"]))
        (line,) = self.limit_lines()
        planted = planted_text()
        self.assertGreaterEqual(len(planted), 15)
        sentinels = ("Zyxwanita", "Qorvath", "Brunhilda", "Vexley", "Quillon", "Foster Care",
                     "Neglected", self._ref(child), self.born.isoformat(), *planted.values())
        held_words = ("withdrew", "withdrawn", "no longer waiting", "asked you", "booked",
                      "cancelled", "accepted", "declined", "reopened")
        everything = (json_of(notice[1]) + " " + " ".join(
            str(getattr(line, f)) for f in ("actor_label", "entity_type", "entity_label")))
        for sentinel in sentinels:
            self.assertNotIn(sentinel.lower(), everything.lower(), sentinel)
        for word in held_words:
            self.assertNotIn(word, notice[1]["textContent"].lower(), word)
        self.assertNotIn(self.psy.email, line.entity_label)


# --------------------------------------------------------------------------
# Keeping the records
# --------------------------------------------------------------------------

class PruneTest(LimitBase):
    def test_rows_older_than_25_hours_go_and_newer_stay(self):
        self.backdated(2, SEND_RECORD_KEEP + timedelta(minutes=1))
        self.backdated(2, SEND_RECORD_KEEP - timedelta(minutes=1))
        self.backdated(1, timedelta(minutes=1))
        self.assertEqual(2, prune_send_records(now=NOW))
        self.assertEqual(3, EmailSendRecord.objects.count())
        self.assertFalse(EmailSendRecord.objects.filter(
            created_at__lt=NOW - SEND_RECORD_KEEP).exists())

    def test_the_keep_is_25_hours(self):
        self.assertEqual(timedelta(hours=25), SEND_RECORD_KEEP)

    def test_it_uses_the_clock_when_given_no_time(self):
        self.backdated(1, timedelta(hours=26))
        self.assertEqual(1, prune_send_records())
        self.assertEqual(0, EmailSendRecord.objects.count())

    def test_the_once_a_minute_sweep_prunes(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.backdated(3, timedelta(hours=30))
        maybe_sweep()
        self.assertEqual(0, EmailSendRecord.objects.count())
        # A second sweep inside the minute does nothing, and nothing breaks.
        self.backdated(1, timedelta(hours=30))
        maybe_sweep()
        self.assertEqual(1, EmailSendRecord.objects.count())

    def test_the_reminder_run_prunes_but_a_dry_run_does_not(self):
        self.backdated(2, timedelta(hours=30))
        send_session_reminders(dry_run=True)
        self.assertEqual(2, EmailSendRecord.objects.count())
        send_session_reminders()
        self.assertEqual(0, EmailSendRecord.objects.count())

    def test_it_never_raises(self):
        with patch("accounts.email_notifications.EmailSendRecord.objects") as manager, \
                self.assertLogs("accounts.email_notifications", "ERROR"):
            manager.filter.side_effect = RuntimeError("no database")
            self.assertEqual(0, prune_send_records())

    def test_a_deleted_actor_leaves_the_row_to_age_out(self):
        """SET_NULL: removing an account does not take its colleagues' counts
        with it, and a row with no actor is counted against nobody."""
        extra = User.objects.create_user(email="gone@t.ph", username="gone", password="x",
                                         role=self.roles[Role.STAFF])
        self.backdated(1, timedelta(minutes=5), actor=extra)
        extra.delete()
        row = EmailSendRecord.objects.get()
        self.assertIsNone(row.actor_id)

    def test_a_deleted_recipient_takes_the_row_with_it(self):
        extra = User.objects.create_user(email="gone@t.ph", username="gone", password="x",
                                         role=self.roles[Role.PSYCHOLOGIST])
        self.backdated(1, timedelta(minutes=5), recipient=extra)
        extra.delete()
        self.assertEqual(0, EmailSendRecord.objects.count())


class AttributionTest(LimitBase):
    def test_a_new_record_asked_of_a_psychologist_counts_as_the_social_worker(self):
        """Created through the API, it is one email caused by the person who
        filled the form in."""
        with override_settings(**LIMITS), self.outbox() as sent:
            res = self._as(self.sw).post("/api/children/", complete(
                first_name="Zyxwanita", last_name="Qorvath", birth_date=str(self.born),
                custodian_name="Brunhilda Vexley (foster parent)",
                psychologist=self.psy.id), format="json")
            self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["case_request"], self.kinds_to(sent, self.psy))
        (row,) = EmailSendRecord.objects.all()
        self.assertEqual((self.sw.pk, self.psy.pk, "case_request"),
                         (row.actor_id, row.recipient_id, row.kind))


def json_of(payload):
    import json
    return json.dumps(payload, ensure_ascii=False)
