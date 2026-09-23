"""Attendance: one definition, shown on the Agency Summary, counted by the
chatbot. The definition tests pin what the no-show rate leaves out and why;
the parity tests ask the Summary for the same numbers the chatbot gives.
"""
from collections import namedtuple
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models import Role
from assistant import tools
from children.models import Child
from clinical.reports import attendance
from scheduling.models import Appointment

User = get_user_model()
Row = namedtuple("Row", "status start")


class DefinitionTest(SimpleTestCase):
    now = timezone.make_aware(datetime(2026, 9, 23, 12, 0))
    past = now - timedelta(days=3)
    future = now + timedelta(days=3)

    def test_each_session_lands_in_exactly_one_place(self):
        out = attendance([Row("completed", self.past), Row("completed", self.past),
                          Row("no_show", self.past), Row("cancelled", self.past),
                          Row("scheduled", self.past), Row("scheduled", self.future)], self.now)
        self.assertEqual({"completed": 2, "no_show": 1, "cancelled": 1, "unrecorded": 1,
                          "upcoming": 1, "took_place": 3, "sessions": 5, "no_show_rate": 33},
                         out)

    def test_the_rate_is_missed_over_attended_plus_missed(self):
        # Cancelled and unrecorded are both left out: 1 of 4, not 1 of 6.
        out = attendance([Row("completed", self.past)] * 3 + [Row("no_show", self.past),
                         Row("cancelled", self.past), Row("scheduled", self.past)], self.now)
        self.assertEqual(25, out["no_show_rate"])

    def test_no_rate_when_nothing_has_taken_place(self):
        # Zero percent would claim perfect attendance for sessions that have
        # not happened. There is no rate yet.
        out = attendance([Row("scheduled", self.future), Row("cancelled", self.past)], self.now)
        self.assertIsNone(out["no_show_rate"])

    def test_a_cancellation_is_not_a_session(self):
        self.assertEqual(0, attendance([Row("cancelled", self.past)], self.now)["sessions"])


class AttendanceBase(TestCase):
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
        mine = Child.objects.create(fullname="Maria Santos", assigned_psychologist=self.psy)
        theirs = Child.objects.create(fullname="Juan Cruz", assigned_psychologist=self.other)

        # Last month: Pia 2 completed + 1 no-show; Oscar 1 completed + 1 cancelled.
        self.last_start, self.last_end = tools.period_range("last_month")
        mid_last = self.last_start + timedelta(days=10)
        for status in ("completed", "completed", "no_show"):
            self._appt(mine, self.psy, mid_last, status)
        self._appt(theirs, self.other, mid_last, "completed")
        self._appt(theirs, self.other, mid_last, "cancelled")
        # Long ago, never recorded; and next week, upcoming.
        self._appt(mine, self.psy, timezone.localdate() - timedelta(days=200), "scheduled")
        self._appt(theirs, self.other, timezone.localdate() + timedelta(days=8), "scheduled")
        self.factory = APIRequestFactory()

    def _appt(self, child, psy, day, status):
        start = timezone.make_aware(datetime.combine(day, time(10, 0)),
                                    timezone.get_current_timezone())
        Appointment.objects.create(child=child, psychologist=psy, start=start, status=status)

    def stats(self, user, **raw):
        call = tools.validate("get_statistics", {"measure": "sessions", **raw})
        req = self.factory.get("/")
        req.user = user
        return tools.REGISTRY["get_statistics"]["resolve"](req, call.args)

    def summary(self, user=None, **params):
        client = APIClient()
        client.force_authenticate(user or self.admin)
        res = client.get("/api/reports/summary/", params)
        self.assertEqual(200, res.status_code)
        return res


