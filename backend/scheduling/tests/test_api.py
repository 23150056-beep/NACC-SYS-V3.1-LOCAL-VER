from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import Child
from scheduling.models import AvailabilityBlock, Appointment

User = get_user_model()


def next_weekday(weekday, hour):
    """The next future datetime falling on `weekday` at `hour`:00 local."""
    now = timezone.localtime()
    days_ahead = (weekday - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


class SchedulingBase(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=self.admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=self.psy_role)
        self.other = User.objects.create_user(
            email="o@racco1.gov.ph", username="o", password="pass1234", role=self.psy_role)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=self.staff_role)
        self.child = Child.objects.create(
            fullname="Ana", case_type="Foster Care", assigned_psychologist=self.psy)
        # Wednesday 9:00-12:00, capacity 2
        self.block = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=2, start_time="09:00", end_time="12:00", capacity=2)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)


class AvailabilityTest(SchedulingBase):
    def test_psychologist_creates_own_block(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/availability/", {
            "weekday": 4, "start_time": "13:00", "end_time": "16:00", "capacity": 3}, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(AvailabilityBlock.objects.filter(psychologist=self.psy).count(), 2)

    def test_rejects_end_before_start(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/availability/", {
            "weekday": 4, "start_time": "16:00", "end_time": "13:00"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_requires_weekday_or_date(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/availability/", {
            "start_time": "09:00", "end_time": "10:00"}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_psychologist_cannot_edit_others_block(self):
        self._auth("o@racco1.gov.ph")
        resp = self.client.patch(f"/api/availability/{self.block.id}/", {"capacity": 5}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_staff_can_list(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.get(f"/api/availability/?psychologist={self.psy.id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    def test_rejects_overlapping_block_same_day(self):
        # setUp block: Wednesday 09:00-12:00 — a 10:00-14:00 window overlaps it.
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/availability/", {
            "weekday": 2, "start_time": "10:00", "end_time": "14:00", "capacity": 1}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Overlaps", str(resp.data))

    def test_adjacent_block_same_day_is_allowed(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/availability/", {
            "weekday": 2, "start_time": "12:00", "end_time": "15:00", "capacity": 1}, format="json")
        self.assertEqual(resp.status_code, 201)

    def test_same_window_on_other_day_is_allowed(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post("/api/availability/", {
            "weekday": 3, "start_time": "09:00", "end_time": "12:00", "capacity": 1}, format="json")
        self.assertEqual(resp.status_code, 201)

    def test_other_psychologist_may_share_the_window(self):
        self._auth("o@racco1.gov.ph")
        resp = self.client.post("/api/availability/", {
            "weekday": 2, "start_time": "09:00", "end_time": "12:00", "capacity": 2}, format="json")
        self.assertEqual(resp.status_code, 201)

    def test_edit_cannot_create_overlap(self):
        self._auth("p@racco1.gov.ph")
        other = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=2, start_time="13:00", end_time="15:00")
        resp = self.client.patch(f"/api/availability/{other.id}/",
                                 {"start_time": "11:00"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Overlaps", str(resp.data))

    def test_rejects_overlapping_dated_block(self):
        self._auth("p@racco1.gov.ph")
        first = self.client.post("/api/availability/", {
            "date": "2026-08-05", "start_time": "09:00", "end_time": "12:00"}, format="json")
        self.assertEqual(first.status_code, 201)
        resp = self.client.post("/api/availability/", {
            "date": "2026-08-05", "start_time": "11:00", "end_time": "13:00"}, format="json")
        self.assertEqual(resp.status_code, 400)


class BookingTest(SchedulingBase):
    def _book(self, start, psychologist=None):
        return self.client.post("/api/appointments/", {
            "child": self.child.id, "psychologist": psychologist or self.psy.id,
            "start": start.isoformat(), "purpose": "pre_assessment"}, format="json")

    def test_staff_books_inside_availability(self):
        self._auth("s@racco1.gov.ph")
        resp = self._book(next_weekday(2, 10))
        self.assertEqual(resp.status_code, 201)
        appt = Appointment.objects.get()
        self.assertEqual(appt.booked_by, self.staff)
        self.assertEqual(appt.status, "scheduled")

    def test_staff_cannot_book_outside_availability(self):
        self._auth("s@racco1.gov.ph")
        resp = self._book(next_weekday(3, 10))  # Thursday — no block
        self.assertEqual(resp.status_code, 400)
        self.assertIn("start", resp.data)

    def test_capacity_enforced(self):
        self._auth("s@racco1.gov.ph")
        day = next_weekday(2, 9)
        self.assertEqual(self._book(day).status_code, 201)
        self.assertEqual(self._book(day.replace(hour=10)).status_code, 201)
        full = self._book(day.replace(hour=11))
        self.assertEqual(full.status_code, 400)

    def test_psychologist_can_override_own_schedule(self):
        self._auth("p@racco1.gov.ph")
        resp = self._book(next_weekday(3, 15))  # outside any block, own calendar
        self.assertEqual(resp.status_code, 201)

    def test_past_booking_rejected(self):
        self._auth("s@racco1.gov.ph")
        resp = self._book(timezone.now() - timedelta(days=1))
        self.assertEqual(resp.status_code, 400)

    def test_status_actions(self):
        self._auth("s@racco1.gov.ph")
        aid = self._book(next_weekday(2, 10)).data["id"]
        # staff may cancel but not complete
        self.assertEqual(self.client.post(f"/api/appointments/{aid}/complete/").status_code, 403)
        # An outcome is a claim about something that happened, so it cannot be
        # recorded ahead of time - see test_booking_integrity. This test is
        # about WHO may record one, so put the session in the past first.
        Appointment.objects.filter(pk=aid).update(start=timezone.now() - timedelta(hours=2))
        # psychologist completes
        self._auth("p@racco1.gov.ph")
        resp = self.client.post(f"/api/appointments/{aid}/complete/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Appointment.objects.get().status, "completed")

    def test_psychologist_sees_only_own_appointments(self):
        Appointment.objects.create(child=self.child, psychologist=self.other,
                                   start=timezone.now() + timedelta(days=1))
        self._auth("p@racco1.gov.ph")
        self.assertEqual(len(self.client.get("/api/appointments/").data), 0)

    def test_monitoring_shows_next_session(self):
        self._auth("s@racco1.gov.ph")
        start = next_weekday(2, 10)
        self._book(start)
        self._auth("a@racco1.gov.ph")
        rows = self.client.get("/api/reports/monitoring/").data
        ana = next(r for r in rows if r["child_name"] == "Ana")
        self.assertIsNotNone(ana["next_session"])
        self.assertTrue(ana["next_session"].startswith(start.strftime("%Y-%m-%d")))
