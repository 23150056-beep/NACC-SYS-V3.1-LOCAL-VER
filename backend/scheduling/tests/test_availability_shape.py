"""What a window is worth knowing before you delete it.

Removing an availability window does not touch the sessions already booked
inside it - and it should not, because those were agreed with somebody. But
the screen asked "Remove this availability block?" and said nothing else, so
the one fact that decides the answer was the one fact missing: whether anybody
is booked in there.

`booked_ahead` puts it on the row. Future only, and cancelled ones do not
count, because neither is a reason to hesitate.
"""
from datetime import timedelta

from django.utils import timezone

from children.models import Child
from scheduling.models import Appointment, AvailabilityBlock
from scheduling.tests.test_api import (
    SchedulingBase, child_with_referral, next_weekday,
)


class BookedAheadTest(SchedulingBase):
    """SchedulingBase gives self.psy a Wednesday 09:00-12:00 window."""

    def setUp(self):
        super().setUp()
        self._auth("a@racco1.gov.ph")

    def _rows(self):
        return self.client.get("/api/availability/").data

    def _row_for(self, block):
        return next(r for r in self._rows() if r["id"] == block.id)

    def test_an_empty_window_says_zero(self):
        self.assertEqual(0, self._row_for(self.block)["booked_ahead"])

    def test_a_future_session_inside_the_window_counts(self):
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=next_weekday(2, 10), duration_minutes=60)
        self.assertEqual(1, self._row_for(self.block)["booked_ahead"])

    def test_a_session_outside_the_window_does_not(self):
        # 14:00 on a Wednesday is not in a 09:00-12:00 window.
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=next_weekday(2, 14), duration_minutes=60)
        self.assertEqual(0, self._row_for(self.block)["booked_ahead"])

    def test_a_session_on_another_weekday_does_not(self):
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=next_weekday(1, 10), duration_minutes=60)
        self.assertEqual(0, self._row_for(self.block)["booked_ahead"])

    def test_a_past_session_does_not(self):
        # Nothing to warn about: deleting the window cannot strand it.
        Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=timezone.now() - timedelta(days=7), duration_minutes=60)
        self.assertEqual(0, self._row_for(self.block)["booked_ahead"])

    def test_a_cancelled_session_does_not(self):
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=next_weekday(2, 10), duration_minutes=60,
                                   status=Appointment.CANCELLED)
        self.assertEqual(0, self._row_for(self.block)["booked_ahead"])

    def test_another_psychologists_session_does_not(self):
        AvailabilityBlock.objects.create(
            psychologist=self.other, weekday=2, start_time="09:00",
            end_time="12:00", capacity=2)
        Appointment.objects.create(child=self.child, psychologist=self.other,
                                   start=next_weekday(2, 10), duration_minutes=60)
        self.assertEqual(0, self._row_for(self.block)["booked_ahead"])

    def test_a_dated_window_counts_only_its_own_date(self):
        one_off = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=None,
            date=next_weekday(2, 10).date(), start_time="09:00",
            end_time="12:00", capacity=2)
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=next_weekday(2, 10), duration_minutes=60)
        other_child = child_with_referral("Ben", self.psy)
        Appointment.objects.create(child=other_child, psychologist=self.psy,
                                   start=next_weekday(2, 10) + timedelta(days=7),
                                   duration_minutes=60)
        self.assertEqual(1, self._row_for(one_off)["booked_ahead"])

    def test_removing_a_window_still_leaves_the_sessions_alone(self):
        """The count is a warning, not a cascade.

        Those sessions were agreed with somebody. Deleting the window is a
        statement about future bookings, and quietly cancelling appointments
        as a side effect of tidying a schedule would be far worse than the
        silence this replaces.
        """
        appt = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                          start=next_weekday(2, 10),
                                          duration_minutes=60)
        self.client.delete(f"/api/availability/{self.block.id}/")
        appt.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, appt.status)