class SummaryCardTest(AttendanceBase):
    def test_the_summary_carries_agency_attendance(self):
        att = self.summary().data["attendance"]
        # 3 completed (Pia 2, Oscar 1), 1 no-show: the rate is 1 of 4.
        self.assertEqual((3, 1, 1, 1, 1, 6, 25),
                         (att["completed"], att["no_show"], att["cancelled"],
                          att["unrecorded"], att["upcoming"], att["sessions"],
                          att["no_show_rate"]))

    def test_the_summary_honours_from_and_to(self):
        att = self.summary(**{"from": self.last_start.isoformat(),
                              "to": (self.last_end - timedelta(days=1)).isoformat()}).data["attendance"]
        self.assertEqual((3, 1, 1, 0, 0, 25),
                         (att["completed"], att["no_show"], att["cancelled"],
                          att["unrecorded"], att["upcoming"], att["no_show_rate"]))

    def test_the_csv_export_carries_it_too(self):
        body = self.summary(export="csv").content.decode()
        self.assertIn("Attendance,Count", body)
        self.assertIn("No-show rate (%),25", body)


class ChatbotSessionsTest(AttendanceBase):
    def test_a_psychologist_counts_their_own_sessions(self):
        # Pia: 2 completed, 1 no-show last month, 1 unrecorded long ago.
        out = self.stats(self.psy)
        self.assertEqual(4, out["total"])
        self.assertTrue(out["note"].startswith("No-show rate 33% — 1 of the 3 sessions"))

    def test_staff_and_administrators_count_the_agency(self):
        for user in (self.admin, self.staff):
            self.assertEqual(6, self.stats(user)["total"])

    def test_by_status_in_a_period(self):
        out = self.stats(self.admin, by="status", period="last_month")
        self.assertEqual([("Completed", 3), ("No-show", 1)],
                         [(r["label"], r["count"]) for r in out["rows"]])
        self.assertEqual("4 sessions last month, by status", out["title"])
        self.assertIn("1 cancelled, not counted.", out["note"])

    def test_the_note_says_what_the_rate_leaves_out(self):
        note = self.stats(self.admin)["note"]
        self.assertIn("1 past session has not been recorded yet and is left out of the rate.", note)
        self.assertIn("1 cancelled, not counted.", note)

    def test_no_rate_for_a_period_with_nothing_yet_taken_place(self):
        out = self.stats(self.admin, period="next_week")
        self.assertIn("no no-show rate", out["note"])

    def test_no_scorecard_of_psychologists(self):
        # Deliberately not offered: see STAT_BY["sessions"].
        out = self.stats(self.admin, by="psychologist")
        self.assertEqual(("none", []), (out["by"], out["rows"]))
        self.assertIn("can't be broken down by psychologist", out["note"])

    def test_the_summary_is_linked_for_those_who_can_open_it(self):
        self.assertEqual("/reports/summary", self.stats(self.staff)["screen"]["path"])
        self.assertIsNone(self.stats(self.psy)["screen"])

    def test_follow_ups_offer_status_and_periods(self):
        call = tools.validate("get_statistics", {"measure": "sessions"})
        offers = tools.followups(call, self.stats(self.admin), Role.ADMINISTRATOR)
        self.assertEqual(["By status?", "This month?", "This year?", "Last year?"],
                         [o["label"] for o in offers])


class ParityTest(AttendanceBase):
    """The chatbot and the Agency Summary's card, same caller, same window."""

    def test_all_time(self):
        att = self.summary().data["attendance"]
        out = self.stats(self.admin, by="status")
        rows = {r["label"]: r["count"] for r in out["rows"]}
        self.assertEqual((att["sessions"], att["completed"], att["no_show"],
                          att["unrecorded"], att["upcoming"]),
                         (out["total"], rows["Completed"], rows["No-show"],
                          rows.get("Not yet recorded", 0), rows.get("Upcoming", 0)))
        self.assertIn(f"No-show rate {att['no_show_rate']}%", out["note"])

    def test_last_month(self):
        att = self.summary(**{"from": self.last_start.isoformat(),
                              "to": (self.last_end - timedelta(days=1)).isoformat()}).data["attendance"]
        out = self.stats(self.admin, period="last_month")
        self.assertEqual(att["sessions"], out["total"])
        self.assertIn(f"No-show rate {att['no_show_rate']}%", out["note"])
