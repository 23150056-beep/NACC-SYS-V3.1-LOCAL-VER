"""Whose name a schedule shows (scheduling/visibility.py, 24 Sep 2026).

A social worker sees the name of a child in their own records - the one they
hold as `social_worker` - and "C-0042 · Ref. E. Pascua" for everyone else's.
Administrators and psychologists see names. Held where the data leaves the
server: the appointments API, the assistant's schedule answers and the
booking refusal. The Dashboard strip and the assistant answer a social worker
about their own children only, so they name every one.
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
from scheduling import booking, visibility
from scheduling.models import Appointment, AvailabilityBlock

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
                                        assigned_psychologist=self.psy, social_worker=self.editha)
        self.ben = Child.objects.create(first_name="Ben", last_name="Lim",
                                        assigned_psychologist=self.psy, social_worker=self.rosa)
        # Cara has no social worker yet: only the ISA holds her record.
        self.cara = Child.objects.create(first_name="Cara", last_name="Diaz",
                                         assigned_psychologist=self.psy)
        for child in (self.ana, self.ben, self.cara):
            self._refer(child, child.social_worker)
        tomorrow = timezone.localdate() + timedelta(days=1)
        for hour, child in ((9, self.ana), (10, self.ben), (11, self.cara)):
            Appointment.objects.create(
                child=child, psychologist=self.psy,
                start=timezone.make_aware(datetime.combine(tomorrow, time(hour))))

    def _refer(self, child, by):
        ref = CaseReferral(child=child, uploaded_by=by, original_filename="referral.pdf")
        ref.file.save("referral.pdf", ContentFile(b"%PDF-1.4"), save=True)
        return ref

    def _client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _rows(self, user):
        return {r["child"]: r for r in self._client(user).get("/api/appointments/").data}

    def test_a_social_worker_sees_names_only_for_their_own_records(self):
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
        body = self._client(self.editha).get("/api/appointments/").content.decode()
        self.assertNotIn("Ben Lim", body)
        self.assertNotIn("Cara Diaz", body)

    def test_another_workers_session_carries_no_notes(self):
        # Free text is the name by another route: "bring Ben's school records".
        Appointment.objects.filter(child=self.ben).update(notes="Bring Ben Lim's school records")
        Appointment.objects.filter(child=self.ana).update(notes="Ana prefers mornings")
        rows = self._rows(self.editha)
        self.assertEqual("", rows[self.ben.id]["notes"])
        self.assertEqual("Ana prefers mornings", rows[self.ana.id]["notes"])
        self.assertEqual("Bring Ben Lim's school records", self._rows(self.rosa)[self.ben.id]["notes"])
        self.assertNotIn("Ben", self._client(self.editha).get("/api/appointments/").content.decode())

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

    def test_the_record_decides_not_the_referral(self):
        # A later referral filed by somebody else does not move the name: the
        # record is still Editha's until the ISA moves it.
        later = self._refer(self.ana, self.admin)
        CaseReferral.objects.filter(pk=later.pk).update(
            created_at=timezone.now() + timedelta(minutes=5))
        self.assertEqual("Ana Cruz", self._rows(self.editha)[self.ana.id]["child_name"])
        Child.objects.filter(pk=self.ana.pk).update(social_worker=self.rosa)
        self.assertIsNone(self._rows(self.editha)[self.ana.id]["child_name"])
        self.assertEqual("Ana Cruz", self._rows(self.rosa)[self.ana.id]["child_name"])

    def test_a_booking_answers_the_same_way(self):
        day = timezone.localdate() + timedelta(days=2)
        AvailabilityBlock.objects.create(psychologist=self.psy, weekday=day.weekday(),
                                         start_time=time(8), end_time=time(17), capacity=3)
        payload = {"child": self.ben.id, "psychologist": self.psy.id,
                   "start": f"{day.isoformat()}T09:00:00", "duration_minutes": 60,
                   "purpose": "session"}
        r = self._client(self.admin).post("/api/appointments/", payload, format="json")
        self.assertEqual(201, r.status_code, r.data)
        self.assertEqual("Ben Lim", r.data["child_name"])
        mine = self._client(self.editha).post(
            "/api/appointments/", {**payload, "child": self.ana.id,
                                   "start": f"{day.isoformat()}T11:00:00"}, format="json")
        self.assertEqual(201, mine.status_code, mine.data)
        self.assertEqual("Ana Cruz", mine.data["child_name"])

    def test_the_today_strip_is_a_social_workers_own_children(self):
        today = timezone.localdate()
        Appointment.objects.update(start=timezone.make_aware(datetime.combine(today, time(23, 30))))
        strip = {s["child_id"]: s for s in
                 self._client(self.editha).get("/api/reports/dashboard/").data["today_schedule"]}
        self.assertEqual({self.ana.id}, set(strip))
        self.assertEqual("Ana Cruz", strip[self.ana.id]["child_name"])
        admin_strip = self._client(self.admin).get("/api/reports/dashboard/").data["today_schedule"]
        self.assertEqual(3, len(admin_strip))

    def test_the_assistant_answers_about_their_own_children(self):
        req = APIRequestFactory().get("/api/assistant/ask/")
        req.user = self.editha
        out = tools.REGISTRY["list_my_appointments"]["resolve"](req, {"when": "tomorrow"})
        self.assertEqual(["Ana Cruz"], [item["child"] for item in out["items"]])
        self.assertEqual(1, out["total"])

    def test_the_one_line_label(self):
        self.assertEqual("Ana Cruz", visibility.label(self.editha, self.ana))
        self.assertEqual(f"C-{self.ben.id:04d} (referred by Rosa Santos)",
                         visibility.label(self.editha, self.ben))
        self.assertEqual(f"C-{self.cara.id:04d} (no social worker yet)",
                         visibility.label(self.editha, self.cara))

    def test_a_refused_booking_does_not_name_the_other_child(self):
        taken = Appointment.objects.get(child=self.ben)
        errors = booking.errors_for(child=self.ana, psychologist=self.psy,
                                    start=taken.start, duration_minutes=60,
                                    own_calendar=True)
        message = " ".join(str(v) for v in errors.values())
        self.assertIn(f"C-{self.ben.id:04d}", message)
        self.assertNotIn("Ben Lim", message)

    def test_the_rule_costs_no_query_per_row(self):
        client = self._client(self.editha)
        with CaptureQueriesContext(connection) as few:
            client.get("/api/appointments/")
        for i in range(6):
            child = Child.objects.create(first_name=f"K{i}", last_name="Extra",
                                         assigned_psychologist=self.psy,
                                         social_worker=self.rosa if i % 2 else self.editha)
            Appointment.objects.create(child=child, psychologist=self.psy,
                                       start=timezone.now() + timedelta(days=3, hours=i))
        with CaptureQueriesContext(connection) as many:
            client.get("/api/appointments/")
        self.assertEqual(len(few.captured_queries), len(many.captured_queries))
