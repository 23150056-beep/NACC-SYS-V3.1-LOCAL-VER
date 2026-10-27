"""A record's status moves only through terminate and reopen.

Closing a case writes a closing record and reopening clears the psychologist,
so an edit that set `status` by itself skipped both: the record went inactive
with no reason on file. Found 26 Oct 2026 in the Add Record audit.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role
from children.models import Child
from children.tests.payloads import complete

User = get_user_model()


class StatusIsReadOnlyTest(TestCase):
    def setUp(self):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345", role=roles[role])
        self.sw = make("sw@t.ph", Role.STAFF)
        self.admin = make("admin@t.ph", Role.ADMINISTRATOR)
        self.psy = make("psy@t.ph", Role.PSYCHOLOGIST)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_an_edit_cannot_close_a_case(self):
        for user in (self.sw, self.admin):
            child = Child.objects.create(first_name="Ana", last_name=f"C{user.pk}",
                                         social_worker=self.sw, assigned_psychologist=self.psy)
            res = self._as(user).patch(f"/api/children/{child.id}/", {"status": "inactive"},
                                       format="json")
            self.assertEqual(200, res.status_code, res.data)
            self.assertEqual("active", res.data["status"])       # still in the response
            child.refresh_from_db()
            self.assertEqual(Child.ACTIVE, child.status)
            self.assertEqual(0, child.terminations.count())

    def test_a_psychologist_edit_cannot_either(self):
        child = Child.objects.create(first_name="Ana", last_name="Cruz", social_worker=self.sw,
                                     assigned_psychologist=self.psy)
        self._as(self.psy).patch(f"/api/children/{child.id}/", {"status": "inactive"}, format="json")
        child.refresh_from_db()
        self.assertEqual(Child.ACTIVE, child.status)

    def test_a_new_record_is_active_whatever_the_request_says(self):
        for n, user in enumerate((self.sw, self.admin)):
            res = self._as(user).post("/api/children/",
                                      complete(first_name=f"Zed{n}", status="inactive"),
                                      format="json")
            self.assertEqual(201, res.status_code, res.data)
            self.assertEqual("active", res.data["status"])
            self.assertEqual(Child.ACTIVE, Child.objects.get(pk=res.data["id"]).status)

    def test_a_closed_record_stays_closed_through_an_edit_that_says_active(self):
        child = Child.objects.create(first_name="Ana", last_name="Cruz", social_worker=self.sw,
                                     status=Child.INACTIVE)
        self._as(self.admin).patch(f"/api/children/{child.id}/", {"status": "active"}, format="json")
        child.refresh_from_db()
        self.assertEqual(Child.INACTIVE, child.status)
