"""Offer times somebody can actually book.

The booking form asked for a date and a time in two blank boxes, and the only
help beside them was a row of chips showing the START of each availability
window - so everyone clicked 09:00, and the second person to do it was told
the slot was taken. Typing into an empty time field is guessing, and the
system knows the answer.

The contract these tests exist to protect is one sentence: **anything this
offers can be booked.** It is enforced literally at the bottom of this file by
taking every slot the endpoint returns and posting it.
"""
from datetime import timedelta

from django.utils import timezone

from children.models import Child
from scheduling import booking
from scheduling.models import Appointment, AvailabilityBlock
from scheduling.tests.test_api import SchedulingBase, next_weekday


class BookableSlotTests(SchedulingBase):
    """The Wednesday block from SchedulingBase is 09:00-12:00, capacity 2."""

    def setUp(self):
        super().setUp()
        self.wednesday = next_weekday(2, 9).date()

    def _slots(self, duration=60, child=None, psychologist=None):
        return booking.bookable_slots(
            psychologist or self.psy, child if child is not None else self.child,
            self.wednesday, duration_minutes=duration)

    def test_a_three_hour_window_offers_hourly_starts(self):
        # 09:00-12:00 at 60 minutes: 09:00, 09:30, 10:00, 10:30, 11:00 - and
        # NOT 11:30, which would finish at 12:30.
        self.assertEqual(["09:00", "09:30", "10:00", "10:30", "11:00"],
                         [s["start"] for s in self._slots()])

    def test_a_longer_session_offers_fewer_starts(self):
        self.assertEqual(["09:00", "09:30", "10:00", "10:30"],
                         [s["start"] for s in self._slots(duration=90)])

    def test_a_session_longer_than_the_window_offers_nothing(self):
        self.assertEqual([], self._slots(duration=240))

    def test_a_booked_time_disappears_from_the_offer(self):
        Appointment.objects.create(
            child=Child.objects.create(fullname="Ben", assigned_psychologist=self.psy),
            psychologist=self.psy, start=next_weekday(2, 10), duration_minutes=60)
        starts = [s["start"] for s in self._slots()]
        # 10:00 is taken, and 09:30 would run into it.
        self.assertNotIn("10:00", starts)
        self.assertNotIn("09:30", starts)
        self.assertIn("09:00", starts)
        self.assertIn("11:00", starts)

    def test_a_cancelled_appointment_gives_its_time_back(self):
        appt = Appointment.objects.create(
            child=Child.objects.create(fullname="Ben", assigned_psychologist=self.psy),
            psychologist=self.psy, start=next_weekday(2, 10), duration_minutes=60,
            status=Appointment.CANCELLED)
        self.assertIn("10:00", [s["start"] for s in self._slots()])
        self.assertEqual(Appointment.CANCELLED, appt.status)

    def test_a_time_the_CHILD_is_busy_elsewhere_disappears_too(self):
        # The clash is on the other psychologist's calendar, so a check that
        # only looked at this one would offer it.
        AvailabilityBlock.objects.create(
            psychologist=self.other, weekday=2, start_time="09:00",
            end_time="12:00", capacity=2)
        Appointment.objects.create(child=self.child, psychologist=self.other,
                                   start=next_weekday(2, 10), duration_minutes=60)
        self.assertNotIn("10:00", [s["start"] for s in self._slots()])

    def test_with_no_child_named_only_the_psychologist_constrains_it(self):
        # Before a child is chosen the form still wants to show the shape of
        # the day rather than an empty panel.
        self.assertEqual(5, len(self._slots(child=None)))

    def test_capacity_closes_the_whole_window(self):
        for hour, name in ((9, "Ben"), (11, "Cara")):
            Appointment.objects.create(
                child=Child.objects.create(fullname=name,
                                           assigned_psychologist=self.psy),
                psychologist=self.psy, start=next_weekday(2, hour),
                duration_minutes=60)
        # Capacity 2 is now used up, so 10:00 is free of clashes and still not
        # bookable. Offering it would be offering a refusal.
        self.assertEqual([], self._slots())

    def test_the_appointment_being_MOVED_does_not_block_its_own_day(self):
        # Rescheduling asks the same question as booking, with one difference:
        # the row being moved must not be read as a clash with itself, or the
        # time it currently holds vanishes from the grid offering to move it.
        appt = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=next_weekday(2, 10), duration_minutes=60)
        self.assertNotIn("10:00", [s["start"] for s in self._slots()])
        moving = booking.bookable_slots(
            self.psy, self.child, self.wednesday, duration_minutes=60,
            exclude_id=appt.pk)
        self.assertIn("10:00", [s["start"] for s in moving])

    def test_overlapping_windows_do_not_offer_a_time_twice(self):
        # The availability form refuses to create such a pair, but a seeder or
        # a direct write can, and a grid showing 09:00 beside 09:00 reads as a
        # broken screen rather than as bad data.
        AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=2, start_time="08:00",
            end_time="12:00", capacity=6)
        starts = [s["start"] for s in self._slots()]
        self.assertEqual(len(starts), len(set(starts)), starts)

    def test_a_day_with_no_window_offers_nothing(self):
        self.assertEqual([], booking.bookable_slots(
            self.psy, self.child, next_weekday(0, 9).date()))


class SlotEndpointTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.wednesday = next_weekday(2, 9).date()
        self._auth("s@racco1.gov.ph")

    def _get(self, **params):
        params.setdefault("psychologist", self.psy.id)
        params.setdefault("date", self.wednesday.isoformat())
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.get(f"/api/availability/slots/?{query}")

    def test_it_returns_the_days_bookable_starts(self):
        r = self._get(child=self.child.id)
        self.assertEqual(200, r.status_code)
        self.assertEqual(["09:00", "09:30", "10:00", "10:30", "11:00"],
                         [s["start"] for s in r.data["slots"]])

    def test_it_says_why_a_day_is_empty(self):
        # An empty list and "they do not work Mondays" are different answers,
        # and a booking screen that cannot tell them apart just looks broken.
        r = self._get(date=next_weekday(0, 9).date().isoformat(), child=self.child.id)
        self.assertEqual(200, r.status_code)
        self.assertEqual([], r.data["slots"])
        self.assertTrue(r.data["reason"])

    def test_it_needs_a_psychologist(self):
        r = self.client.get(f"/api/availability/slots/?date={self.wednesday}")
        self.assertEqual(400, r.status_code)

    def test_a_psychologist_cannot_read_a_colleagues_day(self):
        self._auth("p@racco1.gov.ph")
        r = self._get(psychologist=self.other.id)
        self.assertEqual(404, r.status_code)

    def test_a_psychologist_can_read_their_own_day(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(200, self._get().status_code)

    def test_everything_it_offers_can_actually_be_booked(self):
        """The contract, enforced rather than asserted.

        Each accepted booking removes times from the next answer, so this also
        walks the day down to nothing the way a real morning does.
        """
        booked = 0
        while True:
            slots = self._get(child=self.child.id).data["slots"]
            if not slots:
                break
            r = self.client.post("/api/appointments/", {
                "child": self.child.id, "psychologist": self.psy.id,
                "start": f"{self.wednesday.isoformat()}T{slots[0]['start']}:00",
                "duration_minutes": 60, "purpose": "session"}, format="json")
            self.assertEqual(201, r.status_code, f"offered {slots[0]} then refused it: {r.data}")
            booked += 1
            self.assertLess(booked, 10, "the offer never emptied")
        self.assertEqual(2, booked)   # capacity 2 on the Wednesday block


class SlotsRespectTodayTests(SchedulingBase):
    def test_a_window_that_has_already_started_today_is_not_offered(self):
        now = timezone.localtime()
        AvailabilityBlock.objects.create(
            psychologist=self.psy, date=now.date(),
            start_time="00:00", end_time="23:59", capacity=20)
        starts = [s["start"] for s in booking.bookable_slots(
            self.psy, self.child, now.date(), duration_minutes=60)]
        self.assertTrue(starts, "an all-day window today should still offer something")
        # Nothing offered may be in the past, and the earliest offer must be
        # after right now rather than at midnight.
        earliest = starts[0]
        self.assertGreater(earliest, (now - timedelta(minutes=1)).strftime("%H:%M"))
