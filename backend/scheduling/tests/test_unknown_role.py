"""A role the code admits but names nowhere gets nothing from the calendar.

Each door below used to end in an `else` that served the ISA's access (the
whole calendar, or a move with no own-child check). They name who takes each
branch now and refuse the rest. Two of them are behind an earlier gate (the
queryset, which is empty for the probe), so those tests also widen
KEEPS_RECORDS - a role that sees the calendar and is in no other list, which
is exactly the shape of the Officer - to get to the branch itself.
"""
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.probe import NOW, admit, freeze, make_probe
from assistant import tools
from children.models import Child
from scheduling.models import Appointment, AvailabilityBlock

User = get_user_model()
URL = "/api/appointments/"
FUTURE = timezone.make_aware(datetime(2030, 1, 14, 10, 0))
LATER = timezone.make_aware(datetime(2030, 1, 15, 10, 0))


class _Base(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345", role=roles[role])
        self.admin = make("admin@t.ph", Role.ADMINISTRATOR)
        self.sw = make("sw@t.ph", Role.STAFF)
        self.psy = make("psy@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(first_name="Ana", last_name="Cruz",
                                          assigned_psychologist=self.psy,
                                          social_worker=self.sw)
        self.appt = Appointment.objects.create(child=self.child, psychologist=self.psy,
                                               start=FUTURE, notes="original")
        admit(self)
        self.probe = make_probe()

    def as_(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c


class TheCalendar(_Base):
    def test_the_list_is_empty(self):
        res = self.as_(self.probe).get(URL)
        self.assertEqual(200, res.status_code)
        self.assertEqual([], res.data)

    def test_the_control_the_administrator_sees_it(self):
        self.assertEqual(1, len(self.as_(self.admin).get(URL).data))

    def test_one_appointment_is_not_found(self):
        self.assertEqual(404, self.as_(self.probe).get(f"{URL}{self.appt.pk}/").status_code)


class MovingABooking(_Base):
    """The hole: perform_update checked the own-child rule for Staff only, so
    any other role that got past the queryset could move any session."""

    def setUp(self):
        super().setUp()
        admit(self, "KEEPS_RECORDS")        # sees the calendar, in no other list

    def test_new_notes_are_refused(self):
        res = self.as_(self.probe).patch(
            f"{URL}{self.appt.pk}/", {"notes": "rewritten"}, format="json")
        self.assertEqual(403, res.status_code)
        self.appt.refresh_from_db()
        self.assertEqual("original", self.appt.notes)

    def test_a_new_time_is_refused(self):
        res = self.as_(self.probe).patch(
            f"{URL}{self.appt.pk}/", {"start": LATER.isoformat()}, format="json")
        self.assertEqual(403, res.status_code)
        self.appt.refresh_from_db()
        self.assertEqual(FUTURE, self.appt.start)

    def test_booking_is_refused(self):
        res = self.as_(self.probe).post(URL, {
            "child": self.child.pk, "psychologist": self.psy.pk,
            "start": LATER.isoformat(), "duration_minutes": 60,
            "purpose": "session"}, format="json")
        self.assertEqual(403, res.status_code)
        self.assertEqual(1, Appointment.objects.count())

    def test_cancelling_and_the_other_statuses_are_refused(self):
        for verb in ("cancel", "complete", "no_show"):
            res = self.as_(self.probe).post(f"{URL}{self.appt.pk}/{verb}/")
            self.assertEqual(403, res.status_code, verb)
        self.appt.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, self.appt.status)

    def test_the_control_the_social_worker_can_still_cancel_their_own(self):
        res = self.as_(self.sw).post(f"{URL}{self.appt.pk}/cancel/")
        self.assertEqual(200, res.status_code)


class RecordingAnOutcomeIsNotAReadList(_Base):
    """A role that sees every record and is a social worker in the lists (the
    Officer's shape) must not gain the ISA's right to mark any session
    completed or no-show: that is a write about an outcome, and the ISA's
    alone (a comparison, not SEES_EVERY_RECORD)."""

    def setUp(self):
        super().setUp()
        admit(self, "KEEPS_RECORDS", "SEES_EVERY_RECORD", "SOCIAL_WORK")
        freeze(self)
        Appointment.objects.filter(pk=self.appt.pk).update(start=NOW - timedelta(days=1))

    def test_completed_and_no_show_are_refused(self):
        for verb in ("complete", "no_show"):
            res = self.as_(self.probe).post(f"{URL}{self.appt.pk}/{verb}/")
            self.assertEqual(403, res.status_code, verb)
        self.appt.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, self.appt.status)

    def test_cancelling_any_childs_session_is_still_allowed(self):
        res = self.as_(self.probe).post(f"{URL}{self.appt.pk}/cancel/")
        self.assertEqual(200, res.status_code)

    def test_the_control_the_administrator_can_record_an_outcome(self):
        res = self.as_(self.admin).post(f"{URL}{self.appt.pk}/complete/")
        self.assertEqual(200, res.status_code)


class AStaffAccountHoldingTheSession(_Base):
    """A Staff account set as a session's psychologist - a psychologist whose
    role was changed - records the outcome of its own child's session, as it
    could before the role lists. On another worker's child the own-child rule
    refuses it, as it did then."""

    def setUp(self):
        super().setUp()
        freeze(self)
        self.other_sw = User.objects.create_user(
            email="sw2@t.ph", username="sw2", password="pass12345", role=self.sw.role)
        theirs = Child.objects.create(first_name="Bea", last_name="Reyes",
                                      social_worker=self.other_sw)
        past = NOW - timedelta(days=1)
        self.own = Appointment.objects.create(child=self.child, psychologist=self.sw,
                                              start=past)
        self.theirs = Appointment.objects.create(child=theirs, psychologist=self.sw,
                                                 start=past)

    def test_completing_its_own_childs_session_is_allowed(self):
        res = self.as_(self.sw).post(f"{URL}{self.own.pk}/complete/")
        self.assertEqual(200, res.status_code, res.data)
        self.own.refresh_from_db()
        self.assertEqual(Appointment.COMPLETED, self.own.status)

    def test_another_workers_child_is_refused(self):
        for verb in ("complete", "no_show"):
            res = self.as_(self.sw).post(f"{URL}{self.theirs.pk}/{verb}/")
            self.assertEqual(403, res.status_code, verb)
        self.theirs.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, self.theirs.status)


