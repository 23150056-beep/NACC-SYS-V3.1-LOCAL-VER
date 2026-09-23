"""The wait for a first session: one definition, shown on the Agency Summary,
answered by the chatbot.

The owner chose the ends: from the day the child's record was created to their
first session recorded as completed. The definition tests pin that choice and
what it leaves out; the parity tests ask the Summary for the same numbers the
chatbot gives; the seeder test keeps the demo able to show any of it.
"""
from datetime import datetime, time, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models import Role
from assistant import tools
from children.models import Child
from clinical.reports import first_session_wait
from locations.models import Barangay, Municipality, Province
from scheduling.models import Appointment

User = get_user_model()


def at(day, hour=10):
    """A Manila wall-clock time on `day`."""
    return timezone.make_aware(datetime.combine(day, time(hour, 0)),
                               timezone.get_current_timezone())


class WaitBase(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r) for r in
                 (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)}
        self.admin = User.objects.create_user(email="a@racco1.gov.ph", username="a",
                                              password="pass1234", role=roles[Role.ADMINISTRATOR])
        self.staff = User.objects.create_user(email="s@racco1.gov.ph", username="s",
                                              password="pass1234", role=roles[Role.STAFF])
        self.psy = User.objects.create_user(email="p@racco1.gov.ph", username="p",
                                            password="pass1234", role=roles[Role.PSYCHOLOGIST])
        self.other = User.objects.create_user(email="o@racco1.gov.ph", username="o",
                                              password="pass1234", role=roles[Role.PSYCHOLOGIST])
        self.today = timezone.localdate()
        self.factory = APIRequestFactory()

    def child(self, name, opened_days_ago, psy=None, status=Child.ACTIVE, hour=9):
        c = Child.objects.create(fullname=name, assigned_psychologist=psy or self.psy,
                                 status=status)
        # auto_now_add ignores a value passed to create().
        Child.objects.filter(pk=c.pk).update(
            created_at=at(self.today - timedelta(days=opened_days_ago), hour))
        return c

    def appt(self, child, days_ago, status, hour=10):
        Appointment.objects.create(
            child=child, psychologist=child.assigned_psychologist,
            start=at(self.today - timedelta(days=days_ago), hour), status=status)

    def wait(self, **kw):
        return first_session_wait(Child.objects.all(), self.today, **kw)

    def stats(self, user, **raw):
        call = tools.validate("get_statistics", {"measure": "first_session_wait", **raw})
        req = self.factory.get("/")
        req.user = user
        return tools.REGISTRY["get_statistics"]["resolve"](req, call.args)


