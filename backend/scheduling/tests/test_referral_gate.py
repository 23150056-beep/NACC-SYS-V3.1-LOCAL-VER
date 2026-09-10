"""No case referral on file, no sessions booked.

The referral is the social worker's official document saying why this child is
here. Booking counselling before it exists puts the appointment ahead of the
paperwork that authorises it, and the appointment is the part that involves a
child sitting in a room.

Three decisions, and the middle one is the one worth arguing about:

* **A child record can still be created without one.** Intake happens when a
  child arrives, not when the file catches up, and a system that refuses to
  record a child until the paperwork lands is a system people keep on paper
  instead.
* **Assignment is not blocked either.** The rule people asked for is about
  scheduling, and refusing to assign a psychologist would leave the child
  belonging to nobody - which is worse than a delay in booking.
* **Booking is blocked, in the endpoint.** Hiding the button would be a
  suggestion; this is a rule, so it lives where every caller passes.

Moving an appointment that already exists is deliberately still allowed. The
gate is on putting a child into the diary, not on tidying a diary they are
already in - otherwise every child booked before the rule existed becomes
unmovable until somebody hunts down a document for them.
"""
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile

from children.models import Child
from clinical.models import CaseReferral
from scheduling.models import Appointment
from scheduling.tests.test_api import SchedulingBase, next_weekday


def upload_referral(child, user):
    return CaseReferral.objects.create(
        child=child, uploaded_by=user,
        file=SimpleUploadedFile("referral.pdf", b"%PDF-1.4 referral"),
        original_filename="referral.pdf")


class BookingNeedsAReferralTest(SchedulingBase):
    def setUp(self):
        super().setUp()
        # The shared fixture arrives WITH a referral, because that is what an
        # intake'd child looks like. These tests are about its absence.
        CaseReferral.objects.filter(child=self.child).delete()
        self._auth("s@racco1.gov.ph")
        self.when = next_weekday(2, 10)

    def _book(self, child=None, start=None):
        return self.client.post("/api/appointments/", {
            "child": (child or self.child).id, "psychologist": self.psy.id,
            "start": (start or self.when).isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json")

    def test_booking_is_refused_when_no_referral_is_on_file(self):
        response = self._book()
        self.assertEqual(400, response.status_code)
        self.assertIn("referral", str(response.data).lower())

    def test_the_refusal_says_what_to_do_about_it(self):
        # "Not permitted" sends somebody to an administrator for a permission
        # they already have. The missing thing is a document.
        self.assertIn("upload", str(self._book().data).lower())

    def test_uploading_the_referral_unblocks_it(self):
        upload_referral(self.child, self.staff)
        self.assertEqual(201, self._book().status_code)

    def test_another_childs_referral_does_not_count(self):
        other = Child.objects.create(fullname="Ben", assigned_psychologist=self.psy)
        upload_referral(other, self.staff)
        self.assertEqual(400, self._book().status_code)

    def test_a_psychologist_booking_their_own_calendar_is_not_exempt(self):
        # The availability window is a preference they may override. Whether
        # the child's paperwork exists is not about their calendar at all.
        self._auth("p@racco1.gov.ph")
        response = self.client.post("/api/appointments/", {
            "child": self.child.id, "start": self.when.isoformat(),
            "duration_minutes": 60, "purpose": "session"}, format="json")
        self.assertEqual(400, response.status_code)

    def test_moving_an_appointment_that_already_exists_is_still_allowed(self):
        # Children booked before this rule existed must not become unmovable.
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=self.when, duration_minutes=60, booked_by=self.staff)
        response = self.client.patch(
            f"/api/appointments/{appointment.id}/",
            {"start": (self.when + timedelta(hours=1)).isoformat()}, format="json")
        self.assertEqual(200, response.status_code)

    def test_cancelling_is_never_blocked_by_paperwork(self):
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=self.when, duration_minutes=60, booked_by=self.staff)
        self.assertEqual(
            200, self.client.post(f"/api/appointments/{appointment.id}/cancel/").status_code)


class TheGridAgreesWithTheEndpointTest(SchedulingBase):
    """The contract from test_slots.py, held under the new rule too."""

    def setUp(self):
        super().setUp()
        CaseReferral.objects.filter(child=self.child).delete()
        self._auth("s@racco1.gov.ph")
        self.wednesday = next_weekday(2, 9).date()

    def _slots(self, **extra):
        params = {"psychologist": self.psy.id, "date": self.wednesday.isoformat(),
                  "child": self.child.id, **extra}
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.client.get(f"/api/availability/slots/?{query}").data

    def test_no_times_are_offered_without_a_referral(self):
        # Offering a slot the endpoint will refuse is the screen lying.
        self.assertEqual([], self._slots()["slots"])

    def test_it_says_the_referral_is_what_is_missing(self):
        self.assertIn("referral", self._slots()["reason"].lower())

    def test_times_come_back_once_the_referral_is_uploaded(self):
        upload_referral(self.child, self.staff)
        self.assertTrue(self._slots()["slots"])

    def test_the_grid_still_helps_when_MOVING_an_existing_appointment(self):
        # Rescheduling stays allowed, so the grid must keep offering times -
        # otherwise the drawer that exists to move an appointment shows an
        # empty panel and a reason that does not apply to it.
        appointment = Appointment.objects.create(
            child=self.child, psychologist=self.psy,
            start=next_weekday(2, 10), duration_minutes=60, booked_by=self.staff)
        self.assertTrue(self._slots(exclude=appointment.id)["slots"])


class RecordingAChildIsUnaffectedTest(SchedulingBase):
    def test_a_child_can_still_be_created_without_a_referral(self):
        self._auth("s@racco1.gov.ph")
        response = self.client.post("/api/children/", {
            "fullname": "Cara Diaz", "case_type": "Foster Care"}, format="json")
        self.assertEqual(201, response.status_code, response.data)

    def test_a_child_can_still_be_assigned_without_a_referral(self):
        self._auth("a@racco1.gov.ph")
        response = self.client.patch(f"/api/children/{self.child.id}/",
                                     {"assigned_psychologist": self.psy.id},
                                     format="json")
        self.assertEqual(200, response.status_code, response.data)
