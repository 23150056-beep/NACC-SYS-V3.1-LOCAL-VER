from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role, User
from children.models import Child, TerminationRecord
from children.tests.payloads import complete


def make_user(email, role_name):
    role, _ = Role.objects.get_or_create(role_name=role_name)
    return User.objects.create_user(email=email, username=email.split("@")[0],
                                    password="pass12345", role=role)


class AStatusMovesOnlyThroughTerminateAndReopenTest(APITestCase):
    def setUp(self):
        self.admin = make_user("a@t.ph", Role.ADMINISTRATOR)
        self.staff = make_user("s@t.ph", Role.STAFF)
        self.child = Child.objects.create(social_worker=self.staff, fullname="Mika Santos")

    def patch(self, user, data):
        self.client.force_authenticate(user)
        return self.client.patch(f"/api/children/{self.child.id}/", data, format="json")

    def assert_still_open(self):
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, Child.ACTIVE)
        self.assertEqual(self.child.case_status, Child.STAGE_PRE_ASSESSMENT)
        self.assertFalse(TerminationRecord.objects.filter(child=self.child).exists())

    def test_a_social_worker_cannot_close_their_own_case_by_patch(self):
        r = self.patch(self.staff, {"status": "inactive"})
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assert_still_open()

    def test_an_administrator_cannot_close_a_case_by_patch(self):
        r = self.patch(self.admin, {"status": "inactive"})
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assert_still_open()

    def test_a_new_record_cannot_be_created_already_closed(self):
        self.client.force_authenticate(self.staff)
        r = self.client.post("/api/children/", complete(status="inactive"), format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertEqual(Child.objects.get(pk=r.data["id"]).status, Child.ACTIVE)


class TheEditFormKeepsSavingTest(APITestCase):
    """The form PUTs the whole record back, so its body always carries status."""

    def setUp(self):
        self.staff = make_user("s@t.ph", Role.STAFF)
        self.client.force_authenticate(self.staff)
        r = self.client.post("/api/children/", complete(), format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.id = r.data["id"]

    def put(self, **over):
        return self.client.put(f"/api/children/{self.id}/", complete(**over), format="json")

    def test_a_put_carrying_the_records_own_status_saves_the_change(self):
        r = self.put(education_level="Grade 5", status="active")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        child = Child.objects.get(pk=self.id)
        self.assertEqual(child.education_level, "Grade 5")
        self.assertEqual(child.status, Child.ACTIVE)

    def test_a_put_carrying_inactive_saves_and_leaves_the_record_active(self):
        r = self.put(education_level="Grade 6", status="inactive")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        child = Child.objects.get(pk=self.id)
        self.assertEqual(child.education_level, "Grade 6")
        self.assertEqual(child.status, Child.ACTIVE)
        self.assertFalse(TerminationRecord.objects.filter(child=child).exists())
