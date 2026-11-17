"""The one-door lifecycle helper, against the appointment's real statuses."""
from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from config.lifecycle import Lifecycle
from scheduling.models import Appointment
from scheduling.tests.test_api import SchedulingBase, child_with_referral, next_weekday

TABLE = Lifecycle(Appointment, "status", {
    "scheduled": {"completed", "no_show", "cancelled"},
    "completed": set(), "no_show": set(), "cancelled": set(),
}, name="appointment")
DOORS = {"*": "Use the status action.", "create": "New sessions start scheduled."}


class LifecycleTest(SchedulingBase):
    def _appt(self):
        return Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=next_weekday(2, 10), duration_minutes=60)

    def test_allows(self):
        self.assertTrue(TABLE.allows("scheduled", "completed"))
        self.assertFalse(TABLE.allows("completed", "scheduled"))
        self.assertFalse(TABLE.allows("nope", "completed"))
        self.assertEqual(4, len(TABLE.states))

    def test_move_succeeds_and_updates_memory_and_row(self):
        a = self._appt()
        before = a.updated_at
        self.assertTrue(TABLE.move(a, "completed", extra={"notes": "done"}))
        self.assertEqual("completed", a.status)
        a.refresh_from_db()
        self.assertEqual(("completed", "done"), (a.status, a.notes))
        self.assertGreater(a.updated_at, before)

    def test_move_loses_the_race(self):
        a = self._appt()
        stale = Appointment.objects.get(pk=a.pk)
        self.assertTrue(TABLE.move(a, "cancelled"))
        self.assertFalse(TABLE.move(stale, "completed"))
        self.assertEqual("scheduled", stale.status)
        a.refresh_from_db()
        self.assertEqual("cancelled", a.status)

    def test_an_unlisted_move_is_a_programming_error(self):
        a = self._appt()
        with self.assertRaises(ValueError):
            TABLE.move(a, "scheduled", expected="completed")

    def test_refuse_edit(self):
        a = self._appt()
        attrs = {"status": "scheduled", "notes": "x"}
        TABLE.refuse_edit(a, attrs, DOORS)
        self.assertEqual({"notes": "x"}, attrs)
        with self.assertRaises(serializers.ValidationError) as ctx:
            TABLE.refuse_edit(a, {"status": "completed"}, DOORS)
        self.assertEqual("Use the status action.", str(ctx.exception.detail["status"]))
        with self.assertRaises(serializers.ValidationError) as ctx:
            TABLE.refuse_edit(a, {"status": "completed"},
                              {("scheduled", "completed"): "Mark it complete.", "*": "x"})
        self.assertEqual("Mark it complete.", str(ctx.exception.detail["status"]))
        TABLE.refuse_edit(a, {}, DOORS)

    def test_refuse_edit_on_create(self):
        attrs = {"status": "scheduled"}
        TABLE.refuse_edit(None, attrs, DOORS)
        self.assertEqual({}, attrs)
        with self.assertRaises(serializers.ValidationError) as ctx:
            TABLE.refuse_edit(None, {"status": "completed"}, DOORS)
        self.assertEqual("New sessions start scheduled.", str(ctx.exception.detail["status"]))

    def test_check_table(self):
        self.assertEqual([], TABLE.check_table())
        bad = Lifecycle(Appointment, "status", {
            "scheduled": {"ghost"}, "completed": set(), "extra": set()}, name="bad")
        text = " | ".join(bad.check_table())
        self.assertIn("'cancelled' is not in the table", text)
        self.assertIn("'no_show' is not in the table", text)
        self.assertIn("state 'extra' is not a model choice", text)
        self.assertIn("'ghost' is not a state", text)

    def test_edit_fields_leaves_out_the_status_the_key_and_the_skipped(self):
        a = self._appt()
        fields = TABLE.edit_fields(a, also_skip=("notes",))
        self.assertNotIn("status", fields)
        self.assertNotIn("id", fields)
        self.assertNotIn("notes", fields)
        self.assertIn("purpose", fields)
        self.assertIn("updated_at", fields)
        self.assertIn("status", [f.name for f in a._meta.concrete_fields])
