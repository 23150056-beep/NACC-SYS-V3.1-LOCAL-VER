"""Can this calendar be trusted? - the rules that make a booking real.

Capacity was the only thing the booking endpoint checked, and capacity is a
count per day, not a rule about time. Everything here is a way the calendar
could show a booking that cannot physically happen:

* two children with the same psychologist at the same moment,
* an appointment running past the end of the window it was booked into,
* the same child in two rooms at once,
* and a reschedule, which went through a code path with no checks at all.

The availability window is a preference and a psychologist may work outside
it. Being in two places at once is not a preference, so the overlap rules
apply to every role, including on their own calendar.
"""
from datetime import timedelta

from django.utils import timezone

from children.models import Child
from scheduling.models import AvailabilityBlock, Appointment
from scheduling.tests.test_api import (
    SchedulingBase, child_with_referral, next_weekday,
)


class NoDoubleBookingTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.wednesday_9 = next_weekday(2, 9)
        self._auth("s@racco1.gov.ph")

    def _book(self, start, duration=60, child=None, psychologist=None):
        return self.client.post("/api/appointments/", {
            "child": (child or self.child).id,
            "psychologist": (psychologist or self.psy).id,
            "start": start.isoformat(),
            "duration_minutes": duration,
            "purpose": "session",
        }, format="json")

    def _other_child(self, name="Ben"):
        return child_with_referral(name, self.psy)

    def test_the_first_booking_is_accepted(self):
        # The control. Everything below has to fail for the RIGHT reason.
        self.assertEqual(201, self._book(self.wednesday_9).status_code)

    def test_two_appointments_cannot_start_at_the_same_moment(self):
        # The block has capacity 2, so the count-based check lets this through.
        # One psychologist still cannot see two children at 09:00.
        self._book(self.wednesday_9)
        r = self._book(self.wednesday_9, child=self._other_child())
        self.assertEqual(400, r.status_code)
        self.assertIn("already", str(r.data).lower())

    def test_an_appointment_cannot_start_inside_another_one(self):
        self._book(self.wednesday_9, duration=60)
        r = self._book(self.wednesday_9 + timedelta(minutes=30),
                       child=self._other_child())
        self.assertEqual(400, r.status_code)

    def test_an_appointment_cannot_swallow_another_one(self):
        # The new booking starts earlier and runs over the existing one, so a
        # check that only looked at the start time would miss it.
        self._book(self.wednesday_9 + timedelta(minutes=60), duration=30)
        r = self._book(self.wednesday_9, duration=120, child=self._other_child())
        self.assertEqual(400, r.status_code)

    def test_back_to_back_appointments_are_fine(self):
        # 09:00-10:00 then 10:00-11:00 do not overlap. A rule that refused
        # these would make a full clinic day impossible to book.
        self._book(self.wednesday_9, duration=60)
        r = self._book(self.wednesday_9 + timedelta(minutes=60), duration=60,
                       child=self._other_child())
        self.assertEqual(201, r.status_code)

    def test_a_cancelled_appointment_frees_its_time(self):
        first = self._book(self.wednesday_9).data
        self.client.post(f"/api/appointments/{first['id']}/cancel/")
        r = self._book(self.wednesday_9, child=self._other_child())
        self.assertEqual(201, r.status_code)

    def test_a_child_cannot_be_with_two_psychologists_at_once(self):
        AvailabilityBlock.objects.create(
            psychologist=self.other, weekday=2, start_time="09:00",
            end_time="12:00", capacity=2)
        self._book(self.wednesday_9)
        r = self._book(self.wednesday_9, psychologist=self.other)
        self.assertEqual(400, r.status_code)
        self.assertIn("child", str(r.data).lower())

    def test_a_psychologist_cannot_double_book_their_own_calendar(self):
        # Working outside the posted window is their call. Seeing two children
        # at once is not, so the override stops at the overlap rule.
        self._auth("p@racco1.gov.ph")
        self.client.post("/api/appointments/", {
            "child": self.child.id, "start": self.wednesday_9.isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json")
        r = self.client.post("/api/appointments/", {
            "child": self._other_child().id,
            "start": self.wednesday_9.isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json")
        self.assertEqual(400, r.status_code)


class AppointmentMustFitTheWindowTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self._auth("s@racco1.gov.ph")

    def test_an_appointment_may_not_overrun_the_end_of_the_window(self):
        # The block ends at 12:00. duration_minutes was accepted and then never
        # looked at, so a 60-minute session at 11:30 was allowed to run half an
        # hour past the end of the psychologist's day.
        r = self.client.post("/api/appointments/", {
            "child": self.child.id, "psychologist": self.psy.id,
            "start": next_weekday(2, 11).replace(minute=30).isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json")
        self.assertEqual(400, r.status_code)
        self.assertIn("window", str(r.data).lower())

    def test_an_appointment_that_fits_exactly_is_accepted(self):
        r = self.client.post("/api/appointments/", {
            "child": self.child.id, "psychologist": self.psy.id,
            "start": next_weekday(2, 11).isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json")
        self.assertEqual(201, r.status_code)


class ReschedulingIsStillBookingTests(SchedulingBase):
    """PATCH went straight to the model, so every rule was reachable around.

    perform_update was never overridden, which made the one code path a busy
    office uses most - moving an appointment - the one path with no checks.
    """

    def setUp(self):
        super().setUp()
        self._auth("s@racco1.gov.ph")
        self.appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=next_weekday(2, 9), duration_minutes=60, booked_by=self.staff)

    def _move(self, start, **extra):
        return self.client.patch(f"/api/appointments/{self.appointment.id}/",
                                 {"start": start.isoformat(), **extra}, format="json")

    def test_moving_inside_the_window_is_allowed(self):
        self.assertEqual(200, self._move(next_weekday(2, 10)).status_code)

    def test_moving_outside_the_availability_window_is_refused(self):
        self.assertEqual(400, self._move(next_weekday(2, 3)).status_code)

    def test_moving_into_the_past_is_refused(self):
        r = self._move(timezone.localtime() - timedelta(days=1))
        self.assertEqual(400, r.status_code)
        self.assertIn("past", str(r.data).lower())

    def test_moving_onto_another_appointment_is_refused(self):
        Appointment.objects.create(
            child=child_with_referral("Ben", self.psy),
            psychologist=self.psy, start=next_weekday(2, 10),
            duration_minutes=60, booked_by=self.staff)
        self.assertEqual(400, self._move(next_weekday(2, 10)).status_code)

    def test_an_appointment_does_not_collide_with_itself(self):
        # Moving 09:00 -> 09:00 with a longer duration must not read the row
        # being edited as a clash with itself.
        self.assertEqual(200, self._move(next_weekday(2, 9), duration_minutes=90).status_code)


class StatusTransitionTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self._auth("a@racco1.gov.ph")
        self.appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=next_weekday(2, 9), duration_minutes=60, booked_by=self.admin)

    def test_an_appointment_cannot_be_completed_before_it_has_started(self):
        r = self.client.post(f"/api/appointments/{self.appointment.id}/complete/")
        self.assertEqual(400, r.status_code)

    def test_a_past_appointment_can_be_completed(self):
        Appointment.objects.filter(pk=self.appointment.pk).update(
            start=timezone.now() - timedelta(hours=2))
        r = self.client.post(f"/api/appointments/{self.appointment.id}/complete/")
        self.assertEqual(200, r.status_code)

    def test_a_cancelled_appointment_cannot_be_marked_completed(self):
        self.client.post(f"/api/appointments/{self.appointment.id}/cancel/")
        Appointment.objects.filter(pk=self.appointment.pk).update(
            start=timezone.now() - timedelta(hours=2))
        r = self.client.post(f"/api/appointments/{self.appointment.id}/complete/")
        self.assertEqual(400, r.status_code)

    def test_cancelling_is_still_allowed_after_the_fact(self):
        # A no-show recorded late, or a session cancelled on the day. Refusing
        # this would push people to leave the calendar wrong instead.
        Appointment.objects.filter(pk=self.appointment.pk).update(
            start=timezone.now() - timedelta(hours=2))
        r = self.client.post(f"/api/appointments/{self.appointment.id}/cancel/")
        self.assertEqual(200, r.status_code)