class DefinitionTest(WaitBase):
    def test_from_the_record_to_the_first_completed_session(self):
        c = self.child("Ana Lopez", 30)
        self.appt(c, 20, "completed")
        self.appt(c, 10, "completed")
        w = self.wait()
        self.assertEqual((1, 10, 10), (w["seen"], w["median_days"], w["longest_days"]))

    def test_a_no_show_a_cancellation_or_a_booking_is_not_a_first_session(self):
        seen = self.child("Ana Lopez", 30)
        self.appt(seen, 25, "no_show")
        self.appt(seen, 24, "cancelled")
        self.appt(seen, 20, "completed")
        never = self.child("Ben Cruz", 30)
        self.appt(never, 25, "no_show")
        self.appt(never, -7, "scheduled")
        w = self.wait()
        self.assertEqual((1, 10), (w["seen"], w["median_days"]))
        # Ben has been booked and missed, but never seen: still waiting.
        self.assertEqual((1, 30), (w["waiting"], w["longest_waiting_days"]))

    def test_a_same_day_session_is_a_wait_of_zero(self):
        c = self.child("Ana Lopez", 5)
        self.appt(c, 5, "completed")
        w = self.wait()
        # Zero days is a real wait, not a missing one.
        self.assertEqual((1, 0), (w["seen"], w["median_days"]))

    def test_a_session_dated_before_the_record_is_not_counted(self):
        # A file typed in after care had started. Not a negative wait, and not
        # zero either: left out, and counted as left out.
        c = self.child("Ana Lopez", 10)
        self.appt(c, 20, "completed")
        w = self.wait()
        self.assertEqual((0, None, 1), (w["seen"], w["median_days"], w["before_record"]))

    def test_a_closed_case_that_was_never_seen_is_not_waiting(self):
        self.child("Ana Lopez", 30, status=Child.INACTIVE)
        closed_after_care = self.child("Ben Cruz", 30, status=Child.INACTIVE)
        self.appt(closed_after_care, 26, "completed")
        w = self.wait()
        # Nobody is waiting on a closed case, but a wait that was served is
        # still a wait, whatever happened to the case afterwards.
        self.assertEqual((0, 1, 4), (w["waiting"], w["seen"], w["median_days"]))

    def test_the_median_of_an_even_count(self):
        for name, opened, first in (("Ana Lopez", 10, 7), ("Ben Cruz", 10, 6)):
            self.appt(self.child(name, opened), first, "completed")
        self.assertEqual(3.5, self.wait()["median_days"])       # 3 and 4
        self.appt(self.child("Cara Diaz", 10), 5, "completed")  # 3, 4 and 5
        self.appt(self.child("Dan Reyes", 10), 4, "completed")  # 3, 4, 5 and 6
        self.assertEqual(4.5, self.wait()["median_days"])
        self.appt(self.child("Eva Santos", 10), 3, "completed")  # ... and 7
        median = self.wait()["median_days"]
        self.assertEqual(5, median)
        self.assertIsInstance(median, int)                       # "5 days", not "5.0"

    def test_a_window_picks_children_by_the_day_they_were_first_seen(self):
        inside = self.child("Ana Lopez", 100)
        self.appt(inside, 30, "completed")
        later = self.child("Ben Cruz", 100)
        self.appt(later, 10, "completed")
        # First seen before the window. A later session inside it does not
        # make this child's wait end inside it.
        earlier = self.child("Cara Diaz", 100)
        self.appt(earlier, 60, "completed")
        self.appt(earlier, 30, "completed")
        self.child("Dan Reyes", 50)                               # still waiting
        w = self.wait(start=self.today - timedelta(days=40),
                      end=self.today - timedelta(days=20))
        self.assertEqual((1, 70), (w["seen"], w["median_days"]))
        # Still-waiting is as of today, whatever the window.
        self.assertEqual((1, 50), (w["waiting"], w["longest_waiting_days"]))

    def test_the_end_of_a_window_is_exclusive_and_either_end_may_be_open(self):
        c = self.child("Ana Lopez", 30)
        self.appt(c, 20, "completed")
        day = self.today - timedelta(days=20)
        self.assertEqual(0, self.wait(end=day)["seen"])
        self.assertEqual(1, self.wait(end=day + timedelta(days=1))["seen"])
        self.assertEqual(1, self.wait(start=day)["seen"])
        self.assertEqual(0, self.wait(start=day + timedelta(days=1))["seen"])

    def test_days_are_counted_on_the_manila_calendar(self):
        # Created at 07:00 in Manila, which is still the day before in UTC.
        # Seen the next morning: one day, not two.
        c = self.child("Ana Lopez", 10, hour=7)
        self.appt(c, 9, "completed")
        self.assertEqual(1, self.wait()["median_days"])


