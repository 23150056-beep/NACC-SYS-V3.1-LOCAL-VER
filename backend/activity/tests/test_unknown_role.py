"""A role the code admits but names nowhere sees no audit lines at all.

`_scoped` used to end in an `else` for the full audit stream (the security
lines and every user line), so any role it did not list read the ISA's feed.
It is a comparison with the Administrator now, and the rest is `qs.none()`.
"""
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.probe import admit, make_probe
from activity.models import ActivityLog
from children.models import Child

User = get_user_model()


def _rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) else data


class UnknownRoleActivityTest(TestCase):
    def setUp(self):
        admit(self)
        self.probe = make_probe()
        role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="pass12345", role=role)
        child = Child.objects.create(first_name="Ana", last_name="Cruz",
                                     birth_date=date(2015, 3, 2))
        mk = lambda **kw: ActivityLog.objects.create(  # noqa: E731
            actor_label="Ada Admin", action=ActivityLog.UPDATED, **kw)
        mk(category=ActivityLog.SECURITY, entity_type="User", entity_label="Reset a password")
        mk(category=ActivityLog.USER, entity_type="User", entity_label="Edited an account")
        mk(category=ActivityLog.RECORD, entity_type="Child", entity_id=child.pk,
           entity_label="C-0001")
        mk(category=ActivityLog.RECORD, entity_type="Child", entity_id=child.pk,
           entity_label="C-0001", recipient=self.probe)

    def get(self, user, url):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(url)

    def test_the_feed_is_empty(self):
        res = self.get(self.probe, "/api/activity/")
        self.assertEqual(200, res.status_code)
        self.assertEqual([], _rows(res))

    def test_the_trail_is_empty(self):
        res = self.get(self.probe, "/api/activity/trail/")
        self.assertEqual(200, res.status_code)
        self.assertEqual(0, res.data["count"])
        self.assertEqual([], res.data["results"])

    def test_a_category_filter_cannot_widen_it(self):
        for category in ("security", "user", "record"):
            res = self.get(self.probe, f"/api/activity/trail/?category={category}")
            self.assertEqual(0, res.data["count"], category)

    def test_the_administrator_still_reads_the_security_and_user_lines(self):
        # The control: the same lines are there, so an empty answer above is
        # the scope, not an empty table.
        res = self.get(self.admin, "/api/activity/trail/")
        categories = {r["category"] for r in res.data["results"]}
        self.assertTrue({"security", "user"} <= categories)
