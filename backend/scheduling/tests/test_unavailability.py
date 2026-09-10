"""Saying when somebody is NOT there.

AvailabilityBlock can only express presence. There was no way to say a
psychologist is on leave next week, so the only way to express it was to
delete the weekday window and add it back afterwards - which loses the
capacity anyone had tuned, and silently strands every session already booked
into it. Leave, a training day and a court appearance are all the same shape:
a range of dates when this person is not seeing children.

Three places it has to bite, and only the first is obvious:

* the booking endpoint, so a session cannot be made inside it;
* the slot grid, which must offer nothing AND say leave is the reason -
  "fully booked" would send somebody to try Thursday;
* declaring leave OVER existing bookings, which must say how many are affected
  and change none of them. Those sessions were agreed with somebody, and
  cancelling them as a side effect of recording leave would be far worse than
  the gap this closes.

An absence is not a preference. A psychologist may book outside their own
posted hours - that override is theirs - but not on a day they are not there,
so this applies to every role including on their own calendar.
"""
from datetime import timedelta

from django.utils import timezone

from scheduling.models import Appointment, Unavailability
from scheduling.tests.test_api import SchedulingBase, child_with_referral, next_weekday


class LeaveBlocksBookingTest(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.when = next_weekday(2, 10)
        self._auth("s@racco1.gov.ph")

    def _leave(self, start=None, end=None, psychologist=None):
        day = (start or self.when).date()
        return Unavailability.objects.create(
            psychologist=psychologist or self.psy,
            starts_on=day, ends_on=(end.date() if end else day),
            reason="Annual leave")

    def _book(self, start=None, psychologist=None):
        return self.client.post("/api/appointments/", {
            "child": self.child.id,
            "psychologist": (psychologist or self.psy).id,
            "start": (start or self.when).isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json")

    def test_without_leave_the_booking_is_accepted(self):
        # The control. Everything below has to fail for the right reason.
        self.assertEqual(201, self._book().status_code)

    def test_a_session_inside_the_leave_is_refused(self):
        self._leave()
        response = self._book()
        self.assertEqual(400, response.status_code)
        self.assertIn("leave", str(response.data).lower())

    def test_the_refusal_names_the_dates(self):
        # "Not available" is what the availability window already says. This is
        # a different fact and should read like one.
        self._leave()
        self.assertIn("Sep", str(self._book().data))

    def test_a_session_on_the_last_day_is_refused_too(self):
        # Inclusive at both ends: leave "12th to the 16th" includes the 16th,
        # which is how anybody writing it down means it.
        self._leave(start=self.when - timedelta(days=2), end=self.when)
        self.assertEqual(400, self._book().status_code)

    def test_a_session_the_day_after_is_fine(self):
        self._leave(start=self.when - timedelta(days=2),
                    end=self.when - timedelta(days=1))
        self.assertEqual(201, self._book().status_code)

    def test_another_psychologists_leave_does_not_block_this_one(self):
        self._leave(psychologist=self.other)
        self.assertEqual(201, self._book().status_code)

    def test_a_psychologist_cannot_book_themselves_during_their_own_leave(self):
        # Working outside the posted window is their call. Being absent is not
        # a preference they can override.
        self._leave()
        self._auth("p@racco1.gov.ph")
        response = self.client.post("/api/appointments/", {
            "child": self.child.id, "start": self.when.isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json")
        self.assertEqual(400, response.status_code)

    def test_an_appointment_cannot_be_MOVED_into_leave(self):
        # Unlike the referral gate, which exempts moves: a referral arriving
        # late is paperwork catching up, but moving a session onto a day
        # somebody is away is simply wrong whenever it is done.
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=next_weekday(2, 9), duration_minutes=60, booked_by=self.staff)
        self._leave(start=next_weekday(2, 9))
        response = self.client.patch(
            f"/api/appointments/{appointment.id}/",
            {"start": next_weekday(2, 11).isoformat()}, format="json")
        self.assertEqual(400, response.status_code)


class TheGridSaysLeaveIsTheReasonTest(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.wednesday = next_weekday(2, 9).date()
        self._auth("s@racco1.gov.ph")

    def _slots(self):
        return self.client.get(
            f"/api/availability/slots/?psychologist={self.psy.id}"
            f"&date={self.wednesday}&child={self.child.id}").data

    def test_no_times_are_offered_during_leave(self):
        Unavailability.objects.create(psychologist=self.psy,
                                      starts_on=self.wednesday,
                                      ends_on=self.wednesday, reason="Training")
        self.assertEqual([], self._slots()["slots"])

    def test_the_reason_says_leave_rather_than_fully_booked(self):
        # "Fully booked on Wednesday" sends somebody to try Thursday. If the
        # person is away all week that is a wasted trip through the form.
        Unavailability.objects.create(psychologist=self.psy,
                                      starts_on=self.wednesday,
                                      ends_on=self.wednesday, reason="Training")
        reason = self._slots()["reason"].lower()
        self.assertIn("away", reason)
        self.assertNotIn("fully booked", reason)

    def test_times_come_back_when_the_leave_ends(self):
        Unavailability.objects.create(
            psychologist=self.psy,
            starts_on=self.wednesday - timedelta(days=3),
            ends_on=self.wednesday - timedelta(days=1), reason="Leave")
        self.assertTrue(self._slots()["slots"])


class DeclaringLeaveOverBookingsTest(SchedulingBase):
    """The part that matters. Those sessions were agreed with somebody."""

    def setUp(self):
        super().setUp()
        self._auth("a@racco1.gov.ph")
        self.when = next_weekday(2, 10)
        self.appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=self.when, duration_minutes=60, booked_by=self.admin)

    def _declare(self):
        return self.client.post("/api/unavailability/", {
            "psychologist": self.psy.id,
            "starts_on": self.when.date().isoformat(),
            "ends_on": self.when.date().isoformat(),
            "reason": "Annual leave"}, format="json")

    def test_leave_can_be_declared_over_them(self):
        # Refusing would be worse: the person IS away, and a calendar that
        # will not record it is a calendar that lies about who is in.
        self.assertEqual(201, self._declare().status_code, )

    def test_the_sessions_are_not_cancelled(self):
        self._declare()
        self.appointment.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, self.appointment.status)

    def test_the_row_says_how_many_are_caught_in_it(self):
        # So the screen can warn before, and show it after, rather than
        # leaving somebody to notice.
        response = self._declare()
        self.assertEqual(1, response.data["booked_during"])

    def test_a_cancelled_session_is_not_counted(self):
        self.appointment.status = Appointment.CANCELLED
        self.appointment.save(update_fields=["status"])
        self.assertEqual(0, self._declare().data["booked_during"])

    def test_a_past_session_is_not_counted(self):
        Appointment.objects.filter(pk=self.appointment.pk).update(
            start=timezone.now() - timedelta(days=30))
        self.assertEqual(0, self._declare().data["booked_during"])

    def test_somebody_elses_session_is_not_counted(self):
        Appointment.objects.create(
            child=child_with_referral("Ben", self.other), psychologist=self.other,
            start=self.when, duration_minutes=60, booked_by=self.admin)
        self.assertEqual(1, self._declare().data["booked_during"])


class WhoMayDeclareItTest(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.day = next_weekday(2, 9).date()

    def _declare(self, psychologist=None):
        return self.client.post("/api/unavailability/", {
            "psychologist": (psychologist or self.psy).id,
            "starts_on": self.day.isoformat(), "ends_on": self.day.isoformat(),
            "reason": "Leave"}, format="json")

    def test_a_psychologist_may_declare_their_own(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(201, self._declare().status_code)

    def test_a_psychologist_may_not_declare_a_colleagues(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(403, self._declare(psychologist=self.other).status_code)

    def test_an_administrator_may_declare_anybodys(self):
        self._auth("a@racco1.gov.ph")
        self.assertEqual(201, self._declare().status_code)

    def test_end_before_start_is_refused(self):
        self._auth("a@racco1.gov.ph")
        response = self.client.post("/api/unavailability/", {
            "psychologist": self.psy.id,
            "starts_on": self.day.isoformat(),
            "ends_on": (self.day - timedelta(days=1)).isoformat(),
            "reason": "Leave"}, format="json")
        self.assertEqual(400, response.status_code)