class SummaryCardTest(WaitBase):
    def setUp(self):
        super().setUp()
        a = self.child("Ana Lopez", 40)
        self.appt(a, 30, "completed")                             # 10 days
        b = self.child("Ben Cruz", 40, psy=self.other)
        self.appt(b, 20, "completed")                             # 20 days
        c = self.child("Cara Diaz", 10)
        self.appt(c, 20, "completed")                             # before its record
        self.child("Dan Reyes", 15, psy=self.other)               # waiting 15 days

    def summary(self, **params):
        client = APIClient()
        client.force_authenticate(self.admin)
        res = client.get("/api/reports/summary/", params)
        self.assertEqual(200, res.status_code)
        return res

    def test_the_summary_carries_the_agency_wait(self):
        self.assertEqual({"seen": 2, "median_days": 15, "longest_days": 20,
                          "before_record": 1, "waiting": 1, "longest_waiting_days": 15},
                         self.summary().data["first_session_wait"])

    def test_the_summary_window_is_inclusive_of_to(self):
        # `to` is the last day shown, so a first session ON it is inside.
        day = (self.today - timedelta(days=30)).isoformat()
        w = self.summary(**{"from": day, "to": day}).data["first_session_wait"]
        self.assertEqual((1, 10), (w["seen"], w["median_days"]))

    def test_the_csv_export_carries_it_too(self):
        body = self.summary(export="csv").content.decode()
        self.assertIn("Wait for a first session,Value", body)
        self.assertIn("Median days from record created to first completed session,15", body)
        self.assertIn("First session dated before the record (not counted),1", body)


class ChatbotWaitTest(SummaryCardTest):
    def test_the_answer_is_the_median_in_days(self):
        out = self.stats(self.admin)
        self.assertEqual({"value": "15 days", "label": "median wait for a first session, all time"},
                         out["figure"])
        # The count behind it stays the total, so the log and ai_eval can tell
        # an answer about nobody from one about somebody.
        self.assertEqual(2, out["total"])
        self.assertEqual(2, tools.result_size(out))
        self.assertEqual("2 children seen for a first session, all time", out["title"])

    def test_the_note_says_who_is_in_the_median_and_who_is_not(self):
        note = self.stats(self.admin)["note"]
        self.assertIn("2 children seen, counted from the day the record was created to "
                      "the first completed session. Longest wait 20 days.", note)
        self.assertIn("1 active child is still waiting, the longest for 15 days so far "
                      "— not in the median.", note)
        self.assertIn("1 not counted: the first session is dated before the record "
                      "was created.", note)

    def test_a_psychologist_sees_their_own_children(self):
        out = self.stats(self.psy)
        # Pia: Ana (10 days) and Cara (before her record). Not Oscar's Ben or Dan.
        self.assertEqual({"value": "10 days", "label": "median wait for a first session, all time"},
                         out["figure"])
        self.assertEqual(1, out["total"])
        self.assertNotIn("still waiting", out["note"])

    def test_staff_and_administrators_see_the_agency(self):
        for user in (self.admin, self.staff):
            self.assertEqual(2, self.stats(user)["total"])

    def test_a_period_with_nobody_seen_has_no_figure(self):
        out = self.stats(self.admin, period="next_week")
        self.assertIsNone(out["figure"])
        self.assertEqual(0, out["total"])
        self.assertEqual(0, tools.result_size(out))
        self.assertTrue(out["note"].startswith("No child had a first completed session in "
                                               "this period"))
        # Somebody is still waiting, and the answer says so even here.
        self.assertIn("still waiting", out["note"])

    def test_no_scorecard_of_psychologists(self):
        out = self.stats(self.admin, by="psychologist")
        self.assertEqual(("none", []), (out["by"], out["rows"]))
        self.assertIn("can't be broken down by psychologist", out["note"])

    def test_the_summary_is_linked_for_those_who_can_open_it(self):
        self.assertEqual("/reports/summary", self.stats(self.staff)["screen"]["path"])
        self.assertIsNone(self.stats(self.psy)["screen"])

    def test_follow_ups_offer_periods(self):
        call = tools.validate("get_statistics", {"measure": "first_session_wait"})
        offers = tools.followups(call, self.stats(self.admin), Role.ADMINISTRATOR)
        self.assertEqual(["This month?", "This year?", "Last year?"],
                         [o["label"] for o in offers])

    def test_the_words_people_use_reach_it(self):
        for said in ("wait", "Wait time", "waiting_time", "time to first session",
                     "days_to_first_session", "unang session", "paghihintay"):
            call = tools.validate("get_statistics", {"measure": said})
            self.assertEqual("first_session_wait", call.args["measure"], said)
        # And the neighbours keep theirs.
        for said, measure in (("session", "sessions"), ("intake", "intake")):
            self.assertEqual(measure, tools.validate(
                "get_statistics", {"measure": said}).args["measure"])

    def test_the_echo(self):
        call = tools.validate("get_statistics",
                              {"measure": "first_session_wait", "period": "this_year"})
        self.assertEqual("Looking up: waits for a first session this year", call.echo)


