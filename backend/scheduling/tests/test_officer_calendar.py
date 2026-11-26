"""The RACC Officer on the calendar (WP3 of the Officer plan).

The Officer books, moves and cancels a session for ANY child - one another
worker holds, one nobody holds - inside availability, as a social worker would
for their own; the psychologist and the record's social worker hear of it as
they do when the ISA acts. It never records an outcome (completed, no-show: the
psychologist's, and the ISA's), never writes availability or leave (the ISA for
anybody, a psychologist for themselves), and reads the agency's calendar with
the children's names.

The fixture is session-mail's: a social worker (`sw`) holding a child, another
(`other_sw`), the ISA, two psychologists working Wednesday mornings, an Officer
holding nothing, and the clock pinned to Monday 19 Oct 2026, 10:00 in Manila.
"""
import json
from datetime import datetime, timedelta

from django.utils import timezone

from children.models import Child
from scheduling.models import Appointment, AvailabilityBlock, Unavailability
from scheduling.tests import test_session_mail as mail
from scheduling.tests.test_api import give_referral


class OfficerBooksAnyChildTest(mail.SessionMailBase):
    def test_books_for_a_child_another_worker_holds(self):
        with self.outbox() as sent:
            res = self.book(self.officer)
        self.assertEqual(201, res.status_code, res.data)
        appt = Appointment.objects.get(pk=res.data["id"])
        self.assertEqual((self.child, self.psy, self.officer),
                         (appt.child, appt.psychologist, appt.booked_by))
        # As when the ISA books: the psychologist (with the invite) and the
        # record's social worker are told, the Officer is not.
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        self.assertEqual([], self.to(sent, self.officer))
        self.assertIn("attachment", self.one(sent, self.psy))
        self.assertEqual("Corazon Dizon booked a session for case " + self.ref,
                         self.one(sent, self.sw)["subject"])
        # The child is still the social worker's.
        self.child.refresh_from_db()
        self.assertEqual(self.sw, self.child.social_worker)

    def test_books_for_a_child_nobody_holds(self):
        child = self._child(None, "Cara", "Diaz")
        res = self.book(self.officer, child=child)
        self.assertEqual(201, res.status_code, res.data)

    def test_is_held_to_the_availability_window_like_a_social_worker(self):
        outside = self.start.replace(hour=15)
        res = self.book(self.officer, start=outside)
        self.assertEqual(400, res.status_code, res.data)
        # The same refusal a social worker gets for their own child.
        sw = self.book(self.sw, start=outside)
        self.assertEqual(400, sw.status_code, sw.data)
        self.assertEqual(sw.data, res.data)
        self.assertFalse(Appointment.objects.exists())

    def test_is_held_to_the_referral_gate(self):
        child = Child.objects.create(
            first_name="Dino", last_name="Reyes", fullname="Dino Reyes",
            birth_date=mail.BORN, social_worker=self.other_sw, assigned_psychologist=self.psy)
        res = self.book(self.officer, child=child)
        self.assertEqual(400, res.status_code, res.data)
        self.assertIn("referral", json.dumps(res.data).lower())
        self.assertFalse(Appointment.objects.filter(child=child).exists())

    def test_moves_and_cancels_a_session_on_another_workers_child(self):
        appt, _ = self.booked(self.sw)
        with self.outbox() as sent:
            moved = self.change(self.officer, appt, start=self.later())
        self.assertEqual(200, moved.status_code, moved.data)
        appt.refresh_from_db()
        self.assertEqual(self.later(), appt.start)
        self.assertEqual({"my_calendar", "child_sessions"}, {k for k, _ in sent})
        with self.outbox():
            cancelled = self.cancel(self.officer, appt)
        self.assertEqual(200, cancelled.status_code, cancelled.data)
        appt.refresh_from_db()
        self.assertEqual(Appointment.CANCELLED, appt.status)

    def test_moves_and_cancels_a_session_on_a_child_nobody_holds(self):
        child = self._child(None, "Cara", "Diaz")
        appt, _ = self.booked(self.officer, child=child)
        moved = self.change(self.officer, appt, start=self.later())
        self.assertEqual(200, moved.status_code, moved.data)
        cancelled = self.cancel(self.officer, appt)
        self.assertEqual(200, cancelled.status_code, cancelled.data)

    def test_a_move_outside_availability_is_refused_for_the_officer_too(self):
        appt, _ = self.booked(self.sw)
        res = self.change(self.officer, appt, start=self.start.replace(hour=16))
        self.assertEqual(400, res.status_code, res.data)
        appt.refresh_from_db()
        self.assertEqual(self.start, appt.start)


