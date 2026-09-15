from django.test import TestCase
from activity.models import ActivityLog


class ActivityLogModelTest(TestCase):
    def test_create_minimal_log(self):
        log = ActivityLog.objects.create(
            actor=None, actor_label="System",
            action=ActivityLog.LOGIN, category=ActivityLog.SECURITY)
        self.assertEqual(log.category, "security")
        self.assertIsNotNone(log.created_at)


from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role

User = get_user_model()


class ActivityApiTest(APITestCase):
    def setUp(self):
        # Admin sees the full stream (record + security), so newest-first ordering
        # across categories is observable.
        self.role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.user = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.role)
        ActivityLog.objects.create(
            actor=self.user, actor_label="Staff",
            action=ActivityLog.CREATED, category=ActivityLog.RECORD,
            entity_type="Child", entity_label="Juan")
        ActivityLog.objects.create(
            actor=self.user, actor_label="Staff",
            action=ActivityLog.LOGIN, category=ActivityLog.SECURITY)

    def _auth(self):
        token = self.client.post("/api/auth/login/", {
            "email": "staff@racco1.gov.ph", "password": "staff1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_requires_authentication(self):
        resp = self.client.get("/api/activity/")
        self.assertEqual(resp.status_code, 401)

    def test_lists_newest_first(self):
        self._auth()
        resp = self.client.get("/api/activity/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data[0]["category"], "security")

    def test_filters_by_category(self):
        self._auth()
        resp = self.client.get("/api/activity/?category=record")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(all(r["category"] == "record" for r in resp.data))
        self.assertEqual(len(resp.data), 1)


from children.models import Child


class RecordHookTest(APITestCase):
    def setUp(self):
        self.role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.role)
        token = self.client.post("/api/auth/login/", {
            "email": "staff@racco1.gov.ph", "password": "staff1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_create_child_logs_record_created(self):
        self.client.post("/api/children/", {
            "fullname": "Juan Cruz", "case_type": "Foster Care"})
        logs = ActivityLog.objects.filter(category="record", action="created")
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().entity_type, "Child")
        self.assertEqual(logs.first().entity_label, "Juan Cruz")

    def test_terminating_a_child_logs_record_archived(self):
        """Terminate is what ends a case, and it is administrators (or the
        child's own psychologist) who may - not the staff account this class
        signs in as, which is why this signs in again."""
        User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR))
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

        child = Child.objects.create(fullname="Ana Lopez", case_type="Adoption")
        resp = self.client.post(f"/api/children/{child.id}/terminate/", {
            "reason_category": "Adoption finalized", "note": "Placement completed."})
        self.assertEqual(200, resp.status_code, resp.data)
        self.assertEqual(
            ActivityLog.objects.filter(category="record", action="archived").count(), 1)


class NotificationScopingTest(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=self.admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=self.psy_role)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_assigning_child_notifies_psychologist(self):
        self._auth("a@racco1.gov.ph")
        self.client.post("/api/children/", {
            "fullname": "New Kid", "case_type": "Foster Care", "psychologist": self.psy.id}, format="json")
        self.assertTrue(
            ActivityLog.objects.filter(recipient=self.psy, entity_type="Child").exists())

    def test_psychologist_sees_only_recipient_notifications(self):
        from activity.services import log_activity
        child = Child.objects.create(fullname="Mine", assigned_psychologist=self.psy)
        log_activity(self.admin, ActivityLog.CREATED, ActivityLog.RECORD,
                     entity_type="Child", entity_label="Mine", entity_id=child.id, recipient=self.psy)
        log_activity(self.admin, ActivityLog.CREATED, ActivityLog.USER,
                     entity_type="User", entity_label="Someone Else")
        self._auth("p@racco1.gov.ph")
        resp = self.client.get("/api/activity/")
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["entity_label"], "Mine")


class UserAndLoginHookTest(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_superuser(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)

    def _auth_admin(self):
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_login_logs_security_event(self):
        self._auth_admin()
        self.assertEqual(
            ActivityLog.objects.filter(category="security", action="login").count(), 1)

    def test_create_user_logs_user_created(self):
        self._auth_admin()
        self.client.post("/api/users/", {
            "email": "new@racco1.gov.ph", "username": "newbie",
            "first_name": "New", "last_name": "Bie",
            "role": self.staff_role.id, "password": "newpass123"})
        self.assertEqual(
            ActivityLog.objects.filter(category="user", action="created").count(), 1)
