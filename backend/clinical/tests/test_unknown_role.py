"""A role the code admits but names nowhere: the Dashboard and the care gaps.

The Dashboard narrowed appointments and availability for a psychologist and a
social worker and let everyone else through to the agency's, and `alerts_for`
gave every non-social-worker the ISA's list. Each now names who takes each
branch, and the rest are given nothing.
"""
from datetime import datetime, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models import Role
from accounts.tests.probe import NOW, admit, freeze, make_probe
from children.models import Child
from clinical.care_gaps import alerts_for
from scheduling.models import Appointment, AvailabilityBlock

User = get_user_model()


class _Base(TestCase):
    def setUp(self):
        freeze(self)
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
        today = timezone.localdate()
        self.assertEqual(NOW.date(), today)
        Appointment.objects.create(child=self.child, psychologist=self.psy,
                                   start=timezone.make_aware(datetime.combine(today, time(10))))
        AvailabilityBlock.objects.create(psychologist=self.psy, weekday=today.weekday(),
                                         start_time=time(8), end_time=time(17), capacity=3)
        admit(self)
        self.probe = make_probe()

    def get(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c.get("/api/reports/dashboard/")

    def request(self, user):
        req = APIRequestFactory().get("/")
        req.user = user
        return req


class TheDashboard(_Base):
    def test_no_sessions_and_no_availability(self):
        res = self.get(self.probe)
        self.assertEqual(200, res.status_code)
        self.assertEqual([], res.data["today_schedule"])
        self.assertEqual([], res.data["availability_today"])

    def test_the_control_the_administrator_gets_both(self):
        res = self.get(self.admin)
        self.assertEqual(1, len(res.data["today_schedule"]))
        self.assertEqual(1, len(res.data["availability_today"]))

    def test_it_holds_no_records_and_no_care_gaps(self):
        res = self.get(self.probe)
        self.assertEqual(0, res.data["census"]["active"])
        self.assertEqual([], res.data["care_gaps"])


class CareGaps(_Base):
    def test_a_role_no_branch_names_has_none(self):
        self.assertEqual([], alerts_for(self.request(self.probe), Child.objects.all()))

    def test_the_control_the_administrator_has_them(self):
        Child.objects.create(first_name="Ben", last_name="Lim", assigned_psychologist=self.psy)
        self.assertTrue(alerts_for(self.request(self.admin), Child.objects.all()))
