from unittest.mock import patch

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.google_auth import link_google_account
from accounts.lifecycle import USER_LIFECYCLE
from accounts.models import Role
from accounts.serializers import UserWriteSerializer

User = get_user_model()


class UserLifecycleTest(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)
        self.staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.staff_role)
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _pending(self, email="p@gmail.com"):
        return User.objects.create_user(
            email=email, username=email, password="pending1234",
            status=User.PENDING, email_verified=True)

    def test_table_matches_the_model(self):
        self.assertEqual(USER_LIFECYCLE.check_table(), [])

    def test_archived_cannot_be_reactivated_by_edit(self):
        u = User.objects.create_user(
            email="a@racco1.gov.ph", username="a", password="x12345678",
            role=self.staff_role, status=User.ARCHIVED)
        resp = self.client.patch(f"/api/users/{u.id}/", {"status": "active"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Reactivate", str(resp.data["status"]))
        u.refresh_from_db()
        self.assertEqual(u.status, User.ARCHIVED)

    def test_active_cannot_be_set_pending_by_edit(self):
        resp = self.client.patch(f"/api/users/{self.staff.id}/", {"status": "pending"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.status, User.ACTIVE)

    def test_active_cannot_be_archived_by_edit(self):
        resp = self.client.patch(f"/api/users/{self.staff.id}/", {"status": "archived"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Deactivate", str(resp.data["status"]))

    def test_pending_cannot_be_archived_by_edit(self):
        p = self._pending()
        resp = self.client.patch(f"/api/users/{p.id}/", {"status": "archived"}, format="json")
        self.assertEqual(resp.status_code, 400)
        p.refresh_from_db()
        self.assertEqual(p.status, User.PENDING)

    def test_create_with_another_status_is_refused(self):
        resp = self.client.post("/api/users/", {
            "email": "n@racco1.gov.ph", "username": "n", "first_name": "N",
            "last_name": "N", "role": self.staff_role.id, "status": "archived"},
            format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("status", resp.data)
        self.assertFalse(User.objects.filter(email="n@racco1.gov.ph").exists())

    def test_resending_the_same_status_is_accepted_and_writes_nothing(self):
        resp = self.client.patch(f"/api/users/{self.staff.id}/",
                                 {"status": "active", "first_name": "Sam"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.status, User.ACTIVE)
        self.assertEqual(self.staff.first_name, "Sam")

    def test_move_is_conditional_on_the_status_seen(self):
        p = self._pending()
        self.assertTrue(USER_LIFECYCLE.move(p, User.ACTIVE, extra={"is_active": True}))
        stale = User.objects.get(pk=p.pk)
        stale.status = User.PENDING  # what a second tab still believes
        self.assertFalse(USER_LIFECYCLE.move(stale, User.ARCHIVED, extra={"is_active": False}))
        p.refresh_from_db()
        self.assertEqual(p.status, User.ACTIVE)
        self.assertTrue(p.is_active)

    def test_approve_answers_409_when_the_row_moved_underneath(self):
        p = self._pending()
        with patch("accounts.views.USER_LIFECYCLE.move", return_value=False):
            resp = self.client.post(f"/api/users/{p.id}/approve/", {"role": self.staff_role.id},
                                    format="json")
        self.assertEqual(resp.status_code, 409)
        p.refresh_from_db()
        self.assertEqual(p.status, User.PENDING)
        self.assertIsNone(p.role_id)

    def test_actions_still_move_and_keep_is_active_in_step(self):
        p = self._pending()
        self.assertEqual(self.client.post(f"/api/users/{p.id}/approve/",
                                          {"role": self.staff_role.id}, format="json").status_code, 200)
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertEqual(self.client.post(f"/api/users/{p.id}/archive/").status_code, 200)
        p.refresh_from_db()
        self.assertFalse(p.is_active)
        again = self.client.post(f"/api/users/{p.id}/archive/")
        self.assertEqual(again.status_code, 400)
        self.assertEqual(again.data["detail"], "This account is already deactivated.")
        self.assertEqual(self.client.post(f"/api/users/{p.id}/reactivate/").status_code, 200)
        p.refresh_from_db()
        self.assertTrue(p.is_active)
        self.assertTrue(p.must_change_password)

    def test_google_link_ends_the_emailed_temporary_password(self):
        resp = self.client.post("/api/users/", {
            "email": "g@racco1.gov.ph", "username": "g", "first_name": "G",
            "last_name": "G", "role": self.staff_role.id}, format="json")
        self.assertEqual(resp.status_code, 201)
        temp = resp.data["temp_password"]
        self.client.credentials()
        ok = self.client.post("/api/auth/login/", {"email": "g@racco1.gov.ph", "password": temp})
        self.assertEqual(ok.status_code, 200)
        user = User.objects.get(email="g@racco1.gov.ph")
        link_google_account(user, {"sub": "google-sub-1"})
        user.refresh_from_db()
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.must_change_password)
        again = self.client.post("/api/auth/login/", {"email": "g@racco1.gov.ph", "password": temp})
        self.assertEqual(again.status_code, 401)

    def test_decline_leaves_the_account_unable_to_sign_in(self):
        p = self._pending()
        resp = self.client.post(f"/api/users/{p.id}/decline/")
        self.assertEqual(resp.status_code, 200, resp.data)
        p.refresh_from_db()
        self.assertEqual(p.status, User.ARCHIVED)
        self.assertFalse(p.is_active)

    def test_an_edit_from_a_stale_form_does_not_undo_a_deactivation(self):
        stale = User.objects.get(pk=self.staff.pk)
        self.assertTrue(USER_LIFECYCLE.move(
            self.staff, User.ARCHIVED, extra={"is_active": False}))
        ser = UserWriteSerializer(stale, data={"first_name": "Sam"}, partial=True)
        self.assertTrue(ser.is_valid(), ser.errors)
        ser.save()
        row = User.objects.get(pk=self.staff.pk)
        self.assertEqual(row.first_name, "Sam")
        self.assertEqual(row.status, User.ARCHIVED)
        self.assertFalse(row.is_active)

    def test_an_edit_still_saves_the_email_and_username(self):
        resp = self.client.patch(f"/api/users/{self.staff.id}/",
                                 {"email": "new@racco1.gov.ph"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        row = User.objects.get(pk=self.staff.pk)
        self.assertEqual((row.email, row.username), ("new@racco1.gov.ph", "new@racco1.gov.ph"))

    def test_google_link_after_reactivation_drops_the_old_password(self):
        u = User.objects.create_user(
            email="r@racco1.gov.ph", username="r", password="old-pass-1234",
            role=self.staff_role)
        self.assertEqual(self.client.post(f"/api/users/{u.id}/archive/").status_code, 200)
        self.assertEqual(self.client.post(f"/api/users/{u.id}/reactivate/").status_code, 200)
        u.refresh_from_db()
        self.assertTrue(u.must_change_password)
        self.assertTrue(u.has_usable_password())
        link_google_account(u, {"sub": "google-sub-2"})
        u.refresh_from_db()
        self.assertFalse(u.must_change_password)
        self.assertFalse(u.has_usable_password())
        self.client.credentials()
        again = self.client.post("/api/auth/login/", {
            "email": "r@racco1.gov.ph", "password": "old-pass-1234"})
        self.assertEqual(again.status_code, 401)

    def test_admin_takeover_archives_the_old_administrator_and_is_active(self):
        from accounts.serializers import _complete_admin_takeover
        successor = User.objects.create_user(
            email="admin2@racco1.gov.ph", username="admin2", password="admin5678",
            role=self.admin_role)
        _complete_admin_takeover(successor)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.status, User.ARCHIVED)
        self.assertFalse(self.admin.is_active)
