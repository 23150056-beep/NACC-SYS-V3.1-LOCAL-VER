"""Progress Monitoring lists the case edited most recently first.

Stamps are written with queryset .update(), which skips auto_now, so no test
here reads the wall clock.
"""
from datetime import datetime, timezone as dt_tz

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role
from children.models import Child
from clinical.models import RemarkNote

User = get_user_model()


def day(n):
    return datetime(2026, 1, n, 9, 0, tzinfo=dt_tz.utc)


class MonitoringOrderTests(APITestCase):
    def setUp(self):
        psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=psy_role)
        self.previous = User.objects.create_user(
            email="q@racco1.gov.ph", username="q", password="pass1234", role=psy_role)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=admin_role)

    def child(self, name, stamp, **extra):
        c = Child.objects.create(fullname=name, assigned_psychologist=self.psy, **extra)
        Child.objects.filter(pk=c.pk).update(updated_at=stamp)
        return c

    def remark(self, child, author, stamp):
        r = RemarkNote.objects.create(child=child, author=author, text="note")
        RemarkNote.objects.filter(pk=r.pk).update(updated_at=stamp)
        return r

    def rows(self, user):
        self.client.force_authenticate(user)
        return self.client.get("/api/reports/monitoring/").data

    def test_latest_edit_first(self):
        a = self.child("Ana Alba", day(1))
        b = self.child("Ben Bato", day(2))
        c = self.child("Cora Cruz", day(5))
        self.remark(b, self.psy, day(9))
        data = self.rows(self.admin)
        self.assertEqual([r["child_id"] for r in data], [b.id, c.id, a.id])
        self.assertTrue(all("last_updated" in r for r in data))
        self.assertEqual(data[0]["last_updated"], day(9).isoformat())

    def test_a_hidden_earlier_remark_does_not_lift_its_child(self):
        a = self.child("Ana Alba", day(1), assignee_sees_history=False)
        b = self.child("Ben Bato", day(3), assignee_sees_history=False)
        self.remark(a, self.previous, day(20))  # hidden from self.psy
        data = self.rows(self.psy)
        self.assertEqual([r["child_id"] for r in data], [b.id, a.id])
        self.assertEqual(data[1]["last_updated"], day(1).isoformat())
        # The administrator sees the remark, so there it does lift the child.
        self.assertEqual(self.rows(self.admin)[0]["child_id"], a.id)

    def test_ties_fall_back_to_name(self):
        z = self.child("Zed Zamora", day(4))
        a = self.child("Ana Alba", day(4))
        self.assertEqual([r["child_id"] for r in self.rows(self.admin)], [a.id, z.id])