class SingularTest(WaitBase):
    def test_one_child_one_day(self):
        c = self.child("Ana Lopez", 3)
        self.appt(c, 2, "completed")
        out = self.stats(self.admin)
        self.assertEqual("1 day", out["figure"]["value"])
        self.assertEqual("1 child seen for a first session, all time", out["title"])
        self.assertTrue(out["note"].startswith("1 child seen, counted from"))
        self.assertIn("Longest wait 1 day.", out["note"])

    def test_nobody_seen_yet_is_said_plainly(self):
        self.child("Ben Cruz", 4)
        note = self.stats(self.admin)["note"]
        self.assertTrue(note.startswith("No child has had a completed session yet"), note)
        self.assertIn("1 active child is still waiting, the longest for 4 days so far", note)

    def test_a_period_is_named_in_the_label(self):
        # The only child, seen today: inside this month whatever the date
        # the suite runs on.
        e = self.child("Eva Santos", 5)
        self.appt(e, 0, "completed")
        out = self.stats(self.admin, period="this_month")
        self.assertEqual({"value": "5 days", "label": "median wait for a first session this month"},
                         out["figure"])
        self.assertEqual("1 child seen for a first session this month", out["title"])


class ParityTest(SummaryCardTest):
    """The chatbot and the Agency Summary's card, same caller, same window."""

    def test_all_time(self):
        w = self.summary().data["first_session_wait"]
        out = self.stats(self.admin)
        self.assertEqual(w["seen"], out["total"])
        self.assertEqual(tools._days(w["median_days"]), out["figure"]["value"])
        self.assertIn(f"Longest wait {tools._days(w['longest_days'])}", out["note"])
        self.assertIn(f"{w['waiting']} active child is still waiting", out["note"])

    def test_last_month(self):
        start, end = tools.period_range("last_month")
        w = self.summary(**{"from": start.isoformat(),
                            "to": (end - timedelta(days=1)).isoformat()}).data["first_session_wait"]
        out = self.stats(self.admin, period="last_month")
        self.assertEqual(w["seen"], out["total"])
        if w["median_days"] is None:
            self.assertIsNone(out["figure"])
        else:
            self.assertEqual(tools._days(w["median_days"]), out["figure"]["value"])


@override_settings(DEBUG=True)
class SeededRecordsTest(TestCase):
    """The demo has to be able to show this at all."""

    def test_a_seeded_record_dates_from_intake(self):
        province = Province.objects.create(psgc_code="012800000", name="Ilocos Norte")
        town = Municipality.objects.create(psgc_code="012812000", name="Laoag City",
                                           province=province)
        Barangay.objects.create(psgc_code="012812001", name="Barangay 1", municipality=town)
        call_command("seed_demo_data", children=6, stdout=StringIO())

        for child in Child.objects.all():
            self.assertEqual(child.date_of_admission,
                             timezone.localtime(child.created_at).date(), child.fullname)
        # Left at the moment the seeder ran, every seeded session predated its
        # record and this was zero.
        self.assertGreater(first_session_wait(Child.objects.all(),
                                              timezone.localdate())["seen"], 0)