class OfficerNeverRecordsAnOutcomeTest(mail.SessionMailBase):
    def setUp(self):
        super().setUp()
        self.past = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=mail.NOW - timedelta(days=2), status=Appointment.SCHEDULED)

    def test_completed_and_no_show_are_refused(self):
        for verb in ("complete", "no_show"):
            res = self._as(self.officer).post(f"/api/appointments/{self.past.pk}/{verb}/")
            self.assertEqual(403, res.status_code, verb)
        self.past.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, self.past.status)

    def test_the_psychologist_who_holds_it_still_may(self):
        res = self._as(self.psy).post(f"/api/appointments/{self.past.pk}/complete/")
        self.assertEqual(200, res.status_code, res.data)

    def test_cancel_is_the_officers_only_status_change(self):
        res = self._as(self.officer).post(f"/api/appointments/{self.past.pk}/cancel/")
        self.assertEqual(200, res.status_code, res.data)


class OfficerReadsTheAgencysCalendarTest(mail.SessionMailBase):
    def test_sees_every_session_with_the_childs_name(self):
        mine, _ = self.booked(self.sw)
        other = self._child(self.other_sw, "Ben", "Lim")
        theirs, _ = self.booked(self.other_sw, child=other, start=self.later(1))
        rows = {a["id"]: a for a in self._as(self.officer).get("/api/appointments/").data}
        self.assertEqual({mine.pk, theirs.pk}, set(rows))
        for row in rows.values():
            self.assertFalse(row["name_hidden"])
            self.assertTrue(row["child_name"])
        self.assertEqual("Ben Lim", rows[theirs.pk]["child_name"])
        # A social worker, by contrast, is shown a case reference.
        masked = {a["id"]: a for a in self._as(self.sw).get("/api/appointments/").data}
        self.assertTrue(masked[theirs.pk]["name_hidden"])
        self.assertIsNone(masked[theirs.pk]["child_name"])

    def test_the_dashboard_carries_the_agencys_sessions_and_availability_today(self):
        # A Wednesday, so the window is the day's; the clock is moved there.
        from unittest.mock import patch
        wednesday = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.get_current_timezone())
        other = self._child(self.other_sw, "Ben", "Lim")
        for child, hour in ((self.child, 9), (other, 10)):
            Appointment.objects.create(
                child=child, psychologist=self.psy,
                start=timezone.make_aware(datetime(2026, 10, 21, hour, 0)))
        with patch("django.utils.timezone.now", return_value=wednesday):
            officer = self._as(self.officer).get("/api/reports/dashboard/").data
            sw = self._as(self.sw).get("/api/reports/dashboard/").data
        self.assertEqual(2, len(officer["today_schedule"]))
        self.assertEqual(1, len(sw["today_schedule"]))
        self.assertTrue(officer["availability_today"])
        self.assertEqual({"Ben Lim", "Zyxwanita Qorvath"},
                         {row["child_name"] for row in officer["today_schedule"]})

    def test_the_slot_grid_the_month_and_the_openings_answer_for_any_child(self):
        client = self._as(self.officer)
        day = self.start.date().isoformat()
        self.assertEqual(200, client.get("/api/availability/slots/", {
            "psychologist": self.psy.pk, "date": day, "child": self.child.pk}).status_code)
        self.assertEqual(200, client.get("/api/availability/month/", {
            "psychologist": self.psy.pk, "month": "2026-10", "child": self.child.pk}).status_code)
        self.assertEqual(200, client.get("/api/availability/next-slots/", {
            "child": self.child.pk}).status_code)
        self.assertEqual(200, client.get("/api/availability/openings/", {
            "child": self.child.pk}).status_code)
        # ... and for a child nobody holds.
        orphan = self._child(None, "Cara", "Diaz")
        for url, extra in (("slots", {"date": day}), ("month", {"month": "2026-10"})):
            self.assertEqual(200, client.get(f"/api/availability/{url}/", {
                "psychologist": self.psy.pk, "child": orphan.pk, **extra}).status_code, url)
        for url in ("next-slots", "openings"):
            self.assertEqual(200, client.get(f"/api/availability/{url}/", {
                "child": orphan.pk}).status_code, url)

    def test_the_psychologist_picker_answers_with_states_and_reasons(self):
        res = self._as(self.officer).get("/api/availability/psychologists/",
                                         {"child": self.child.pk})
        self.assertEqual(200, res.status_code, res.data)
        self.assertFalse(res.data.get("blocked"))
        states = {row["id"]: row["state"] for row in res.data["psychologists"]}
        self.assertEqual({self.psy.pk, self.psy2.pk}, set(states))
        self.assertEqual({"open"}, set(states.values()))
        # An Officer sees the same picker for a child it does not hold; the
        # social worker for a colleague's child is told it does not exist.
        self.assertEqual(404, self._as(self.other_sw).get(
            "/api/availability/psychologists/", {"child": self.child.pk}).status_code)


