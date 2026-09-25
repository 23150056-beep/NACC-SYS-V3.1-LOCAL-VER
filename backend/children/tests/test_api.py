from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model
from accounts.models import Role
from children.models import Child

User = get_user_model()


class ChildApiTest(APITestCase):
    def setUp(self):
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psychologist_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.staff_role)
        self.psychologist = User.objects.create_user(
            email="c@racco1.gov.ph", username="c", password="couns1234",
            role=self.psychologist_role)

    def _auth(self, email, password):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": password}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_staff_can_create_child(self):
        self._auth("staff@racco1.gov.ph", "staff1234")
        resp = self.client.post("/api/children/", {
            "fullname": "Juan Cruz", "gender": "Male", "case_type": "Foster Care"})
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(Child.objects.filter(fullname="Juan Cruz").exists())

    def test_psychologist_cannot_create_child(self):
        self._auth("c@racco1.gov.ph", "couns1234")
        resp = self.client.post("/api/children/", {"fullname": "X"})
        self.assertEqual(resp.status_code, 403)

    def test_psychologist_can_view_children(self):
        Child.objects.create(fullname="Visible Child", case_type="Foster Care",
                             assigned_psychologist=self.psychologist)
        self._auth("c@racco1.gov.ph", "couns1234")
        resp = self.client.get("/api/children/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    def test_a_terminated_child_drops_out_of_the_list(self):
        """Terminate is the only way a case goes inactive now. Staff cannot
        take it, so this goes through the child's own psychologist."""
        child = Child.objects.create(fullname="Ana Lopez", case_type="Adoption",
                                     assigned_psychologist=self.psychologist)
        self._auth("c@racco1.gov.ph", "couns1234")
        resp = self.client.post(f"/api/children/{child.id}/terminate/", {
            "reason_category": "Services completed", "note": "Case closed."})
        self.assertEqual(200, resp.status_code, resp.data)
        names = [c["fullname"] for c in self.client.get("/api/children/").data]
        self.assertNotIn("Ana Lopez", names)

    def test_create_child_with_valid_case_category_persists_and_returns_it(self):
        self._auth("staff@racco1.gov.ph", "staff1234")
        resp = self.client.post("/api/children/", {
            "fullname": "Nico Reyes", "gender": "Male", "case_category": "Abandoned"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["case_category"], "Abandoned")
        child = Child.objects.get(fullname="Nico Reyes")
        self.assertEqual(child.case_category, "Abandoned")

    def test_update_child_with_valid_case_category_persists_and_returns_it(self):
        self._auth("staff@racco1.gov.ph", "staff1234")
        child = Child.objects.create(social_worker=self.staff, fullname="Lena Santos")
        resp = self.client.put(f"/api/children/{child.id}/", {
            "fullname": "Lena Santos", "case_category": "Neglected"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["case_category"], "Neglected")
        child.refresh_from_db()
        self.assertEqual(child.case_category, "Neglected")

    def test_invalid_case_category_rejected(self):
        self._auth("staff@racco1.gov.ph", "staff1234")
        resp = self.client.post("/api/children/", {
            "fullname": "Bad Category", "case_category": "Not A Real Category"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("case_category", resp.data)
