from rest_framework import status
from rest_framework.test import APITestCase
from accounts.models import Role
from children.lifecycle import CHILD_LIFECYCLE, LOST_RACE
from children.models import AssignmentRequest, Child, TerminationRecord
from children.serializers import ChildSerializer
from children.tests.test_child_collab import make_user


class CaseLifecycleTests(APITestCase):
    def setUp(self):
        self.admin = make_user("cla@t.ph", Role.ADMINISTRATOR)
        self.staff = make_user("cls@t.ph", Role.STAFF)
        self.psych = make_user("clp@t.ph", Role.PSYCHOLOGIST)
        self.other_psych = make_user("clq@t.ph", Role.PSYCHOLOGIST)
        self.child = Child.objects.create(
            fullname="Open Case", social_worker=self.staff,
            assigned_psychologist=self.psych)
        self.url = f"/api/children/{self.child.id}/"

    def closed(self):
        Child.objects.filter(pk=self.child.pk).update(
            status=Child.INACTIVE, case_status=Child.STAGE_TERMINATED)

    def test_table_matches_the_model(self):
        self.assertEqual(CHILD_LIFECYCLE.check_table(), [])

    def test_edit_cannot_close_a_case(self):
        for user in (self.staff, self.admin):
            self.client.force_authenticate(user)
            r = self.client.patch(self.url, {"status": "inactive"}, format="json")
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("status", r.data)
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, Child.ACTIVE)

    def test_edit_cannot_reopen_a_case(self):
        self.closed()
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.url + "?include_archived=true",
                              {"status": "active"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, r.data)
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, Child.INACTIVE)

    def test_resending_the_same_status_is_fine(self):
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.url, {"status": "active"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)

    def test_second_terminate_makes_no_second_record(self):
        self.client.force_authenticate(self.admin)
        body = {"reason_category": "Other", "note": "Closing."}
        first = self.client.post(self.url + "terminate/", body, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK, first.data)
        again = self.client.post(self.url + "terminate/", body, format="json")
        self.assertEqual(again.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TerminationRecord.objects.filter(child=self.child).count(), 1)

    def test_lost_race_on_terminate_is_409_and_writes_nothing(self):
        self.client.force_authenticate(self.admin)
        stale = Child.objects.get(pk=self.child.pk)
        self.assertTrue(CHILD_LIFECYCLE.move(
            Child.objects.get(pk=self.child.pk), Child.INACTIVE))
        from unittest.mock import patch
        with patch("children.views.ChildViewSet.get_object", return_value=stale):
            r = self.client.post(
                self.url + "terminate/",
                {"reason_category": "Other", "note": "Closing."}, format="json")
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(r.data["detail"], LOST_RACE)
        self.assertEqual(TerminationRecord.objects.filter(child=self.child).count(), 0)

    def test_lost_race_on_reopen_is_409(self):
        self.closed()
        self.client.force_authenticate(self.admin)
        stale = Child.objects.get(pk=self.child.pk)
        self.assertTrue(CHILD_LIFECYCLE.move(
            Child.objects.get(pk=self.child.pk), Child.ACTIVE))
        from unittest.mock import patch
        with patch("children.views.ChildViewSet.get_object", return_value=stale):
            r = self.client.post(self.url + "reopen/")
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)

    def test_closed_record_cannot_ask_a_psychologist(self):
        self.closed()
        self.client.force_authenticate(self.admin)
        r = self.client.patch(self.url + "?include_archived=true",
                              {"psychologist": self.other_psych.id}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, r.data)
        self.assertEqual(AssignmentRequest.objects.filter(child=self.child).count(), 0)

    def test_an_edit_from_a_stale_copy_does_not_reopen_a_closed_case(self):
        stale = Child.objects.get(pk=self.child.pk)
        self.closed()
        ser = ChildSerializer(stale, data={"landmark": "By the church"}, partial=True)
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        row = Child.objects.get(pk=self.child.pk)
        self.assertEqual(row.landmark, "By the church")
        self.assertEqual(row.status, Child.INACTIVE)
        self.assertEqual(row.case_status, Child.STAGE_TERMINATED)
        self.assertEqual(row.fullname, "Open Case")
