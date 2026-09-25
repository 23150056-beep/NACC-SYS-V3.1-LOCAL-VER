"""Staff had a firehose where psychologists had an inbox.

The scoping was already role-aware and already right in shape: a psychologist
sees only what is addressed to them, an administrator sees the whole audit
stream, and staff see the case-coordination stream. But every `recipient=` in
the codebase pointed at a psychologist, so nothing in the system was ever
addressed to a staff member. Their "notifications" were every child-record
event in the office, whether or not it concerned their work, and a personal
one could not have reached them even if something had tried to send it.

Two changes, and the first is what makes the second possible:

* staff now also see anything addressed to them personally, on top of the
  stream they already saw - a superset, so nothing they relied on disappears;
* the adoption module addresses its case events to the case OWNER, who is a
  staff member. That is the one place the data model actually knows which
  staff member a piece of work belongs to.

Notifications about children stayed collective while staff worked a shared
caseload with no per-child owner in the model. Since 24 Sep 2026 each social
worker keeps their own records (`Child.social_worker`), so the record stream a
staff member sees is their own records' - the owner decided the fact the model
was missing.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role
from activity.models import ActivityLog
from activity.services import log_activity
from children.models import Child

User = get_user_model()


class StaffSeeWhatIsAddressedToThemTest(APITestCase):
    def setUp(self):
        staff_role = Role.objects.create(role_name=Role.STAFF)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234",
            role=staff_role)
        self.other_staff = User.objects.create_user(
            email="s2@racco1.gov.ph", username="s2", password="pass1234",
            role=staff_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.child = Child.objects.create(fullname="Ana", assigned_psychologist=self.psy,
                                          social_worker=self.staff)
        self.client.force_authenticate(self.staff)

    def _labels(self):
        return [row["entity_label"] for row in self.client.get("/api/activity/").data]

    def test_the_record_stream_is_their_own_records(self):
        # A superset of the inbox, over their own records - not the office's.
        colleagues = Child.objects.create(fullname="Ben", assigned_psychologist=self.psy,
                                          social_worker=self.other_staff)
        for child in (self.child, colleagues):
            log_activity(self.psy, ActivityLog.UPDATED, ActivityLog.RECORD,
                         entity_type="Child", entity_label=child.fullname, entity_id=child.id)
        self.assertIn("Ana", self._labels())
        self.assertNotIn("Ben", self._labels())

    def test_something_addressed_to_them_arrives(self):
        log_activity(self.psy, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="AdoptionCase", entity_label="Ben's adoption case",
                     entity_id=self.child.id, recipient=self.staff)
        self.assertIn("Ben's adoption case", self._labels())

    def test_something_addressed_to_a_COLLEAGUE_does_not(self):
        # The point of an inbox. Without this it is the firehose again with
        # extra rows in it.
        log_activity(self.psy, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="AdoptionCase", entity_label="Not yours",
                     entity_id=self.child.id, recipient=self.other_staff)
        self.assertNotIn("Not yours", self._labels())

    def test_a_security_event_is_still_not_theirs_to_read(self):
        # Staff see case coordination, not the audit trail. Widening the filter
        # must not have quietly widened that too.
        log_activity(self.psy, ActivityLog.UPDATED, ActivityLog.SECURITY,
                     entity_type="User", entity_label="Somebody's password",
                     entity_id=self.psy.id)
        self.assertNotIn("Somebody's password", self._labels())


class PsychologistsAreUnchangedTest(APITestCase):
    def setUp(self):
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=Role.objects.create(role_name=Role.PSYCHOLOGIST))
        self.child = Child.objects.create(fullname="Ana", assigned_psychologist=self.psy)
        self.client.force_authenticate(self.psy)

    def test_they_still_see_only_what_is_addressed_to_them(self):
        log_activity(None, ActivityLog.UPDATED, ActivityLog.RECORD,
                     entity_type="Child", entity_label="Somebody else's child",
                     entity_id=self.child.id)
        labels = [row["entity_label"] for row in self.client.get("/api/activity/").data]
        self.assertNotIn("Somebody else's child", labels)
