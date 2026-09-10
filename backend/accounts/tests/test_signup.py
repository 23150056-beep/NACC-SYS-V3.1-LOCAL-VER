"""Password sign-up: a request to be let in, never an account that is in.

This endpoint is reachable by anyone on the internet and the system holds
child case records, so the tests that matter here are the ones asserting what
it does NOT do. Registering is not the same as being let in — the same rule
the Google sign-up path already follows.
"""
from django.test import TestCase
from rest_framework.test import APITestCase

from django.core.cache import cache

from accounts.models import Role, User

URL = "/api/auth/signup/"
LOGIN = "/api/auth/login/"


def _payload(**over):
    body = {
        "first_name": "Maria",
        "last_name": "Santos",
        "email": "maria@gmail.com",
        "password": "a-long-enough-passphrase-9",
        "requested_role": Role.STAFF,
    }
    body.update(over)
    return body


class SignupCreatesARequestNotAnAccountTest(APITestCase):
    def setUp(self):
        Role.objects.create(role_name=Role.STAFF)
        Role.objects.create(role_name=Role.PSYCHOLOGIST)
        Role.objects.create(role_name=Role.ADMINISTRATOR)
        cache.clear()

    def test_it_accepts_the_request(self):
        resp = self.client.post(URL, _payload(), format="json")
        self.assertEqual(resp.status_code, 202, resp.data)

    def test_the_account_is_pending_with_no_role(self):
        self.client.post(URL, _payload(), format="json")
        user = User.objects.get(email="maria@gmail.com")
        self.assertEqual(user.status, User.PENDING)
        self.assertIsNone(user.role)

    def test_the_account_cannot_authenticate(self):
        """The whole safety of this feature. is_active follows status, and
        both Django's backend and SimpleJWT check is_active — so a pending
        account cannot sign in even with the correct password."""
        self.client.post(URL, _payload(), format="json")
        user = User.objects.get(email="maria@gmail.com")
        self.assertFalse(user.is_active)

        resp = self.client.post(
            LOGIN, {"email": "maria@gmail.com",
                    "password": "a-long-enough-passphrase-9"}, format="json")
        self.assertNotEqual(resp.status_code, 200)
        self.assertNotIn("access", resp.data)

    def test_it_never_returns_a_token(self):
        resp = self.client.post(URL, _payload(), format="json")
        for key in ("access", "refresh", "token"):
            self.assertNotIn(key, resp.data)

    def test_the_stated_role_is_recorded_as_a_claim_only(self):
        self.client.post(URL, _payload(requested_role=Role.PSYCHOLOGIST),
                         format="json")
        user = User.objects.get(email="maria@gmail.com")
        self.assertEqual(user.requested_role.role_name, Role.PSYCHOLOGIST)
        self.assertIsNone(user.role)

    def test_it_shows_up_for_an_administrator_to_approve(self):
        self.client.post(URL, _payload(), format="json")
        self.assertTrue(
            User.objects.filter(status=User.PENDING,
                                email="maria@gmail.com").exists())


