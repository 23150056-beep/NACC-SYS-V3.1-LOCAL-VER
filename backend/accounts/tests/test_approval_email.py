"""The email a Google applicant gets when the ISA approves them."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.email_notifications import SAFETY
from accounts.models import Role
from children import notifications
from children.notifications import (
    build_access_approved_payload, send_access_approved_notification)

User = get_user_model()
SENDER = "accounts.views.send_access_approved_notification"


class ApprovalEmailViewTest(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psych_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)
        self.google = User.objects.create_user(
            email="g@gmail.com", username="g@gmail.com", status=User.PENDING,
            requested_role=self.psych_role, google_sub="sub-g",
            email_verified=True)
        self.typed = User.objects.create_user(
            email="t@gmail.com", username="t@gmail.com", status=User.PENDING,
            requested_role=self.psych_role, email_verified=True)
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def approve(self, user, role=None):
        body = {} if role is None else {"role": role.id}
        return self.client.post(f"/api/users/{user.id}/approve/", body)

    def test_google_approval_sends_once_and_reports_queued(self):
        with patch(SENDER, return_value=True) as sender:
            resp = self.approve(self.google, self.staff_role)
        self.assertEqual(resp.status_code, 200, resp.data)
        sender.assert_called_once_with(self.google, self.staff_role)
        self.assertIs(resp.data["email_queued"], True)
        self.assertEqual(resp.data["email"], "g@gmail.com")

    def test_typed_signup_is_not_emailed(self):
        with patch(SENDER) as sender:
            resp = self.approve(self.typed, self.staff_role)
        self.assertEqual(resp.status_code, 200, resp.data)
        sender.assert_not_called()
        self.assertIs(resp.data["email_queued"], False)

    def test_decline_sends_nothing(self):
        with patch(SENDER) as sender:
            resp = self.client.post(f"/api/users/{self.google.id}/decline/")
        self.assertEqual(resp.status_code, 200)
        sender.assert_not_called()

    def test_refused_approvals_send_nothing(self):
        unverified = User.objects.create_user(
            email="u@gmail.com", username="u@gmail.com", status=User.PENDING,
            google_sub="sub-u", email_verified=False)
        with patch(SENDER) as sender:
            self.assertEqual(self.approve(unverified, self.staff_role).status_code, 400)
            self.assertEqual(self.approve(self.google).status_code, 400)
            self.assertEqual(
                self.approve(self.google, self.admin_role).status_code, 400)
        sender.assert_not_called()
        self.google.refresh_from_db()
        self.assertEqual(self.google.status, User.PENDING)


@override_settings(SITE_URL="https://nacc.example.ph",
                   BREVO_SENDER_NAME="NACC SYS",
                   BREVO_SENDER_EMAIL="sender@example.ph")
class ApprovalEmailPayloadTest(TestCase):
    def setUp(self):
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psych_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.user = User.objects.create_user(
            email="ana@gmail.com", username="ana@gmail.com", first_name="Ana", last_name="Cruz",
            google_sub="sub-ana", email_verified=True)

    def test_subject_recipient_and_role_words(self):
        staff = build_access_approved_payload(self.user, self.staff_role)
        self.assertEqual(staff["subject"], "Your NACC SYS access was approved")
        self.assertEqual(staff["to"][0]["email"], "ana@gmail.com")
        self.assertIn("as a Social Worker (SW).", staff["textContent"])
        self.assertIn("as a Social Worker (SW).", staff["htmlContent"])
        psych = build_access_approved_payload(self.user, self.psych_role)
        self.assertIn("as a Psychologist.", psych["textContent"])

    def test_link_safety_and_no_credential(self):
        payload = build_access_approved_payload(self.user, self.staff_role)
        for part in ("htmlContent", "textContent"):
            self.assertIn("https://nacc.example.ph/login", payload[part])
            self.assertIn("Sign in with Google", payload[part].replace(
                "&ldquo;", '"').replace("&rdquo;", '"'))
        self.assertIn(SAFETY, payload["textContent"])
        self.assertIn(SAFETY.replace("&", "&amp;"), payload["htmlContent"].replace("&#x27;", "'"))
        self.assertNotIn("temporary password", payload["textContent"].lower())
        self.assertNotIn("temporary password", payload["htmlContent"].lower())
        self.assertNotIn("code is", payload["textContent"].lower())

    def test_no_site_means_no_button(self):
        with patch.object(notifications, "sign_in_url", return_value=""):
            payload = build_access_approved_payload(self.user, self.staff_role)
        self.assertNotIn("Go to NACC SYS", payload["htmlContent"])
        self.assertNotIn("/login", payload["textContent"])

    def test_a_name_is_escaped(self):
        self.user.first_name = "<b>Ana</b>"; self.user.last_name = ""
        payload = build_access_approved_payload(self.user, self.staff_role)
        self.assertNotIn("<b>Ana</b>", payload["htmlContent"])
        self.assertIn("&lt;b&gt;Ana&lt;/b&gt;", payload["htmlContent"])

    def test_safety_sentence_matches_the_catalogue(self):
        # Imported lazily in the builder; this pins that it is that sentence.
        from accounts import email_notifications
        self.assertIs(email_notifications.SAFETY, SAFETY)


@override_settings(SITE_URL="https://nacc.example.ph")
class ApprovalEmailSendTest(TestCase):
    def setUp(self):
        self.role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email="ana@gmail.com", username="ana@gmail.com", first_name="Ana", last_name="Cruz",
            google_sub="sub-ana")

    @override_settings(BREVO_API_KEY="")
    def test_without_a_key_it_logs_and_returns_false(self):
        with self.assertLogs("children.notifications", "WARNING") as logs:
            self.assertFalse(send_access_approved_notification(self.user, self.role))
        self.assertIn(
            "EMAIL (console) access_approved to ana@gmail.com: "
            "Your NACC SYS access was approved", logs.output[0])

    @override_settings(BREVO_API_KEY="k")
    def test_without_an_address_it_returns_false(self):
        self.user.email = ""
        with patch.object(notifications, "_post") as post:
            self.assertFalse(send_access_approved_notification(self.user, self.role))
        post.assert_not_called()

    @override_settings(BREVO_API_KEY="k")
    def test_with_a_key_it_posts_after_commit(self):
        with patch.object(notifications, "_post") as post, \
                patch.object(notifications.threading, "Thread") as thread:
            thread.side_effect = lambda target, args, daemon: type(
                "T", (), {"start": lambda self: target(*args)})()
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.assertTrue(
                    send_access_approved_notification(self.user, self.role))
            self.assertEqual(len(callbacks), 1)
        post.assert_called_once()
        payload, label = post.call_args.args
        self.assertEqual(label, "access approved email")
        self.assertEqual(payload["to"][0]["email"], "ana@gmail.com")
