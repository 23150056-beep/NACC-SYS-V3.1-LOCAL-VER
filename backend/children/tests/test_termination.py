from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import Child, TerminationRecord

User = get_user_model()


class TerminationApiTest(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="pass1234", role=self.admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234", role=self.psy_role)
        self.other = User.objects.create_user(
            email="o@racco1.gov.ph", username="o", password="pass1234", role=self.psy_role)
        self.staff = User.objects.create_user(
            email="s@racco1.gov.ph", username="s", password="pass1234", role=self.staff_role)
        self.child = Child.objects.create(social_worker=self.staff, 
            fullname="Ana", case_type="Adoption", assigned_psychologist=self.psy)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _payload(self):
        return {"reason_category": "Reunified with family", "note": "Returned to biological family."}

    def test_assigned_psychologist_can_terminate(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/terminate/", self._payload(), format="json")
        self.assertEqual(resp.status_code, 200)
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, Child.INACTIVE)
        rec = TerminationRecord.objects.get()
        self.assertEqual(rec.terminated_by, self.psy)
        self.assertEqual(rec.reason_category, "Reunified with family")

    def test_admin_can_terminate(self):
        self._auth("a@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/terminate/", self._payload(), format="json")
        self.assertEqual(resp.status_code, 200)

    def test_unassigned_psychologist_cannot_terminate(self):
        self._auth("o@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/terminate/", self._payload(), format="json")
        # Queryset scoping hides the child from an unassigned psychologist (404).
        self.assertIn(resp.status_code, (403, 404))
        self.child.refresh_from_db()
        self.assertEqual(self.child.status, Child.ACTIVE)

    def test_staff_cannot_terminate(self):
        self._auth("s@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/terminate/", self._payload(), format="json")
        self.assertEqual(resp.status_code, 403)

    def test_note_is_required(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/terminate/", {
            "reason_category": "Other", "note": "  "}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("note", resp.data)
        self.assertEqual(TerminationRecord.objects.count(), 0)

    def test_valid_reason_is_required(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/terminate/", {
            "reason_category": "Because", "note": "x"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason_category", resp.data)

    def test_already_inactive_rejected(self):
        self.child.status = Child.INACTIVE
        self.child.save()
        self._auth("a@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{self.child.id}/terminate/", self._payload(), format="json")
        self.assertEqual(resp.status_code, 400)

    def test_terminated_child_hidden_from_default_list(self):
        self._auth("p@racco1.gov.ph")
        self.client.post(f"/api/children/{self.child.id}/terminate/", self._payload(), format="json")
        names = [c["fullname"] for c in self.client.get("/api/children/").data]
        self.assertNotIn("Ana", names)
        with_inactive = self.client.get("/api/children/?include_archived=true").data
        self.assertIn("Ana", [c["fullname"] for c in with_inactive])

    def test_serializer_exposes_termination_details(self):
        self._auth("p@racco1.gov.ph")
        self.client.post(f"/api/children/{self.child.id}/terminate/", self._payload(), format="json")
        data = self.client.get(f"/api/children/{self.child.id}/").data
        self.assertEqual(data["status"], "inactive")
        self.assertEqual(data["termination"]["reason_category"], "Reunified with family")

    def test_terminate_hidden_child_returns_404_for_other_psychologist(self):
        hidden = Child.objects.create(fullname="Ben", assigned_psychologist=self.other)
        self._auth("p@racco1.gov.ph")
        resp = self.client.post(f"/api/children/{hidden.id}/terminate/", self._payload(), format="json")
        self.assertEqual(resp.status_code, 404)
