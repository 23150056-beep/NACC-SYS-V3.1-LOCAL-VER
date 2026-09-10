"""Prove the address exists before anything is sent to it.

The sign-up form verifies nothing, and that matters at exactly one moment:
approval emails a temporary password. Approve a request whose address was
mistyped and the credential goes to whoever owns the typo, or nowhere at all
while the applicant waits for a mail that cannot arrive.

The pattern is the one already used for phone numbers - a six-digit code in
the cache, a few minutes to use it, a limited number of guesses - because a
second way of doing the same thing is a second thing to get wrong.

**Google requests skip it.** Google already verified the address; asking the
applicant to prove it again would be ceremony, and the queue has always
recorded which door a request came through for precisely this reason.

**Approval is refused until it is verified.** That is the whole point: the
check has to bite where the credential is issued, not merely be displayed
beside the row for somebody to notice.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase

from accounts import email_verification
from accounts.models import Role

User = get_user_model()


class SignupAsksForProofTest(APITestCase):
    def setUp(self):
        cache.clear()
        Role.objects.create(role_name=Role.STAFF)

    def _signup(self, email="applicant@racco1.gov.ph"):
        return self.client.post("/api/auth/signup/", {
            "email": email, "password": "Str0ngPass!2026",
            "first_name": "Ana", "last_name": "Lopez",
            "requested_role": "Staff"}, format="json")

    def test_signing_up_still_creates_a_pending_request(self):
        with patch("accounts.email_verification.send_verification_email", return_value=True):
            self.assertEqual(202, self._signup().status_code)
        user = User.objects.get(email="applicant@racco1.gov.ph")
        self.assertEqual(User.PENDING, user.status)

    def test_the_address_starts_unverified(self):
        with patch("accounts.email_verification.send_verification_email", return_value=True):
            self._signup()
        self.assertFalse(User.objects.get(email="applicant@racco1.gov.ph").email_verified)

    def test_a_code_is_sent_to_the_address_given(self):
        with patch("accounts.email_verification.send_verification_email", return_value=True) as sent:
            self._signup()
        self.assertEqual("applicant@racco1.gov.ph", sent.call_args[0][0])

    def test_the_right_code_verifies_the_address(self):
        with patch("accounts.email_verification.send_verification_email", return_value=True):
            self._signup()
        code = cache.get(email_verification.code_key("applicant@racco1.gov.ph"))["code"]
        response = self.client.post("/api/auth/signup/verify-email/", {
            "email": "applicant@racco1.gov.ph", "code": code}, format="json")
        self.assertEqual(200, response.status_code, response.data)
        self.assertTrue(User.objects.get(email="applicant@racco1.gov.ph").email_verified)

    def test_a_wrong_code_does_not(self):
        with patch("accounts.email_verification.send_verification_email", return_value=True):
            self._signup()
        response = self.client.post("/api/auth/signup/verify-email/", {
            "email": "applicant@racco1.gov.ph", "code": "000000"}, format="json")
        self.assertEqual(400, response.status_code)
        self.assertFalse(User.objects.get(email="applicant@racco1.gov.ph").email_verified)

    def test_guessing_is_limited(self):
        with patch("accounts.email_verification.send_verification_email", return_value=True):
            self._signup()
        for _ in range(email_verification.MAX_ATTEMPTS + 1):
            self.client.post("/api/auth/signup/verify-email/", {
                "email": "applicant@racco1.gov.ph", "code": "000000"}, format="json")
        code = (cache.get(email_verification.code_key("applicant@racco1.gov.ph")) or {}).get("code")
        self.assertIsNone(code, "the code should be burned after too many guesses")

    def test_an_unknown_address_is_answered_the_same_way(self):
        # Same reasoning as the sign-up form's single refusal message: a
        # different answer here would turn this into a way to ask whether
        # somebody works at the agency.
        response = self.client.post("/api/auth/signup/verify-email/", {
            "email": "stranger@example.com", "code": "000000"}, format="json")
        self.assertEqual(400, response.status_code)


class ApprovalRequiresItTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR))
        self.applicant = User.objects.create_user(
            email="applicant@racco1.gov.ph", username="applicant",
            password="pass1234", status=User.PENDING)
        self.applicant.role = None
        self.applicant.save()
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _approve(self):
        return self.client.post(f"/api/users/{self.applicant.id}/approve/",
                                {"role": self.staff_role.id}, format="json")

    def test_an_unverified_address_cannot_be_approved(self):
        response = self._approve()
        self.assertEqual(400, response.status_code)
        self.assertIn("verif", str(response.data).lower())

    def test_a_verified_address_can(self):
        self.applicant.email_verified = True
        self.applicant.save(update_fields=["email_verified"])
        self.assertEqual(200, self._approve().status_code, )

    def test_a_google_request_counts_as_verified(self):
        # Google checked the address. Making the applicant prove it again
        # would be ceremony, and the queue records which door they used.
        self.applicant.google_sub = "google-abc"
        self.applicant.email_verified = True
        self.applicant.save(update_fields=["google_sub", "email_verified"])
        self.assertEqual(200, self._approve().status_code)