class TheOfficersFinalShapeHoldingTheSession(_Base):
    """The probe in every list the plan gives the Officer (EVERY_ROLE,
    SEES_EVERY_RECORD, KEEPS_RECORDS, SOCIAL_WORK, OVERSEES_CASEWORK) and set
    as the session's psychologist - a former psychologist made Officer - may
    cancel the session and never records its outcome:
    RECORDS_OWN_SESSION_OUTCOMES leaves the Officer out."""

    def setUp(self):
        super().setUp()
        admit(self, "SEES_EVERY_RECORD", "KEEPS_RECORDS", "SOCIAL_WORK",
              "OVERSEES_CASEWORK")
        freeze(self)
        self.held = Appointment.objects.create(child=self.child, psychologist=self.probe,
                                               start=NOW - timedelta(days=1))

    def test_completed_and_no_show_are_refused(self):
        for verb in ("complete", "no_show"):
            res = self.as_(self.probe).post(f"{URL}{self.held.pk}/{verb}/")
            self.assertEqual(403, res.status_code, verb)
        self.held.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, self.held.status)

    def test_cancelling_it_is_allowed(self):
        res = self.as_(self.probe).post(f"{URL}{self.held.pk}/cancel/")
        self.assertEqual(200, res.status_code, res.data)
        self.held.refresh_from_db()
        self.assertEqual(Appointment.CANCELLED, self.held.status)


class BookingWithoutTheCalendar(_Base):
    def test_booking_is_refused_even_before_the_calendar_is_shared(self):
        res = self.as_(self.probe).post(URL, {
            "child": self.child.pk, "psychologist": self.psy.pk,
            "start": LATER.isoformat(), "duration_minutes": 60,
            "purpose": "session"}, format="json")
        self.assertEqual(403, res.status_code)

    def test_cancelling_finds_nothing_to_cancel(self):
        res = self.as_(self.probe).post(f"{URL}{self.appt.pk}/cancel/")
        self.assertIn(res.status_code, (403, 404))
        self.appt.refresh_from_db()
        self.assertEqual(Appointment.SCHEDULED, self.appt.status)


class AvailabilityAndLeave(_Base):
    """The whole viewsets need a listed role; the probe is admitted, so what
    it gets is what the querysets and actions give a role they do not name."""

    def test_a_role_the_code_does_not_admit_is_refused_everywhere(self):
        from unittest.mock import patch
        from accounts import capabilities
        user = self.probe
        with patch.object(capabilities, "EVERY_ROLE", (Role.ADMINISTRATOR, Role.PSYCHOLOGIST,
                                                       Role.STAFF)):
            for url in ("/api/availability/", "/api/availability/slots/",
                        "/api/availability/month/", "/api/availability/psychologists/",
                        "/api/availability/openings/", "/api/unavailability/"):
                res = self.as_(user).get(url)
                self.assertEqual(403, res.status_code, url)

    def test_combine_and_the_psychologist_picker_are_refused(self):
        self.assertEqual(403, self.as_(self.probe).get("/api/availability/psychologists/").status_code)
        res = self.as_(self.probe).post("/api/availability/combine/", {"ids": [1, 2]},
                                           format="json")
        self.assertEqual(403, res.status_code)


class TheChatbotsSchedule(_Base):
    def _request(self, user):
        from rest_framework.test import APIRequestFactory
        req = APIRequestFactory().get("/api/assistant/ask/")
        req.user = user
        return req

    def test_scope_appointments_gives_nothing(self):
        qs, own = tools._scope_appointments(self._request(self.probe),
                                            Appointment.objects.all())
        self.assertEqual(0, qs.count())

    def test_the_control_the_administrator_gets_the_agency(self):
        qs, own = tools._scope_appointments(self._request(self.admin),
                                            Appointment.objects.all())
        self.assertEqual(1, qs.count())
        self.assertFalse(own)

    def _windows(self, user):
        """This week's open windows as the chatbot offers them to `user`, the
        clock pinned to Mon 23 Nov 2026 08:00 and a Wednesday window posted,
        so there is always one ahead to offer."""
        freeze(self)
        AvailabilityBlock.objects.get_or_create(
            psychologist=self.psy, weekday=2,
            defaults={"start_time": time(8), "end_time": time(17), "capacity": 3})
        return tools.REGISTRY["find_availability"]["resolve"](
            self._request(user), {"when": "this_week"})["items"]

    def test_find_availability_names_nobody_for_a_role_the_code_does_not_admit(self):
        from unittest.mock import patch
        from accounts import capabilities
        with patch.object(capabilities, "EVERY_ROLE",
                          (Role.ADMINISTRATOR, Role.PSYCHOLOGIST, Role.STAFF)):
            self.assertEqual([], self._windows(self.probe))
        self.assertEqual(["2026-11-25"], [w["date"] for w in self._windows(self.admin)])

    def test_a_role_the_code_admits_is_offered_every_psychologists_windows(self):
        """The brief's rule, as before the lists: everyone the system admits
        books, so sees every active psychologist's open windows."""
        self.assertEqual(["2026-11-25"], [w["date"] for w in self._windows(self.probe)])
