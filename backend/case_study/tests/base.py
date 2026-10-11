"""Fixtures shared by the case study tests.

The clock is pinned (a test never reads the wall clock - CLAUDE.md, "Before
committing"): `django.utils.timezone.now` is patched, and `localdate` and
`localtime` follow it. 12:00 on 8 Oct 2026, Manila time.
"""
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role, User
from case_study.models import CaseStudy
from children.models import Child

TODAY = date(2026, 10, 8)
NOW = datetime(2026, 10, 8, 4, 0, tzinfo=dt_timezone.utc)


def make_user(email, role_name, first, last):
    role, _ = Role.objects.get_or_create(role_name=role_name)
    return User.objects.create_user(
        email=email, username=email.split("@")[0], password="pass12345",
        first_name=first, last_name=last, role=role)


class CaseStudyTestCase(TestCase):
    """An adoption record held by `sw`, assigned to `psy`; one more of each
    role for the people who must be refused."""

    @classmethod
    def setUpTestData(cls):
        cls.sw = make_user("sw@t.ph", Role.STAFF, "Editha", "Pascua")
        cls.sw2 = make_user("sw2@t.ph", Role.STAFF, "Rosa", "Santos")
        cls.psy = make_user("psy@t.ph", Role.PSYCHOLOGIST, "Marivic", "Bulan")
        cls.psy2 = make_user("psy2@t.ph", Role.PSYCHOLOGIST, "Jose", "Rizal")
        cls.isa = make_user("isa@t.ph", Role.ADMINISTRATOR, "Ada", "Admin")
        cls.child = Child.objects.create(
            first_name="Ana", last_name="Cruz", gender="Female",
            birth_date=date(2019, 3, 2),
            case_type="Adoption", case_category="Surrendered", type_of_adoption="Regular",
            social_worker=cls.sw, assigned_psychologist=cls.psy)

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    # --- helpers ---------------------------------------------------------------

    def as_user(self, user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user)
        return client

    def url(self, child=None):
        return f"/api/case-studies/child/{(child or self.child).pk}/"

    def section_url(self, key, child=None):
        return f"{self.url(child)}sections/{key}/"

    def start(self, child=None, **fields):
        """A case study already started for the child, as the SW would."""
        return CaseStudy.objects.create(
            child=child or self.child, created_by=self.sw, **fields)

    def save_section(self, key, value, version=None, user=None, child=None, **extra):
        body = {"value": value, **extra}
        if version is not None:
            body["expected_version"] = version
        return self.as_user(user or self.sw).put(
            self.section_url(key, child), body, format="json")
