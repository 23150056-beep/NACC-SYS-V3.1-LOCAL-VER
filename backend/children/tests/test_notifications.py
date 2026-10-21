import io
import json
import urllib.error
from unittest import mock
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import Role, User
from children.models import Child
from accounts.email_notifications import case_request
from children.notifications import (
    _post, build_temporary_password_payload, send_temporary_password_notification,
)


@override_settings(BREVO_API_KEY="test-key", BREVO_SENDER_EMAIL="from@racco1.gov.ph",
                   BREVO_SENDER_NAME="NACC RACCO1")
class AssignmentEmailContentTest(TestCase):
    """What leaves the building. Brevo is a processor outside the agency's
    data-processing agreements, so this asserts the absence of case data as
    strictly as it asserts the presence of the case reference."""

    def setUp(self):
        self.psych_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psych = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="x",
            role=self.psych_role, first_name="Maria", last_name="Dela Cruz")
        self.sw = User.objects.create_user(
            email="sw@racco1.gov.ph", username="sw", password="x",
            role=Role.objects.create(role_name=Role.STAFF),
            first_name="Editha", last_name="Pascua")

    def _sent(self, child, psychologist=None):
        """The payloads case_request handed to the sender, read at the seam
        instead of standing up an HTTP server."""
        with patch("accounts.email_notifications._queue") as queue:
            case_request(child, psychologist or self.psych, by=self.sw)
        return [call.args[0] for call in queue.call_args_list]

    def test_the_case_reference_is_included_as_every_screen_writes_it(self):
        child = Child.objects.create(fullname="Ana Reyes")
        (payload,) = self._sent(child)
        ref = f"C-{child.id:04d}"
        self.assertIn(ref, payload["htmlContent"])
        self.assertIn(ref, payload["textContent"])
        # The raw id the old email printed ("Case number: 42") is gone.
        self.assertNotIn(f"Case number: {child.id}", payload["htmlContent"])

    def test_the_child_is_never_named(self):
        child = Child.objects.create(
            first_name="Ana", last_name="Reyes", fullname="Ana Reyes",
            case_type="Adoption", birth_date="2016-04-09")
        (payload,) = self._sent(child)
        everything = " ".join([payload["subject"], payload["htmlContent"],
                               payload["textContent"]])
        for leak in ("Ana", "Reyes", "Adoption", "2016", "09 Apr"):
            self.assertNotIn(leak, everything)

    def test_it_is_still_the_question_it_was(self):
        (payload,) = self._sent(Child.objects.create(fullname="Ana Reyes"))
        self.assertEqual("A case is waiting for your answer", payload["subject"])
        self.assertIn("once you accept it", payload["textContent"])

    def test_it_says_who_asked(self):
        (payload,) = self._sent(Child.objects.create(fullname="Ana Reyes"))
        self.assertIn("Editha Pascua asked you", payload["textContent"])

    def test_it_goes_to_the_psychologist(self):
        (payload,) = self._sent(Child.objects.create(fullname="Ana Reyes"))
        self.assertEqual("p@racco1.gov.ph", payload["to"][0]["email"])

    def test_no_api_key_means_no_send(self):
        child = Child.objects.create(fullname="Ana Reyes")
        with override_settings(BREVO_API_KEY=""), \
                patch("accounts.email_notifications._queue") as queue, \
                self.assertLogs("accounts.email_notifications", level="WARNING"):
            case_request(child, self.psych, by=self.sw)
        queue.assert_not_called()

    def test_no_psychologist_sends_nothing(self):
        child = Child.objects.create(fullname="Ana Reyes")
        with patch("accounts.email_notifications._queue") as queue:
            case_request(child, None, by=self.sw)
        queue.assert_not_called()

    def test_a_psychologist_without_an_address_sends_nothing(self):
        self.psych.email = ""
        self.psych.save(update_fields=["email"])
        self.assertEqual([], self._sent(Child.objects.create(fullname="Ana Reyes")))

    def test_temporary_password_is_sent_to_the_user(self):
        payload = build_temporary_password_payload(self.psych, "TempPass9")
        self.assertEqual(payload["to"][0]["email"], "p@racco1.gov.ph")
        self.assertIn("TempPass9", payload["htmlContent"])

    def test_temporary_password_without_an_address_sends_nothing(self):
        self.psych.email = ""
        self.psych.save(update_fields=["email"])
        self.assertFalse(send_temporary_password_notification(self.psych, "TempPass9"))


