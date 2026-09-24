"""Whose name a schedule shows (scheduling/visibility.py, 24 Sep 2026).

A social worker sees the name of a child they referred - the one whose latest
case referral they filed - and "C-0042 · Ref. E. Pascua" for everyone else's.
Administrators and psychologists see names. Held where the data leaves the
server: the appointments API, the Dashboard's "Today" strip, the assistant's
schedule answers and the booking refusal.
"""
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models import Role
from assistant import tools
from children.models import Child
from clinical.models import CaseReferral
from scheduling import booking
from scheduling.models import Appointment

User = get_user_model()


@override_settings(MEDIA_ROOT="/tmp/visibility-test-media")
class ScheduleNamesTest(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        self.editha = make("editha@t.ph", "Editha", "Pascua", Role.STAFF)
        self.rosa = make("rosa@t.ph", "Rosa", "Santos", Role.STAFF)
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)

        self.ana = Child.objects.create(first_name="Ana", last_name="Cruz",
                                        assigned_psychologist=self.psy)
        self.ben = Child.objects.create(first_name="Ben", last_name="Lim",
                                        assigned_psychologist=self.psy)
        self.cara = Child.objects.create(first_name="Cara", last_name="Diaz",
                                         assigned_psychologist=self.psy)
        self._refer(self.ana, self.editha)
        self._refer(self.ben, self.rosa)
        # Cara was booked before referrals were required: none on file.
        tomorrow = timezone.localdate() + timedelta(days=1)
        for hour, child in ((9, self.ana), (10, self.ben), (11, self.cara)):
            Appointment.objects.create(
                child=child, psychologist=self.psy,
                start=timezone.make_aware(datetime.combine(tomorrow, time(hour))))

    def _refer(self, child, by):
        ref = CaseReferral(child=child, uploaded_by=by, original_filename="referral.pdf")
        ref.file.save("referral.pdf", ContentFile(b"%PDF-1.4"), save=True)
        return ref

    def _rows(self, user):
        client = APIClient()
        client.force_authenticate(user)
        rows = client.get("/api/appointments/").data
        return {r["child"]: r for r in rows}

    def test_a_social_worker_sees_only_the_children_they_referred(self):
        rows = self._rows(self.editha)
        self.assertEqual("Ana Cruz", rows[self.ana.id]["child_name"])
        self.assertFalse(rows[self.ana.id]["name_hidden"])
        for child in (self.ben, self.cara):
            self.assertIsNone(rows[child.id]["child_name"])
            self.assertTrue(rows[child.id]["name_hidden"])
            self.assertEqual(f"C-{child.id:04d}", rows[child.id]["case_ref"])
        self.assertEqual("Rosa Santos", rows[self.ben.id]["referred_by_name"])
        self.assertIsNone(rows[self.cara.id]["referred_by_name"])

    def test_the_name_is_not_anywhere_in_the_response(self):
        client = APIClient()
        client.force_authenticate(self.editha)
        body = client.get("/api/appointments/").content.decode()
        self.assertNotIn("Ben Lim", body)
        self.assertNotIn("Cara Diaz", body)

    def test_each_social_worker_sees_their_own(self):
        rows = self._rows(self.rosa)
        self.assertEqual("Ben Lim", rows[self.ben.id]["child_name"])
        self.assertIsNone(rows[self.ana.id]["child_name"])
        self.assertEqual("Editha Pascua", rows[self.ana.id]["referred_by_name"])

    def test_administrators_and_psychologists_see_names(self):
        for user in (self.admin, self.psy):
            rows = self._rows(user)
            self.assertEqual({"Ana Cruz", "Ben Lim", "Cara Diaz"},
                             {r["child_name"] for r in rows.values()}, user.email)
            self.assertFalse(any(r["name_hidden"] for r in rows.values()))

    def test_the_latest_referral_decides(self):
        # Replacing a referral files a new one; whoever filed that is the referrer.
        later = self._refer(self.ana, self.rosa)
        CaseReferral.objects.filter(pk=later.pk).update(
            created_at=timezone.now() + timedelta(minutes=5))
        self.assertIsNone(self._rows(self.editha)[self.ana.id]["child_name"])
        self.assertEqual("Ana Cruz", self._rows(self.rosa)[self.ana.id]["child_name"])

    def test_a_booking_answers_the_same_way(self):
        client = APIClient()
        client.force_authenticate(self.editha)
        day = timezone.localdate() + timedelta(days=2)
        from scheduling.models import AvailabilityBlock
        AvailabilityBlock.objects.create(psychologist=self.psy, weekday=day.weekday(),
                                         start_time=time(8), end_time=time(17), capacity=3)
        r = client.post("/api/appointments/", {
            "child": self.ben.id, "psychologist": self.psy.id,
            "start": f"{day.isoformat()}T09:00:00", "duration_minutes": 60,
            "purpose": "session"}, format="json")
        self.assertEqual(201, r.status_code, r.data)
        self.assertIsNone(r.data["child_name"])
        self.assertEqual(f"C-{self.ben.id:04d}", r.data["case_ref"])

    def test_the_today_strip_follows_the_rule(self):
        today = timezone.localdate()
        Appointment.objects.update(start=timezone.make_aware(datetime.combine(today, time(23, 30))))
        client = APIClient()
        client.force_authenticate(self.editha)
        strip = {s["child_id"]: s for s in
                 client.get("/api/reports/dashboard/").data["today_schedule"]}
        self.assertEqual("Ana Cruz", strip[self.ana.id]["child_name"])
        self.assertIsNone(strip[self.ben.id]["child_name"])
        self.assertEqual("Rosa Santos", strip[self.ben.id]["referred_by_name"])

    def test_the_assistant_follows_the_rule(self):
        req = APIRequestFactory().get("/api/assistant/ask/")
        req.user = self.editha
        out = tools.REGISTRY["list_my_appointments"]["resolve"](req, {"when": "tomorrow"})
        said = {item["child"] for item in out["items"]}
        self.assertIn("Ana Cruz", said)
        self.assertIn(f"C-{self.ben.id:04d} (referred by Rosa Santos)", said)
        self.assertIn(f"C-{self.cara.id:04d} (no referral on file)", said)
        self.assertFalse(any("Ben Lim" in s or "Cara Diaz" in s for s in said))

    def test_a_refused_booking_does_not_name_the_other_child(self):
        taken = Appointment.objects.get(child=self.ben)
        errors = booking.errors_for(child=self.ana, psychologist=self.psy,
                                    start=taken.start, duration_minutes=60,
                                    own_calendar=True)
        message = " ".join(str(v) for v in errors.values())
        self.assertIn(f"C-{self.ben.id:04d}", message)
        self.assertNotIn("Ben Lim", message)

    def test_the_rule_costs_no_query_per_row(self):
        client = APIClient()
        client.force_authenticate(self.editha)
        with CaptureQueriesContext(connection) as few:
            client.get("/api/appointments/")
        for i in range(6):
            child = Child.objects.create(first_name=f"K{i}", last_name="Extra",
                                         assigned_psychologist=self.psy)
            self._refer(child, self.rosa)
            Appointment.objects.create(child=child, psychologist=self.psy,
                                       start=timezone.now() + timedelta(days=3, hours=i))
        with CaptureQueriesContext(connection) as many:
            client.get("/api/appointments/")
        self.assertEqual(len(few.captured_queries), len(many.captured_queries))