class OfficerNeverWritesAvailabilityOrLeaveTest(mail.SessionMailBase):
    """Decision 5: the ISA for anybody, a psychologist for themselves."""

    def setUp(self):
        super().setUp()
        self.block = AvailabilityBlock.objects.filter(psychologist=self.psy).first()
        self.leave = Unavailability.objects.create(
            psychologist=self.psy, starts_on=self.start.date() + timedelta(days=7),
            ends_on=self.start.date() + timedelta(days=8), reason="Away")

    def test_every_write_is_refused_with_a_complete_payload(self):
        client = self._as(self.officer)
        day = (self.start.date() + timedelta(days=3)).isoformat()
        responses = {
            "create window": client.post("/api/availability/", {
                "psychologist": self.psy.pk, "weekday": 3, "start_time": "09:00",
                "end_time": "12:00", "capacity": 2}, format="json"),
            "edit window": client.patch(f"/api/availability/{self.block.pk}/",
                                        {"capacity": 9}, format="json"),
            "delete window": client.delete(f"/api/availability/{self.block.pk}/"),
            "combine": client.post("/api/availability/combine/",
                                   {"ids": [self.block.pk, self.block.pk + 1]}, format="json"),
            "create leave": client.post("/api/unavailability/", {
                "psychologist": self.psy.pk, "starts_on": day, "ends_on": day,
                "reason": "x"}, format="json"),
            "edit leave": client.patch(f"/api/unavailability/{self.leave.pk}/",
                                       {"reason": "y"}, format="json"),
            "delete leave": client.delete(f"/api/unavailability/{self.leave.pk}/"),
        }
        for name, res in responses.items():
            self.assertEqual(403, res.status_code, f"{name}: {res.status_code} {res.data}")
        self.block.refresh_from_db()
        self.assertEqual(4, self.block.capacity)
        self.assertTrue(Unavailability.objects.filter(pk=self.leave.pk).exists())

    def test_it_can_read_both(self):
        client = self._as(self.officer)
        self.assertEqual(200, client.get("/api/availability/").status_code)
        self.assertEqual(200, client.get("/api/unavailability/").status_code)
        self.assertTrue(client.get("/api/unavailability/").data)
