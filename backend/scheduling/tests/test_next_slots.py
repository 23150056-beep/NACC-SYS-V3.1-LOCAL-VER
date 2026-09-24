import datetime
from django.utils import timezone
from rest_framework.test import APITestCase
from accounts.models import Role
from children.tests.test_child_collab import make_user
from children.models import Child
from scheduling.models import AvailabilityBlock, Appointment


class NextSlotsTests(APITestCase):
    def setUp(self):
        self.staff = make_user("sl@t.ph", Role.STAFF)
        self.psych = make_user("pl@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(fullname="Slot Kid", assigned_psychologist=self.psych,
                                          social_worker=self.staff)
        self.tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        self.block = AvailabilityBlock.objects.create(
            psychologist=self.psych, weekday=self.tomorrow.weekday(),
            start_time=datetime.time(9), end_time=datetime.time(12), capacity=2)
        self.client.force_authenticate(self.staff)

    def test_returns_upcoming_slots(self):
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.data["slots"]), 1)
        self.assertEqual(r.data["slots"][0]["remaining"], 2)

    def test_unassigned_child_400(self):
        solo = Child.objects.create(fullname="No Psych", social_worker=self.staff)
        r = self.client.get(f"/api/availability/next-slots/?child={solo.id}")
        self.assertEqual(r.status_code, 400)

    def test_response_prefers_the_psychologists_real_name(self):
        self.psych.first_name, self.psych.last_name = "Paz", "Cruz"
        self.psych.save()
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual("Paz Cruz", r.data["psychologist"])

    def test_response_falls_back_to_the_username(self):
        self.assertEqual("", self.psych.fullname)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual(self.psych.username, r.data["psychologist"])

    def test_response_never_carries_an_email_address(self):
        """This used to fall back to get_username(), which on this model is
        the email - so a psychologist with no name had their address handed
        to anyone who could see the child. The previous version of this test
        asserted `fullname or email`, which is how it went unnoticed: it
        restated the implementation instead of saying what was wanted.
        See accounts/display.py."""
        self.assertEqual("", self.psych.fullname, "the case that mattered")
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertNotIn("@", r.data["psychologist"])

    def test_unknown_child_400(self):
        r = self.client.get("/api/availability/next-slots/?child=999999")
        self.assertEqual(r.status_code, 400)

    def test_unassigned_psychologist_gets_404(self):
        # A psychologist who isn't assigned to this child must not be able
        # to learn another psychologist's assigned-child name or their
        # availability windows via this endpoint.
        other_psych = make_user("op@t.ph", Role.PSYCHOLOGIST)
        self.client.force_authenticate(other_psych)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual(r.status_code, 404)

    def test_assigned_psychologist_can_query_own_child(self):
        self.client.force_authenticate(self.psych)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual(r.status_code, 200)

    def test_cancelled_appointment_does_not_reduce_remaining(self):
        # Mirrors _validate_booking: only CANCELLED is excluded from the count.
        start = timezone.make_aware(datetime.datetime.combine(self.tomorrow, datetime.time(9)))
        Appointment.objects.create(
            child=self.child, psychologist=self.psych, start=start,
            status=Appointment.CANCELLED)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        first = next(s for s in r.data["slots"] if s["date"] == self.tomorrow.isoformat())
        self.assertEqual(first["remaining"], 2)

    def test_completed_appointment_reduces_remaining(self):
        # Mirrors _validate_booking: non-cancelled statuses (including completed
        # and no_show) still occupy capacity.
        start = timezone.make_aware(datetime.datetime.combine(self.tomorrow, datetime.time(9)))
        Appointment.objects.create(
            child=self.child, psychologist=self.psych, start=start,
            status=Appointment.COMPLETED)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        first = next(s for s in r.data["slots"] if s["date"] == self.tomorrow.isoformat())
        self.assertEqual(first["remaining"], 1)

    def test_fully_booked_day_excluded_but_next_occurrence_shown(self):
        start = timezone.make_aware(datetime.datetime.combine(self.tomorrow, datetime.time(9)))
        Appointment.objects.create(child=self.child, psychologist=self.psych, start=start)
        Appointment.objects.create(
            child=self.child, psychologist=self.psych, start=start.replace(hour=10))
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        dates = [s["date"] for s in r.data["slots"]]
        self.assertNotIn(self.tomorrow.isoformat(), dates)
        next_week = (self.tomorrow + datetime.timedelta(days=7)).isoformat()
        self.assertIn(next_week, dates)

    def test_todays_window_is_dropped_once_its_start_time_has_passed(self):
        """A slot is booked at its own `start` — both screens fill the booking
        form straight from it — and the API refuses a start in the past. So a
        window that has already begun today is a dead end, not an opening."""
        now = timezone.localtime()
        if now.hour == 0 and now.minute == 0:
            self.skipTest("Needs part of today already elapsed.")
        today = timezone.localdate()
        AvailabilityBlock.objects.create(
            psychologist=self.psych, weekday=today.weekday(),
            start_time=datetime.time(0, 0), end_time=datetime.time(23, 59), capacity=2)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual(r.status_code, 200)
        todays = [s for s in r.data["slots"] if s["date"] == today.isoformat()]
        self.assertEqual(todays, [])

    def test_todays_window_is_still_offered_before_it_starts(self):
        """The guard is about windows that have STARTED, not about today."""
        now = timezone.localtime()
        if now.hour >= 22:
            self.skipTest("No room left in today for a still-future window.")
        today = timezone.localdate()
        AvailabilityBlock.objects.create(
            psychologist=self.psych, weekday=today.weekday(),
            start_time=datetime.time(now.hour + 1, 0),
            end_time=datetime.time(23, 59), capacity=2)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        dates = [s["date"] for s in r.data["slots"]]
        self.assertIn(today.isoformat(), dates)
