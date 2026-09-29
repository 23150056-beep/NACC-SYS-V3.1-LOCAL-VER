"""Every time written for a person is on the 12-hour clock (29 Sep 2026).

The owner asked for no military time anywhere. What the server WRITES - a
refusal, an assistant answer, Monitoring's next session - says "9:00 AM". A
slot's `start` stays "09:00": it is data, sent back when booking, and the
screen writes it out itself (frontend/src/utils/time.js).
"""
from datetime import datetime, time, timedelta

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from assistant import tools
from config.clock import clock
from scheduling.models import AvailabilityBlock
from scheduling.tests.test_api import SchedulingBase, child_with_referral, next_weekday


class ClockTests(SimpleTestCase):
    def test_the_hours_that_trip_a_hand_built_clock(self):
        cases = {time(0, 0): "12:00 AM", time(0, 30): "12:30 AM", time(9, 5): "9:05 AM",
                 time(11, 59): "11:59 AM", time(12, 0): "12:00 PM", time(13, 30): "1:30 PM",
                 time(23, 59): "11:59 PM"}
        for t, spoken in cases.items():
            self.assertEqual(spoken, clock(t), t)

    def test_a_datetime_reads_the_same_as_its_time(self):
        self.assertEqual("2:15 PM", clock(datetime(2026, 9, 30, 14, 15)))


class WrittenTimesTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.wednesday_9 = next_weekday(2, 9)
        self._auth("s@racco1.gov.ph")

    def _book(self, start, child=None, psychologist=None):
        return self.client.post("/api/appointments/", {
            "child": (child or self.child).id,
            "psychologist": (psychologist or self.psy).id,
            "start": start.isoformat(), "duration_minutes": 60,
            "purpose": "session"}, format="json")

    def test_a_psychologist_clash_names_the_time_on_the_12_hour_clock(self):
        self.assertEqual(201, self._book(self.wednesday_9).status_code)
        r = self._book(self.wednesday_9, child=child_with_referral("Ben", self.psy))
        self.assertEqual(400, r.status_code)
        self.assertIn("from 9:00 AM.", str(r.data))
        self.assertNotIn("09:00", str(r.data))

    def test_a_child_clash_names_the_time_on_the_12_hour_clock(self):
        AvailabilityBlock.objects.create(
            psychologist=self.other, weekday=2, start_time="09:00",
            end_time="12:00", capacity=2)
        self.assertEqual(201, self._book(self.wednesday_9).status_code)
        r = self._book(self.wednesday_9, psychologist=self.other)
        self.assertEqual(400, r.status_code)
        self.assertIn("appointment at 9:00 AM that day", str(r.data))

    def test_an_overlapping_window_is_named_on_the_12_hour_clock(self):
        self._auth("p@racco1.gov.ph")
        r = self.client.post("/api/availability/", {
            "weekday": 2, "start_time": "10:00", "end_time": "14:00", "capacity": 1},
            format="json")
        self.assertEqual(400, r.status_code)
        self.assertIn("9:00 AM–12:00 PM", str(r.data))

    def test_monitoring_writes_the_next_session_on_the_12_hour_clock(self):
        start = next_weekday(2, 10)
        self._book(start)
        self._auth("a@racco1.gov.ph")
        rows = self.client.get("/api/reports/monitoring/").data
        ana = next(r for r in rows if r["child_name"] == "Ana")
        self.assertEqual(f"{start:%Y-%m-%d} 10:00 AM", ana["next_session"])

    def test_the_assistant_answers_on_the_12_hour_clock(self):
        # An afternoon session, on the psychologist's own calendar.
        start = timezone.make_aware(datetime.combine(
            timezone.localdate() + timedelta(days=1), time(14, 30)))
        self._auth("p@racco1.gov.ph")
        self.assertEqual(201, self.client.post("/api/appointments/", {
            "child": self.child.id, "start": start.isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json").status_code)
        req = APIRequestFactory().get("/api/assistant/ask/")
        req.user = self.psy
        out = tools.REGISTRY["list_my_appointments"]["resolve"](req, {"when": "tomorrow"})
        self.assertEqual([f"{start:%a %d %b}, 2:30 PM"], [i["when"] for i in out["items"]])

    def test_a_slot_stays_data(self):
        # The booking grid sends `start` straight back; it must not be prose.
        self._auth("p@racco1.gov.ph")
        r = self.client.get("/api/availability/slots/", {
            "psychologist": self.psy.id, "date": self.wednesday_9.date().isoformat()})
        self.assertEqual(200, r.status_code, r.data)
        self.assertIn("09:00", [s["start"] for s in r.data["slots"]])