class SignupRefusalsTest(APITestCase):
    def setUp(self):
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        Role.objects.create(role_name=Role.PSYCHOLOGIST)
        Role.objects.create(role_name=Role.ADMINISTRATOR)
        cache.clear()

    def test_nobody_can_request_administrator(self):
        """Administrators authenticate with a password and are created by
        another administrator. The admin account is the agency's recovery
        path; letting the internet queue up for it is not a queue, it is a
        target."""
        resp = self.client.post(
            URL, _payload(requested_role=Role.ADMINISTRATOR), format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        self.assertFalse(User.objects.filter(email="maria@gmail.com").exists())

    def test_an_address_already_in_use_is_refused_without_saying_so(self):
        """The refusal must be indistinguishable from the one an unused-but-
        rejected address gets. If they differ, this open form becomes a way to
        discover which addresses belong to agency staff — the same reasoning
        that makes the three Google refusals share one message."""
        User.objects.create_user(email="taken@gmail.com", username="taken",
                                 password="x", role=self.staff_role)
        User.objects.create_user(email="gone2@gmail.com", username="gone2",
                                 password="x", role=self.staff_role,
                                 status=User.ARCHIVED)

        active = self.client.post(URL, _payload(email="taken@gmail.com"),
                                  format="json")
        archived = self.client.post(URL, _payload(email="gone2@gmail.com"),
                                    format="json")
        self.assertEqual(active.status_code, 400)
        self.assertEqual(archived.status_code, 400)
        self.assertEqual(str(active.data), str(archived.data))

    def test_a_deactivated_person_cannot_re_register_themselves(self):
        User.objects.create_user(email="gone@gmail.com", username="gone",
                                 password="x", role=self.staff_role,
                                 status=User.ARCHIVED)
        self.client.post(URL, _payload(email="gone@gmail.com"), format="json")
        self.assertEqual(User.objects.get(email="gone@gmail.com").status,
                         User.ARCHIVED)

    def test_a_weak_password_is_refused(self):
        resp = self.client.post(URL, _payload(password="123"), format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(User.objects.filter(email="maria@gmail.com").exists())

    def test_a_missing_field_is_refused(self):
        resp = self.client.post(URL, _payload(email=""), format="json")
        self.assertEqual(resp.status_code, 400)

    def test_it_is_throttled_like_the_google_path(self):
        for i in range(40):
            self.client.post(URL, _payload(email=f"n{i}@gmail.com"),
                             format="json")
        resp = self.client.post(URL, _payload(email="last@gmail.com"),
                                format="json")
        self.assertIn(resp.status_code, (400, 429))


class SignupIsOpenToAnyoneTest(TestCase):
    def test_no_authentication_is_required(self):
        Role.objects.create(role_name=Role.STAFF)
        cache.clear()
        resp = self.client.post(URL, _payload(), content_type="application/json")
        self.assertNotIn(resp.status_code, (401, 403))


class SignupReachesTheApprovalQueueTest(APITestCase):
    """The half that is easy to leave unbuilt.

    A form that writes a PENDING row and a queue that only ever expected
    Google sign-ups would both pass their own tests while the request sat
    somewhere no administrator looks. This walks the whole loop: request,
    queue, approval, sign-in.
    """

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin",
            password="admin1234", role=self.admin_role)
        cache.clear()

    def _as_admin(self):
        token = self.client.post(
            LOGIN, {"email": "admin@racco1.gov.ph", "password": "admin1234"},
            format="json").data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_the_request_shows_up_in_the_directory(self):
        self.client.post(URL, _payload(), format="json")
        self._as_admin()
        rows = self.client.get("/api/users/").data
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        mine = [r for r in rows if r["email"] == "maria@gmail.com"]
        self.assertEqual(len(mine), 1, "the request is not in the directory")
        # The screen files it under "Awaiting approval" off this field alone.
        self.assertEqual(mine[0]["status"], User.PENDING)

    def _confirm_email(self, email):
        from django.core.cache import cache
        from accounts import email_verification

        entry = cache.get(email_verification.code_key(email))
        self.assertIsNotNone(entry, "signing up should have issued a code")
        return self.client.post("/api/auth/signup/verify-email/",
                                {"email": email, "code": entry["code"]},
                                format="json")

    def test_approval_turns_it_into_an_account_that_can_sign_in(self):
        self.client.post(URL, _payload(), format="json")
        user = User.objects.get(email="maria@gmail.com")

        # A typed address proves itself with the code mailed to it. Approval
        # emails a temporary password, so an unverified one is refused - see
        # test_email_verification. This walks the real flow rather than
        # reaching around it.
        self._confirm_email("maria@gmail.com")

        self._as_admin()
        resp = self.client.post(f"/api/users/{user.id}/approve/",
                                {"role": self.staff_role.id}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)

        user.refresh_from_db()
        self.assertEqual(user.status, User.ACTIVE)
        self.assertEqual(user.role.role_name, Role.STAFF)
        # They chose this password themselves, so unlike an admin-created
        # account there is no temporary one to replace.
        self.assertFalse(user.must_change_password)

        self.client.credentials()
        login = self.client.post(
            LOGIN, {"email": "maria@gmail.com",
                    "password": "a-long-enough-passphrase-9"}, format="json")
        self.assertEqual(login.status_code, 200, login.data)
        self.assertIn("access", login.data)

    def test_approval_still_refuses_to_hand_out_administrator(self):
        """The password door must not become the way around the rule that an
        administrator is only ever created by another administrator."""
        self.client.post(URL, _payload(), format="json")
        user = User.objects.get(email="maria@gmail.com")
        self._as_admin()
        resp = self.client.post(f"/api/users/{user.id}/approve/",
                                {"role": self.admin_role.id}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)
        user.refresh_from_db()
        self.assertEqual(user.status, User.PENDING)

    def test_declining_blocks_a_second_attempt(self):
        self.client.post(URL, _payload(), format="json")
        user = User.objects.get(email="maria@gmail.com")
        self._as_admin()
        self.assertEqual(
            self.client.post(f"/api/users/{user.id}/decline/").status_code, 200)

        self.client.credentials()
        cache.clear()
        again = self.client.post(URL, _payload(), format="json")
        self.assertEqual(again.status_code, 400)
        user.refresh_from_db()
        self.assertEqual(user.status, User.ARCHIVED)
