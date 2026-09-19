"""The three records that end by being archived, never by being deleted.

A child, a user account and an appointment all have a designed way to end:
`terminate` writes a TerminationRecord and demands a reason; `archive` keeps
the row, which is what stops a declined address registering again; `cancel`
keeps the appointment so the diary still shows a session was agreed and called
off. Each is scoped to the right role and each writes an activity row.

DELETE was none of that. It was never written, never called by any screen, and
never tested - and DRF's ModelViewSet supplies it whether or not anyone meant
to. What it supplied, measured before this test existed:

* Staff DELETE an appointment -> 204. `_set_status` deliberately lets staff
  cancel and NOT record an outcome; DELETE let them erase the row entirely.
* A psychologist DELETE their own COMPLETED session from last week -> 204.
* Staff DELETE a child -> 204, and every FK to Child is on_delete=CASCADE, so
  the appointments, remarks, referrals, consents and the TerminationRecords
  meant to outlive the case went with it.
* An administrator DELETE the last administrator -> 204, admins left: 0. The
  `archive` action refuses that exact request, in as many words, because an
  agency with no administrator has no way to make one.
* Activity rows written by any of the above: none.

So this is the same lesson as `perform_update` in scheduling/booking.py, in
the one verb nobody had written a rule for: a rule enforced on one verb is not
enforced. These tests are per-role rather than one blanket check, because the
hole was never "nobody may delete" - it was that the roles each screen already
refuses had a second door standing open beside it.

405 rather than 403 is deliberate. The caller is not forbidden from ending the
record; they asked with the wrong verb, and there is a route that works.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from activity.models import ActivityLog
from children.models import Child, TerminationRecord
from clinical.models import RemarkNote
from scheduling.models import Appointment
from scheduling.tests.test_api import give_referral, next_weekday

User = get_user_model()


class HardDeleteBase(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234",
            role=self.admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=self.psy_role)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=self.staff_role)
        self.child = Child.objects.create(
            fullname="Ana", case_type="Foster Care", assigned_psychologist=self.psy)
        give_referral(self.child, self.staff)

    def _auth(self, user):
        token = self.client.post("/api/auth/login/", {
            "email": user.email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _appointment(self, **kwargs):
        return Appointment.objects.create(
            child=self.child, psychologist=self.psy, booked_by=self.staff,
            start=kwargs.pop("start", next_weekday(2, 10)),
            duration_minutes=60, **kwargs)


class AppointmentsAreCancelledNotDeleted(HardDeleteBase):
    def test_staff_cannot_delete_an_appointment(self):
        """The role that may cancel and may not record an outcome."""
        appointment = self._appointment()
        self._auth(self.staff)
        response = self.client.delete(f"/api/appointments/{appointment.id}/")
        self.assertEqual(405, response.status_code)
        self.assertTrue(Appointment.objects.filter(pk=appointment.pk).exists())

    def test_a_psychologist_cannot_delete_their_own_appointment(self):
        """Own calendar waives the availability window, never the record."""
        appointment = self._appointment()
        self._auth(self.psy)
        response = self.client.delete(f"/api/appointments/{appointment.id}/")
        self.assertEqual(405, response.status_code)
        self.assertTrue(Appointment.objects.filter(pk=appointment.pk).exists())

    def test_a_completed_session_cannot_be_deleted(self):
        """A session that HAPPENED is the one it matters most to keep."""
        appointment = self._appointment(
            start=timezone.now() - timedelta(days=7),
            status=Appointment.COMPLETED)
        self._auth(self.psy)
        response = self.client.delete(f"/api/appointments/{appointment.id}/")
        self.assertEqual(405, response.status_code)
        self.assertEqual(Appointment.COMPLETED,
                         Appointment.objects.get(pk=appointment.pk).status)

    def test_an_administrator_cannot_either(self):
        appointment = self._appointment()
        self._auth(self.admin)
        response = self.client.delete(f"/api/appointments/{appointment.id}/")
        self.assertEqual(405, response.status_code)
        self.assertTrue(Appointment.objects.filter(pk=appointment.pk).exists())

    def test_cancel_is_still_open_to_staff_and_logs_it(self):
        """The route that replaces it, proved to still work - a rule that only
        takes something away has removed a capability rather than moved it."""
        appointment = self._appointment()
        self._auth(self.staff)
        before = ActivityLog.objects.count()
        response = self.client.post(f"/api/appointments/{appointment.id}/cancel/")
        self.assertEqual(200, response.status_code)
        self.assertEqual(Appointment.CANCELLED,
                         Appointment.objects.get(pk=appointment.pk).status)
        self.assertEqual(before + 1, ActivityLog.objects.count())


class ChildrenAreTerminatedNotDeleted(HardDeleteBase):
    def test_staff_cannot_delete_a_child(self):
        """RecordsAccess's write rule is Admin OR STAFF, so this was the
        archive action's hole reopened under another verb."""
        self._auth(self.staff)
        response = self.client.delete(f"/api/children/{self.child.id}/")
        self.assertEqual(405, response.status_code)
        self.assertTrue(Child.objects.filter(pk=self.child.pk).exists())

    def test_an_administrator_cannot_delete_a_child(self):
        self._auth(self.admin)
        response = self.client.delete(f"/api/children/{self.child.id}/")
        self.assertEqual(405, response.status_code)
        self.assertTrue(Child.objects.filter(pk=self.child.pk).exists())

    def test_the_cascade_that_made_it_worse_never_runs(self):
        """Every FK to Child is on_delete=CASCADE. One 204 took the lot."""
        self._appointment()
        RemarkNote.objects.create(child=self.child, author=self.psy, text="note")
        self._auth(self.admin)
        self.client.delete(f"/api/children/{self.child.id}/")
        self.assertEqual(1, Appointment.objects.count())
        self.assertEqual(1, RemarkNote.objects.count())
        self.assertEqual(1, self.child.case_referrals.count())

    def test_terminate_is_still_the_way_a_case_ends(self):
        self._auth(self.admin)
        response = self.client.post(f"/api/children/{self.child.id}/terminate/", {
            "reason_category": TerminationRecord.REASON_CHOICES[0][0],
            "note": "Case closed at the guardian's request."}, format="json")
        self.assertEqual(200, response.status_code)
        self.child.refresh_from_db()
        self.assertEqual(Child.INACTIVE, self.child.status)
        self.assertEqual(1, TerminationRecord.objects.filter(child=self.child).count())


