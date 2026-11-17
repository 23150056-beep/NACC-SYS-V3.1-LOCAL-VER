"""Appointment status has one door: the table, enforced by every endpoint."""
from datetime import timedelta
from unittest.mock import patch

from django.utils import timezone

from config.lifecycle import Lifecycle
from scheduling.lifecycle import APPOINTMENT_LIFECYCLE
from scheduling.models import Appointment
from scheduling.tests.test_api import SchedulingBase, child_with_referral, next_weekday

ACTIONS = {"completed": "complete", "no_show": "no_show", "cancelled": "cancel"}


class AppointmentLifecycleTest(SchedulingBase):
    def _appt(self, status="scheduled", past=True, child=None):
        start = timezone.now() - timedelta(days=2) if past else next_weekday(2, 10)
        return Appointment.objects.create(
            child=child or self.child, psychologist=self.psy, start=start,
            duration_minutes=60, status=status)

    def _do(self, appt, to):
        return self.client.post(f"/api/appointments/{appt.id}/{ACTIONS[to]}/")

    def test_table_matches_the_model(self):
        self.assertEqual([], APPOINTMENT_LIFECYCLE.check_table())
        self.assertIsInstance(APPOINTMENT_LIFECYCLE, Lifecycle)

    def test_every_cell_of_the_table(self):
        self._auth("p@racco1.gov.ph")
        for frm in APPOINTMENT_LIFECYCLE.states:
            for to in ACTIONS:
                with self.subTest(frm=frm, to=to):
                    a = self._appt(frm)
                    r = self._do(a, to)
                    a.refresh_from_db()
                    if APPOINTMENT_LIFECYCLE.allows(frm, to):
                        self.assertEqual(200, r.status_code, r.data)
                        self.assertEqual(to, a.status)
                    else:
                        self.assertEqual(400, r.status_code)
                        self.assertEqual(frm, a.status)
                        if frm == "cancelled" and to != "cancelled":
                            self.assertIn("Book a new one", r.data["detail"])
                        else:
                            self.assertTrue(r.data["detail"].startswith(
                                "This appointment is already"))

    def test_cancelling_a_cancelled_one_is_refused(self):
        self._auth("p@racco1.gov.ph")
        r = self._do(self._appt("cancelled"), "cancelled")
        self.assertEqual(400, r.status_code)
        self.assertEqual("This appointment is already cancelled.", r.data["detail"])

    def test_staff_cannot_erase_a_recorded_outcome(self):
        self._auth("s@racco1.gov.ph")
        for outcome in ("completed", "no_show"):
            a = self._appt(outcome)
            r = self._do(a, "cancelled")
            self.assertEqual(403, r.status_code)
            self.assertEqual("Only the psychologist can change a recorded outcome.",
                             r.data["detail"])
            a.refresh_from_db()
            self.assertEqual(outcome, a.status)

    def test_staff_can_still_cancel_a_scheduled_one(self):
        self._auth("s@racco1.gov.ph")
        a = self._appt(past=False)
        self.assertEqual(200, self._do(a, "cancelled").status_code)

    def test_psychologist_and_isa_can_correct_an_outcome(self):
        for email in ("p@racco1.gov.ph", "a@racco1.gov.ph"):
            self._auth(email)
            a = self._appt("completed")
            self.assertEqual(200, self._do(a, "no_show").status_code)
            self.assertEqual(200, self._do(a, "cancelled").status_code)

    def test_only_a_scheduled_one_can_be_moved(self):
        self._auth("p@racco1.gov.ph")
        for st in ("completed", "no_show", "cancelled"):
            a = self._appt(st, past=False)
            before = a.start
            r = self.client.patch(f"/api/appointments/{a.id}/", {
                "start": (before + timedelta(days=7)).isoformat()}, format="json")
            self.assertEqual(400, r.status_code, st)
            self.assertIn("Only a scheduled appointment can be moved", r.data["detail"])
            a.refresh_from_db()
            self.assertEqual(before, a.start)

    def test_notes_of_a_past_session_can_still_be_edited(self):
        self._auth("p@racco1.gov.ph")
        for st in ("completed", "no_show"):
            a = self._appt(st)
            r = self.client.patch(f"/api/appointments/{a.id}/", {
                "notes": "Late note", "start": a.start.isoformat(),
                "duration_minutes": a.duration_minutes}, format="json")
            self.assertEqual(200, r.status_code, (st, r.data))
            a.refresh_from_db()
            self.assertEqual(("Late note", st), (a.notes, a.status))

    def test_the_refusal_to_move_is_a_plain_string(self):
        self._auth("p@racco1.gov.ph")
        a = self._appt("completed", past=False)
        r = self.client.patch(f"/api/appointments/{a.id}/", {
            "start": (a.start + timedelta(days=7)).isoformat()}, format="json")
        self.assertEqual(400, r.status_code)
        self.assertIsInstance(r.data["detail"], str)

    def test_the_child_cannot_change_whatever_the_status(self):
        self._auth("p@racco1.gov.ph")
        other = child_with_referral("Ben", self.psy)
        a = self._appt("completed")
        r = self.client.patch(f"/api/appointments/{a.id}/", {"child": other.id},
                              format="json")
        self.assertEqual(400, r.status_code)
        self.assertIn("child", r.data)

    def test_an_edit_from_a_stale_copy_does_not_undo_a_cancellation(self):
        from scheduling.serializers import AppointmentSerializer
        a = self._appt(past=False)
        stale = Appointment.objects.get(pk=a.pk)
        APPOINTMENT_LIFECYCLE.move(a, "cancelled")
        ser = AppointmentSerializer(stale, data={"notes": "Edited"}, partial=True)
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        row = Appointment.objects.get(pk=a.pk)
        self.assertEqual(("Edited", "cancelled"), (row.notes, row.status))

    def test_status_in_a_patch_is_still_ignored(self):
        self._auth("p@racco1.gov.ph")
        a = self._appt(past=False)
        self.client.patch(f"/api/appointments/{a.id}/", {"status": "cancelled"},
                          format="json")
        a.refresh_from_db()
        self.assertEqual("scheduled", a.status)

    def test_a_move_cannot_change_the_child(self):
        self._auth("p@racco1.gov.ph")
        other = child_with_referral("Ben", self.psy)
        a = self._appt(past=False)
        r = self.client.patch(f"/api/appointments/{a.id}/", {"child": other.id},
                              format="json")
        self.assertEqual(400, r.status_code)
        self.assertIn("child", r.data)
        a.refresh_from_db()
        self.assertEqual(self.child, a.child)

    def test_the_same_child_resent_is_fine(self):
        self._auth("p@racco1.gov.ph")
        a = self._appt(past=False)
        new_start = a.start + timedelta(days=7)
        r = self.client.patch(f"/api/appointments/{a.id}/", {
            "child": self.child.id, "start": new_start.isoformat()}, format="json")
        self.assertEqual(200, r.status_code, r.data)

    def test_a_lost_race_is_409_and_sends_nothing(self):
        self._auth("p@racco1.gov.ph")
        a = self._appt(past=False)
        with patch("scheduling.views.notify_custodian") as notify, \
                patch("scheduling.views.session_changed") as changed, \
                patch.object(Lifecycle, "move", return_value=False):
            r = self._do(a, "cancelled")
        self.assertEqual(409, r.status_code)
        self.assertIn("changed a moment ago", r.data["detail"])
        notify.assert_not_called()
        changed.assert_not_called()

    def test_a_row_moved_in_the_database_after_loading_is_409(self):
        self._auth("p@racco1.gov.ph")
        a = self._appt(past=False)
        real = Lifecycle.move

        def racing(self_, instance, to, **kw):
            Appointment.objects.filter(pk=instance.pk).update(status="cancelled")
            return real(self_, instance, to, **kw)
        with patch("scheduling.views.notify_custodian") as notify, \
                patch.object(Lifecycle, "move", racing):
            r = self._do(a, "cancelled")
        self.assertEqual(409, r.status_code)
        notify.assert_not_called()