@override_settings(BREVO_API_KEY="test-key")
class BrevoFailureLoggingTest(SimpleTestCase):
    """More than one kind of message goes through _post. A failure that does
    not name which one sends whoever is reading the logs to the wrong
    feature — this pins the name to the message."""

    def _failing_post(self, description):
        with patch("children.notifications.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("no route")):
            with self.assertLogs("children.notifications", level="ERROR") as captured:
                self.assertFalse(_post({}, description))
        return "\n".join(captured.output)

    def test_a_failed_temporary_password_email_says_so(self):
        logged = self._failing_post("temporary password email")
        self.assertIn("temporary password email", logged)
        self.assertNotIn("case_request email", logged)

    def test_a_failed_case_email_still_says_so(self):
        self.assertIn("case_request email", self._failing_post("case_request email"))


@override_settings(BREVO_API_KEY="test-key")
class AssignmentEmailTriggerTest(APITestCase):
    """When it fires: when a psychologist is asked to take a case, on a new
    record or a change of psychologist — not on every edit to an assigned
    case. Since 28 Sep 2026 picking a psychologist asks them rather than
    assigning (children/assignment.py), so the mail goes out from there."""

    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psych_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)
        self.psych = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="x", role=self.psych_role)
        self.other = User.objects.create_user(
            email="p2@racco1.gov.ph", username="p2", password="x", role=self.psych_role)
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    @patch("accounts.email_notifications.case_request")
    def test_creating_with_an_assignee_notifies(self, mock_send):
        resp = self.client.post("/api/children/", {
            "fullname": "Nico Reyes", "gender": "Male",
            "case_type": "Foster Care", "psychologist": self.psych.id,
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        mock_send.assert_called_once()

    @patch("accounts.email_notifications.case_request")
    def test_creating_without_an_assignee_does_not(self, mock_send):
        resp = self.client.post("/api/children/", {
            "fullname": "Nico Reyes", "gender": "Male", "case_type": "Foster Care",
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        mock_send.assert_not_called()

    @patch("accounts.email_notifications.case_request")
    def test_reassigning_notifies_the_new_psychologist(self, mock_send):
        child = Child.objects.create(fullname="Nico Reyes", assigned_psychologist=self.psych)
        resp = self.client.patch(f"/api/children/{child.id}/",
                                 {"psychologist": self.other.id}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        mock_send.assert_called_once()

    @patch("accounts.email_notifications.case_request")
    def test_editing_an_assigned_case_does_not_re_notify(self, mock_send):
        child = Child.objects.create(fullname="Nico Reyes", assigned_psychologist=self.psych)
        resp = self.client.patch(f"/api/children/{child.id}/",
                                 {"medical_notes": "Updated."}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        mock_send.assert_not_called()


@override_settings(BREVO_API_KEY="test-key")
class BrevoRejectionIsLoggedWithItsReasonTest(SimpleTestCase):
    """Brevo says WHY in the response body. A traceback does not carry it, so
    an unverified sender and a bad key both read as "HTTP Error 400" — and the
    only honest answer to "why did no email arrive" becomes a guess."""

    def _failing_post(self, code, body):
        err = urllib.error.HTTPError(
            "https://api.brevo.com/v3/smtp/email", code, "Bad Request", {},
            io.BytesIO(body.encode()))
        with mock.patch("children.notifications.urllib.request.urlopen",
                        side_effect=err):
            with self.assertLogs("children.notifications", level="ERROR") as cap:
                self.assertFalse(_post({}, "temporary password email"))
        return "\n".join(cap.output)

    def test_an_unverified_sender_says_so(self):
        logged = self._failing_post(
            400, '{"code":"invalid_parameter","message":"Sender email is not valid"}')
        self.assertIn("Sender email is not valid", logged)
        self.assertIn("400", logged)
        self.assertIn("temporary password email", logged)

    def test_a_bad_key_says_so(self):
        logged = self._failing_post(
            401, '{"code":"unauthorized","message":"Key not found"}')
        self.assertIn("Key not found", logged)
        self.assertIn("401", logged)


@override_settings(BREVO_API_KEY="test-key")
class RefusedAttachmentTest(SimpleTestCase):
    """The calendar invite is the one attachment this system sends, and the one
    part of a message Brevo may refuse on its own (an account that restricts
    attachments). The message is the point and the invite is the extra, so a
    400 for a message that carries a file is sent once more without it."""

    PAYLOAD = {"subject": "S", "textContent": "T",
               "attachment": [{"name": "invite.ics", "content": "QkVHSU4="}]}

    @staticmethod
    def _refusal(code=400):
        return urllib.error.HTTPError(
            "https://api.brevo.com/v3/smtp/email", code, "Bad Request", {},
            io.BytesIO(b'{"code":"invalid_parameter","message":"attachment"}'))

    @staticmethod
    def _accepted():
        response = mock.MagicMock()
        response.__enter__.return_value.status = 201
        return response

    def _post(self, payload, *outcomes):
        with mock.patch("children.notifications.urllib.request.urlopen",
                        side_effect=list(outcomes)) as urlopen, \
                self.assertLogs("children.notifications") as logged:
            result = _post(payload, "my_calendar email")
        sent = [json.loads(call.args[0].data) for call in urlopen.call_args_list]
        return result, sent, "\n".join(logged.output)

    def test_a_refused_attachment_is_sent_again_without_it_and_arrives(self):
        result, sent, logged = self._post(self.PAYLOAD, self._refusal(), self._accepted())
        self.assertTrue(result)
        self.assertEqual(2, len(sent))
        self.assertIn("attachment", sent[0])
        self.assertNotIn("attachment", sent[1])
        self.assertEqual({"subject": "S", "textContent": "T"}, sent[1])
        self.assertIn("without the attachment", logged)
        self.assertIn("my_calendar email", logged)

    def test_it_is_tried_once_more_and_no_more(self):
        result, sent, _ = self._post(self.PAYLOAD, self._refusal(), self._refusal())
        self.assertFalse(result)
        self.assertEqual(2, len(sent))

    def test_the_callers_payload_is_left_as_it_was(self):
        self._post(self.PAYLOAD, self._refusal(), self._accepted())
        self.assertIn("attachment", self.PAYLOAD)

    def test_a_refusal_of_a_message_with_no_file_is_not_repeated(self):
        result, sent, _ = self._post({"subject": "S"}, self._refusal())
        self.assertFalse(result)
        self.assertEqual(1, len(sent))

    def test_only_a_400_is_taken_for_the_file(self):
        """A bad key or a rate limit would fail again whatever is attached."""
        for code in (401, 403, 429, 500):
            result, sent, _ = self._post(self.PAYLOAD, self._refusal(code))
            self.assertFalse(result, code)
            self.assertEqual(1, len(sent), code)


@override_settings(BREVO_API_KEY="test-key", BREVO_SENDER_EMAIL="from@racco1.gov.ph",
                   BREVO_SENDER_NAME="NACC RACCO 1",
                   CORS_ALLOWED_ORIGINS=["http://localhost:5173",
                                         "https://nacc-v3-demo-web.onrender.com"])
class TemporaryPasswordEmailTest(TestCase):
    """The password is the whole point of this message. If it is wrong,
    truncated or absent, the person cannot get in and has no other route."""

    def setUp(self):
        role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email="someone@gmail.com", username="someone", password="x",
            role=role, first_name="Maria", last_name="Santos")

    def test_the_password_appears_exactly_once_and_intact(self):
        payload = build_temporary_password_payload(self.user, "nbqwyHsCzY8u")
        self.assertEqual(payload["htmlContent"].count("nbqwyHsCzY8u"), 1)
        self.assertIn("nbqwyHsCzY8u", payload["textContent"])

    def test_a_password_with_html_characters_is_not_mangled_or_injected(self):
        payload = build_temporary_password_payload(self.user, "a<b>&c")
        self.assertNotIn("<b>", payload["htmlContent"])
        self.assertIn("a&lt;b&gt;&amp;c", payload["htmlContent"])

    def test_it_goes_to_the_account_holder(self):
        payload = build_temporary_password_payload(self.user, "pw123456")
        self.assertEqual(payload["to"][0]["email"], "someone@gmail.com")

    def test_it_carries_a_plain_text_part(self):
        # A message with no text part is likelier to be filtered as spam.
        payload = build_temporary_password_payload(self.user, "pw123456")
        self.assertTrue(payload["textContent"].strip())

    def test_the_sign_in_link_prefers_the_deployed_origin(self):
        # The mail is read on someone else's machine; localhost is useless there.
        payload = build_temporary_password_payload(self.user, "pw123456")
        self.assertIn("https://nacc-v3-demo-web.onrender.com",
                      payload["htmlContent"])
        self.assertNotIn("localhost:5173", payload["htmlContent"])

    def test_no_link_rather_than_a_dead_one(self):
        with override_settings(CORS_ALLOWED_ORIGINS=[]):
            payload = build_temporary_password_payload(self.user, "pw123456")
        self.assertNotIn("<a href", payload["htmlContent"])

    def test_no_child_of_theirs_is_named_in_it(self):
        """Brevo is a processor outside the agency's agreements, which is why
        the assignment email carries a case number and no name. The same rule
        applies here: this message is about an account, not about anyone's
        caseload.

        Asserting the absence of the word "child" would only catch the agency's
        own name in the header. What matters is that no record reachable from
        this user reaches the message."""
        Child.objects.create(fullname="Anabelle Suguitan",
                             assigned_psychologist=self.user)
        payload = build_temporary_password_payload(self.user, "pw123456")
        body = payload["htmlContent"] + payload["textContent"]
        self.assertNotIn("Anabelle", body)
        self.assertNotIn("Suguitan", body)

    def test_the_recipient_name_cannot_inject_markup(self):
        self.user.first_name = "<script>alert(1)</script>"
        self.user.save()
        payload = build_temporary_password_payload(self.user, "pw123456")
        self.assertNotIn("<script>", payload["htmlContent"])