class AccountsAreArchivedNotDeleted(HardDeleteBase):
    def test_an_administrator_cannot_delete_an_account(self):
        self._auth(self.admin)
        response = self.client.delete(f"/api/users/{self.psy.id}/")
        self.assertEqual(405, response.status_code)
        self.assertTrue(User.objects.filter(pk=self.psy.pk).exists())

    def test_the_last_administrator_cannot_be_deleted_either(self):
        """`archive` refuses this in as many words. DELETE answered 204 and
        left the agency with no administrator and no way to make one."""
        self._auth(self.admin)
        refusal = self.client.post(f"/api/users/{self.admin.id}/archive/")
        self.assertEqual(400, refusal.status_code)
        self.assertIn("only administrator", str(refusal.data).lower())

        response = self.client.delete(f"/api/users/{self.admin.id}/")
        self.assertEqual(405, response.status_code)
        self.assertEqual(1, User.objects.filter(
            role__role_name=Role.ADMINISTRATOR, status=User.ACTIVE).count())

    def test_archive_is_still_the_way_an_account_ends(self):
        self._auth(self.admin)
        response = self.client.post(f"/api/users/{self.psy.id}/archive/")
        self.assertEqual(200, response.status_code)
        self.psy.refresh_from_db()
        self.assertEqual(User.ARCHIVED, self.psy.status)
        # The row survives, which is what stops the address registering again.
        self.assertTrue(User.objects.filter(pk=self.psy.pk).exists())
        self.assertFalse(self.psy.is_active)


class TheRoutesThatDoDeleteStillDo(HardDeleteBase):
    """The counterpart, so this does not quietly become "nothing may delete".

    Availability, leave and case referrals are all removed by a real button on
    a real screen, and all three keep their DELETE. Availability is the one
    worth pinning: removing a window deliberately does NOT cancel the sessions
    booked inside it, and that behaviour would be just as broken if some later
    tidy-up took this verb away with the others.
    """

    def test_a_psychologist_can_still_remove_their_own_availability(self):
        from scheduling.models import AvailabilityBlock
        block = AvailabilityBlock.objects.create(
            psychologist=self.psy, weekday=2,
            start_time="09:00", end_time="12:00", capacity=2)
        appointment = self._appointment()
        self._auth(self.psy)
        response = self.client.delete(f"/api/availability/{block.id}/")
        self.assertEqual(204, response.status_code)
        self.assertFalse(AvailabilityBlock.objects.filter(pk=block.pk).exists())
        # Booked sessions are left exactly where they are - the screen reports
        # the count and changes nothing.
        self.assertTrue(Appointment.objects.filter(pk=appointment.pk).exists())

    def test_staff_can_still_remove_a_case_referral(self):
        referral = self.child.case_referrals.first()
        self._auth(self.staff)
        response = self.client.delete(f"/api/case-referrals/{referral.id}/")
        self.assertEqual(204, response.status_code)
        self.assertEqual(0, self.child.case_referrals.count())
