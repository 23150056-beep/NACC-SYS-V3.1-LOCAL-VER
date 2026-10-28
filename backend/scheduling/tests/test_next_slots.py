import datetime
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APITestCase
from accounts.models import Role
from children.tests.test_child_collab import make_user
from children.models import Child
from scheduling.models import AvailabilityBlock, Appointment, Unavailability
from scheduling.tests.test_api import give_referral


class NextSlotsTests(APITestCase):
    def setUp(self):
        self.staff = make_user("sl@t.ph", Role.STAFF)
        self.psych = make_user("pl@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(fullname="Slot Kid", assigned_psychologist=self.psych,
                                          social_worker=self.staff)
        give_referral(self.child)
        # The clock is pinned (Tue 10:30 Manila) so no test depends on when
        # the suite happens to run.
        pinned = timezone.make_aware(datetime.datetime(2026, 11, 3, 10, 30))
        patcher = patch("django.utils.timezone.now", return_value=pinned)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tomorrow = timezone.localdate() + datetime.timedelta(days=1)
        self.block = AvailabilityBlock.objects.create(
            psychologist=self.psych, weekday=self.tomorrow.weekday(),
            start_time=datetime.time(9), end_time=datetime.time(12), capacity=2)
        self.client.force_authenticate(self.staff)

    def _book(self, slot, child=None):
        child = child or self.child
        return self.client.post("/api/appointments/", {
            "child": child.id, "psychologist": self.psych.id,
            "start": f"{slot['date']}T{slot['start']}:00",
            "duration_minutes": 60, "purpose": "session", "notes": ""}, format="json")

    def _at(self, hour, **kw):
        start = timezone.make_aware(datetime.datetime.combine(self.tomorrow, datetime.time(hour)))
        other = Child.objects.create(fullname="Other Kid", social_worker=self.staff,
                                     assigned_psychologist=self.psych)
        give_referral(other)
        return Appointment.objects.create(child=other, psychologist=self.psych, start=start,
                                          duration_minutes=60, **kw)

    def test_returns_upcoming_slots(self):
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(len(r.data["slots"]), 1)
        first = r.data["slots"][0]
        self.assertEqual(first["date"], self.tomorrow.isoformat())
        self.assertEqual(first["start"], "09:00")
        self.assertNotIn("remaining", first)

    def test_a_booked_block_start_is_not_offered(self):
        """The reported bug: capacity left in the block, but 09:00 itself taken."""
        self._at(9)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        tomorrows = [s["start"] for s in r.data["slots"] if s["date"] == self.tomorrow.isoformat()]
        self.assertNotIn("09:00", tomorrows)

    def test_everything_it_offers_can_actually_be_booked(self):
        self._at(9)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertGreaterEqual(len(r.data["slots"]), 1)
        for slot in r.data["slots"]:
            c = Child.objects.create(fullname=f"K {slot['date']} {slot['start']}",
                                     social_worker=self.staff, assigned_psychologist=self.psych)
            give_referral(c)
            resp = self._book(slot, c)
            self.assertEqual(resp.status_code, 201, (slot, resp.data))
            # Each offer stands alone; undo it so the next is judged on its
            # own (a block's capacity is shared between the offers).
            Appointment.objects.filter(pk=resp.data["id"]).delete()

    def test_leave_covering_tomorrow_offers_nothing_tomorrow(self):
        Unavailability.objects.create(psychologist=self.psych, starts_on=self.tomorrow,
                                      ends_on=self.tomorrow)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertNotIn(self.tomorrow.isoformat(), [s["date"] for s in r.data["slots"]])

    def test_no_referral_offers_nothing_and_says_why(self):
        from clinical.models import CaseReferral
        CaseReferral.objects.filter(child=self.child).delete()
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual(r.data["slots"], [])
        self.assertTrue(r.data["reason"])

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

    def test_cancelled_appointment_does_not_hide_the_time(self):
        self._at(9, status=Appointment.CANCELLED)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        tomorrows = [x["start"] for x in r.data["slots"] if x["date"] == self.tomorrow.isoformat()]
        self.assertIn("09:00", tomorrows)

    def test_completed_appointment_still_occupies_its_time(self):
        self._at(9, status=Appointment.COMPLETED)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        tomorrows = [x["start"] for x in r.data["slots"] if x["date"] == self.tomorrow.isoformat()]
        self.assertNotIn("09:00", tomorrows)

    def test_fully_booked_day_excluded_but_next_occurrence_shown(self):
        for hour in (9, 10, 11):
            self._at(hour)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        dates = [s["date"] for s in r.data["slots"]]
        self.assertNotIn(self.tomorrow.isoformat(), dates)
        next_week = (self.tomorrow + datetime.timedelta(days=7)).isoformat()
        self.assertIn(next_week, dates)

    def test_a_time_already_past_today_is_not_offered(self):
        """The API refuses a start in the past (the clock is pinned at 10:30)."""
        today = timezone.localdate()
        AvailabilityBlock.objects.create(
            psychologist=self.psych, weekday=today.weekday(),
            start_time=datetime.time(0, 0), end_time=datetime.time(23, 59), capacity=2)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertEqual(r.status_code, 200)
        todays = [s for s in r.data["slots"] if s["date"] == today.isoformat()]
        self.assertTrue(all(s["start"] > "10:30" for s in todays), todays)

    def test_a_later_window_today_is_still_offered(self):
        today = timezone.localdate()
        AvailabilityBlock.objects.create(
            psychologist=self.psych, weekday=today.weekday(),
            start_time=datetime.time(14, 0), end_time=datetime.time(17, 0), capacity=2)
        r = self.client.get(f"/api/availability/next-slots/?child={self.child.id}")
        self.assertIn(today.isoformat(), [s["date"] for s in r.data["slots"]])
