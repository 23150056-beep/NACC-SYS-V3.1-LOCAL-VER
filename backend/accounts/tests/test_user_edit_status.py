"""Editing an account never changes its status, address spelling or username."""

from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import Role, User
from accounts.google_auth import link_google_account

URL = "/api/users/{}/"


class UserEditRulesTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin@racco1.gov.ph",
            password="admin1234", role=self.admin_role)
        self.staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff@racco1.gov.ph",
            password="staff1234", role=self.staff_role)
        self.client.force_authenticate(self.admin)

    def _patch(self, user, body):
        return self.client.patch(URL.format(user.pk), body, format="json")

    # --- status ---------------------------------------------------------

    def test_declined_typed_signup_cannot_be_revived_by_edit(self):
        declined = User.objects.create_user(
            email="no@x.ph", username="no@x.ph", password="pw123456",
            status=User.ARCHIVED, email_verified=False)
        r = self._patch(declined, {"status": "active"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("status", r.data)
        declined.refresh_from_db()
        self.assertEqual(declined.status, User.ARCHIVED)
        self.assertFalse(declined.is_active)

    def test_deactivated_admin_cannot_be_revived_by_edit(self):
        old = User.objects.create_user(
            email="old@x.ph", username="old@x.ph", password="pw123456",
            role=self.admin_role, status=User.ARCHIVED)
        self.assertEqual(self._patch(old, {"status": "active"}).status_code, 400)
        old.refresh_from_db()
        self.assertEqual(old.status, User.ARCHIVED)

    def test_deactivated_staff_is_reactivated_only_by_the_action(self):
        self.client.post(URL.format(self.staff.pk) + "archive/")
        self.assertEqual(self._patch(self.staff, {"status": "active"}).status_code, 400)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.status, User.ARCHIVED)
        self.assertEqual(
            self.client.post(URL.format(self.staff.pk) + "reactivate/").status_code, 200)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.must_change_password)

    def test_pending_to_archived_and_active_to_archived_are_refused(self):
        pending = User.objects.create_user(
            email="p@x.ph", username="p@x.ph", password="pw123456",
            status=User.PENDING)
        self.assertEqual(self._patch(pending, {"status": "archived"}).status_code, 400)
        self.assertEqual(self._patch(self.staff, {"status": "archived"}).status_code, 400)
        pending.refresh_from_db()
        self.staff.refresh_from_db()
        self.assertEqual(pending.status, User.PENDING)
        self.assertEqual(self.staff.status, User.ACTIVE)

    def test_same_status_is_accepted_on_patch_and_put(self):
        self.assertEqual(self._patch(self.staff, {"status": "active"}).status_code, 200)
        r = self.client.put(URL.format(self.staff.pk), {
            "email": self.staff.email, "first_name": "S", "last_name": "T",
            "role": self.staff_role.id, "status": "active"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)

    def test_the_last_administrator_still_cannot_be_archived(self):
        self.assertEqual(
            self.client.post(URL.format(self.admin.pk) + "archive/").status_code, 400)
        self.assertEqual(self._patch(self.admin, {"status": "archived"}).status_code, 400)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.status, User.ACTIVE)

    def test_must_change_password_is_not_touched_by_edits(self):
        self.staff.must_change_password = True
        self.staff.save(update_fields=["must_change_password"])
        self._patch(self.staff, {"first_name": "Sam", "must_change_password": False})
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.must_change_password)

    def test_create_with_a_non_active_status_is_refused(self):
        r = self.client.post("/api/users/", {
            "email": "n@x.ph", "first_name": "N", "last_name": "N",
            "role": self.staff_role.id, "status": "archived"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(User.objects.filter(email="n@x.ph").exists())

    # --- email ----------------------------------------------------------

    def test_email_is_lowercased_on_create_and_update(self):
        r = self.client.post("/api/users/", {
            "email": "  New.Person@Racco1.gov.ph ", "first_name": "N", "last_name": "P",
            "role": self.staff_role.id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        made = User.objects.get(pk=r.data["id"])
        self.assertEqual(made.email, "new.person@racco1.gov.ph")
        self.assertEqual(made.username, made.email)
        self.assertEqual(self._patch(self.staff, {"email": "Staff.Two@X.PH"}).status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.email, "staff.two@x.ph")
        self.assertEqual(self.staff.username, "staff.two@x.ph")

    def test_case_insensitive_duplicate_refused_on_create_and_update(self):
        r = self.client.post("/api/users/", {
            "email": "STAFF@racco1.gov.ph", "first_name": "D", "last_name": "D",
            "role": self.staff_role.id}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data)
        r = self._patch(self.staff, {"email": "Admin@Racco1.gov.ph"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data)

    def test_own_address_in_another_case_is_fine(self):
        self.assertEqual(self._patch(self.staff, {"email": "STAFF@racco1.gov.ph"}).status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.email, "staff@racco1.gov.ph")

    def test_mixed_case_stored_address_still_signs_in(self):
        User.objects.create_user(
            email="Mixed@Racco1.gov.ph", username="mixed", password="pw123456",
            role=self.staff_role)
        for typed in ("Mixed@Racco1.gov.ph", "mixed@racco1.gov.ph"):
            r = self.client.post("/api/auth/login/", {"email": typed, "password": "pw123456"})
            self.assertEqual(r.status_code, 200, typed)

    # --- username -------------------------------------------------------

    def test_username_is_not_writable_and_follows_email(self):
        r = self.client.post("/api/users/", {
            "email": "u@x.ph", "username": "admin@racco1.gov.ph", "first_name": "U",
            "last_name": "U", "role": self.staff_role.id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(User.objects.get(pk=r.data["id"]).username, "u@x.ph")
        self.assertEqual(self._patch(self.staff, {"username": "admin@racco1.gov.ph"}).status_code, 200)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.username, self.staff.email)


class ApproveDeclineRaceTest(APITestCase):
    def setUp(self):
        admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin@racco1.gov.ph",
            password="admin1234", role=admin_role)
        self.client.force_authenticate(self.admin)

    def _pending(self):
        return User.objects.create_user(
            email="req@x.ph", username="req@x.ph", password="pw123456",
            status=User.PENDING, email_verified=True)

    def test_approve_then_decline_second_is_refused(self):
        u = self._pending()
        self.assertEqual(self.client.post(
            f"/api/users/{u.pk}/approve/", {"role": self.staff_role.id}, format="json").status_code, 200)
        r = self.client.post(f"/api/users/{u.pk}/decline/")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["detail"], "This account is not awaiting approval.")
        u.refresh_from_db()
        self.assertEqual(u.status, User.ACTIVE)

    def test_decline_then_approve_second_is_refused(self):
        u = self._pending()
        self.assertEqual(self.client.post(f"/api/users/{u.pk}/decline/").status_code, 200)
        r = self.client.post(
            f"/api/users/{u.pk}/approve/", {"role": self.staff_role.id}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["detail"], "This account is not awaiting approval.")
        u.refresh_from_db()
        self.assertEqual(u.status, User.ARCHIVED)


class GoogleKeepsTempPasswordFlagTest(APITestCase):
    def _user(self, usable):
        u = User.objects.create_user(
            email="g@x.ph", username="g@x.ph", password="pw123456",
            must_change_password=True)
        if not usable:
            u.set_unusable_password()
            u.save()
        return u

    def test_usable_password_keeps_the_flag(self):
        u = self._user(usable=True)
        link_google_account(u, {"sub": "s1"})
        u.refresh_from_db()
        self.assertTrue(u.must_change_password)

    def test_no_usable_password_clears_the_flag(self):
        u = self._user(usable=False)
        link_google_account(u, {"sub": "s2"})
        u.refresh_from_db()
        self.assertFalse(u.must_change_password)


class ArchiveAndAddressEdgesTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin@racco1.gov.ph",
            password="admin1234", role=self.admin_role)
        self.client.force_authenticate(self.admin)

    def test_archiving_a_pending_request_is_refused(self):
        pending = User.objects.create_user(
            email="p@x.ph", username="p@x.ph", password="pw123456",
            status=User.PENDING)
        r = self.client.post(URL.format(pending.pk) + "archive/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Decline", r.data["detail"])
        pending.refresh_from_db()
        self.assertEqual(pending.status, User.PENDING)

    def test_exact_case_duplicate_gets_the_agreed_sentence(self):
        User.objects.create_user(
            email="a@x.ph", username="a@x.ph", password="pw123456",
            role=self.staff_role)
        other = User.objects.create_user(
            email="b@x.ph", username="b@x.ph", password="pw123456")
        r = self.client.patch(URL.format(other.pk), {"email": "a@x.ph"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["email"][0], "Another account already uses this address.")

    def test_address_held_by_another_row_as_a_username_is_refused(self):
        User.objects.create_user(
            email="who@x.ph", username="old-login@x.ph", password="pw123456")
        other = User.objects.create_user(
            email="b@x.ph", username="b@x.ph", password="pw123456")
        r = self.client.patch(
            URL.format(other.pk), {"email": "old-login@x.ph"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("email", r.data)

    def test_user_payload_says_whether_there_is_a_password(self):
        google = User.objects.create_user(
            email="g@x.ph", username="g@x.ph", password="pw123456",
            role=self.staff_role, google_sub="sub-1")
        google.set_unusable_password()
        google.save()
        r = self.client.get(URL.format(google.pk))
        self.assertIs(r.data["has_password"], False)
        r = self.client.get(URL.format(self.admin.pk))
        self.assertIs(r.data["has_password"], True)
