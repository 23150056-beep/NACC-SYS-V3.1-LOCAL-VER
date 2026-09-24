from rest_framework import status
from rest_framework.test import APITestCase
from accounts.models import Role
from activity.models import ActivityLog
from children.models import Child, TerminationRecord
from children.tests.test_child_collab import make_user


class ReopenTests(APITestCase):
    def setUp(self):
        self.admin = make_user("ra@t.ph", Role.ADMINISTRATOR)
        self.staff = make_user("rs@t.ph", Role.STAFF)
        self.psych = make_user("rp@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(social_worker=self.staff, 
            fullname="Back Again", status=Child.INACTIVE,
            case_status=Child.STAGE_TERMINATED, assigned_psychologist=self.psych)
        TerminationRecord.objects.create(
            child=self.child, terminated_by=self.psych,
            reason_category="Services completed", note="Done for now.")

    def test_admin_reopen_restores_active_and_keeps_history(self):
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"/api/children/{self.child.id}/reopen/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, Child.ACTIVE)
        self.assertEqual(self.child.case_status, Child.STAGE_PRE_ASSESSMENT)
        self.assertEqual(self.child.terminations.count(), 1)  # history kept

    def test_reopen_resets_assigned_psychologist_but_keeps_details(self):
        # A reopened case returns to the pool unassigned — staff/admin pick
        # the (possibly different) psychologist fresh. Everything else stays.
        self.child.case_type = "Foster Care"
        self.child.save(update_fields=["case_type"])
        self.client.force_authenticate(self.admin)
        r = self.client.post(f"/api/children/{self.child.id}/reopen/")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.assigned_psychologist)
        self.assertEqual(self.child.fullname, "Back Again")
        self.assertEqual(self.child.case_type, "Foster Care")
        self.assertEqual(self.child.terminations.count(), 1)

    def test_staff_can_reopen_and_it_is_logged(self):
        # The owner's decision, 24 Sep 2026: staff run intake, and a child
        # returning to the clinic arrives at intake. Reopening restores and
        # erases nothing, so it no longer waits on an administrator.
        self.client.force_authenticate(self.staff)
        before = ActivityLog.objects.count()
        r = self.client.post(f"/api/children/{self.child.id}/reopen/")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, Child.ACTIVE)
        self.assertEqual(self.child.case_status, Child.STAGE_PRE_ASSESSMENT)
        self.assertIsNone(self.child.assigned_psychologist)
        self.assertEqual(self.child.terminations.count(), 1)  # history kept
        self.assertEqual(before + 1, ActivityLog.objects.count())

    def test_a_psychologist_still_cannot_reopen(self):
        self.client.force_authenticate(self.psych)
        r = self.client.post(f"/api/children/{self.child.id}/reopen/")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, Child.INACTIVE)

    def test_staff_still_cannot_terminate(self):
        # Only reopening moved. Ending a case stays with the assigned
        # psychologist or an administrator.
        active = Child.objects.create(social_worker=self.staff, fullname="Still Here", assigned_psychologist=self.psych)
        self.client.force_authenticate(self.staff)
        r = self.client.post(f"/api/children/{active.id}/terminate/",
                             {"reason_category": "Services completed", "note": "x"})
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        active.refresh_from_db()
        self.assertEqual(active.status, Child.ACTIVE)

    def test_reopen_active_child_400(self):
        self.client.force_authenticate(self.admin)
        active = Child.objects.create(fullname="Still Active")
        r = self.client.post(f"/api/children/{active.id}/reopen/")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_serializer_exposes_termination_history(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(f"/api/children/{self.child.id}/")
        self.assertEqual(len(r.data["terminations"]), 1)
        self.assertEqual(r.data["terminations"][0]["reason_category"], "Services completed")

    def test_who_else_is_viewing_works_on_an_archived_record(self):
        # The drawer an archived case is reopened from sends this every few
        # seconds; it answered 404 for inactive children.
        self.client.force_authenticate(self.staff)
        r = self.client.post(f"/api/children/{self.child.id}/presence/")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.client.force_authenticate(self.admin)
        r = self.client.get(f"/api/children/{self.child.id}/presence/")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(1, len(r.data["others"]))
