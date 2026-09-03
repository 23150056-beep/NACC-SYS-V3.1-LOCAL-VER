"""Text messages: who can be sent one, and what may be in it.

Two things are worth testing here and they are not the plumbing.

The first is that nothing confidential leaves. A text is unencrypted, carried
by a telco, and sits on a lock screen anyone beside the person can read. The
email already refuses to name a child for the same reason; this is the weaker
channel, so the rule is applied harder. There is a test below that reads every
message this system can send and fails if a child's name or a password is in
one — driven by the senders themselves, so a fourth message added later is
covered without anybody remembering to come back here.

The second is that an unverified number never receives anything. A number
somebody typed is a number that might be a typo, and a typo is a stranger's
handset.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.phone import (
    InvalidPhilippineMobile, as_typed, normalise_ph_mobile,
)
from accounts import sms_notifications
from accounts.sms import SINGLE_SEGMENT, send_sms
from children.models import Child

User = get_user_model()
URL = "/api/auth/me/phone/"


class NumberNormalisationTest(TestCase):
    """One implementation, so the serializer and the sender cannot disagree
    about the same person's number."""

    ACCEPTED = [
        ("0917 123 4567", "+639171234567"),
        ("09171234567", "+639171234567"),
        ("+63 917 123 4567", "+639171234567"),
        ("+639171234567", "+639171234567"),
        ("639171234567", "+639171234567"),
        ("9171234567", "+639171234567"),
        ("(0917) 123-4567", "+639171234567"),
        ("  09991234567  ", "+639991234567"),
    ]

    REFUSED = [
        "02 8123 4567",      # landline: a real contact detail, not an SMS one
        "+63 2 8123 4567",
        "0917123456",        # a digit short
        "091712345678",      # a digit long
        "0917 12A 4567",
        "+1 415 555 0100",   # not Philippine
        "12345",
    ]

    def test_every_shape_lands_on_one_stored_value(self):
        for typed, expected in self.ACCEPTED:
            with self.subTest(typed=typed):
                self.assertEqual(normalise_ph_mobile(typed), expected)

    def test_what_cannot_receive_a_text_is_refused(self):
        for typed in self.REFUSED:
            with self.subTest(typed=typed):
                with self.assertRaises(InvalidPhilippineMobile):
                    normalise_ph_mobile(typed)

    def test_a_landline_says_it_is_a_landline(self):
        """The two ways this goes wrong are different mistakes and deserve
        different messages."""
        with self.assertRaises(InvalidPhilippineMobile) as caught:
            normalise_ph_mobile("02 8123 4567")
        self.assertIn("landline", str(caught.exception).lower())

    def test_empty_is_not_an_error(self):
        self.assertEqual(normalise_ph_mobile(""), "")
        self.assertEqual(normalise_ph_mobile(None), "")

    def test_it_is_shown_the_way_a_reader_expects(self):
        self.assertEqual(as_typed("+639171234567"), "0917 123 4567")


class SmsBase(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="pass1234",
            role=self.admin_role)
        self.psy = User.objects.create_user(
            email="p@racco1.gov.ph", username="p", password="pass1234",
            role=self.psy_role)
        cache.clear()

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)


class NothingConfidentialLeavesTest(SmsBase):
    """The test that matters most, and it drives itself off the senders."""

    def _every_message(self):
        """Capture the text of every message the system can send."""
        self.psy.phone = "+639171234567"
        self.psy.phone_verified = True
        self.psy.save()
        child = Child.objects.create(
            fullname="Angelica Torres", case_type="Adoption",
            assigned_psychologist=self.psy)

        sent = []
        with patch.object(sms_notifications, "queue_sms",
                          side_effect=lambda n, t, d: sent.append(t) or True):
            sms_notifications.notify_temporary_password(self.psy)
            sms_notifications.notify_new_assignment(child)
            sms_notifications.notify_session_reminder(self.psy, 3)
        return sent, child

    def test_no_message_carries_a_child_name(self):
        sent, child = self._every_message()
        self.assertTrue(sent, "no messages were captured — the test is not testing")
        for text in sent:
            for part in child.fullname.split():
                self.assertNotIn(
                    part.lower(), text.lower(),
                    f"a child's name reached a text message: {text!r}")

    def test_no_message_carries_a_password(self):
        """The password goes by email, where it is behind a login. The text
        says one is waiting."""
        sent, _ = self._every_message()
        joined = " ".join(sent).lower()
        for word in ("password is", "your password:", "temporary password is"):
            self.assertNotIn(word, joined)

    def test_every_message_fits_one_segment(self):
        """Two texts arriving out of order say something nobody wrote."""
        sent, _ = self._every_message()
        for text in sent:
            self.assertLessEqual(len(text), SINGLE_SEGMENT, repr(text))


class OnlyVerifiedNumbersReceiveTest(SmsBase):
    def setUp(self):
        super().setUp()
        self.child = Child.objects.create(
            fullname="Ana", case_type="Adoption", assigned_psychologist=self.psy)

    def test_no_number_means_no_message(self):
        self.assertFalse(sms_notifications.notify_temporary_password(self.psy))
        self.assertFalse(sms_notifications.notify_new_assignment(self.child))
        self.assertFalse(sms_notifications.notify_session_reminder(self.psy, 2))

    def test_an_unverified_number_means_no_message(self):
        """An administrator typing a number into a record must not be enough
        to start texting it."""
        self.psy.phone = "+639171234567"
        self.psy.phone_verified = False
        self.psy.save()
        self.assertFalse(sms_notifications.notify_temporary_password(self.psy))
        self.assertFalse(sms_notifications.notify_new_assignment(self.child))

    def test_a_verified_number_receives(self):
        self.psy.phone = "+639171234567"
        self.psy.phone_verified = True
        self.psy.save()
        with patch.object(sms_notifications, "queue_sms", return_value=True):
            self.assertTrue(sms_notifications.notify_temporary_password(self.psy))
            self.assertTrue(sms_notifications.notify_new_assignment(self.child))


class PhoneEndpointIsBoundToTheCallerTest(SmsBase):
    def test_it_needs_a_signed_in_account(self):
        self.assertIn(self.client.get(URL).status_code, (401, 403))

    def test_a_landline_is_refused_before_anything_is_sent(self):
        self._auth("p@racco1.gov.ph")
        resp = self.client.post(URL, {"phone": "02 8123 4567"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.psy.refresh_from_db()
        self.assertEqual(self.psy.phone, "")

    def test_the_code_must_match_before_the_number_is_stored(self):
        self._auth("p@racco1.gov.ph")
        self.client.post(URL, {"phone": "0917 123 4567"}, format="json")
        self.psy.refresh_from_db()
        self.assertEqual(self.psy.phone, "",
                         "the number was stored before it was proved")

        resp = self.client.put(URL, {"code": "000000"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.psy.refresh_from_db()
        self.assertFalse(self.psy.phone_verified)

    def test_the_right_code_verifies(self):
        self._auth("p@racco1.gov.ph")
        self.client.post(URL, {"phone": "0917 123 4567"}, format="json")
        entry = cache.get(f"phone-verify:{self.psy.pk}")
        resp = self.client.put(URL, {"code": entry["code"]}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.psy.refresh_from_db()
        self.assertEqual(self.psy.phone, "+639171234567")
        self.assertTrue(self.psy.phone_verified)

    def test_guessing_is_limited(self):
        self._auth("p@racco1.gov.ph")
        self.client.post(URL, {"phone": "0917 123 4567"}, format="json")
        for _ in range(sms_notifications.CODE_MAX_ATTEMPTS + 1):
            self.client.put(URL, {"code": "000000"}, format="json")
        entry = cache.get(f"phone-verify:{self.psy.pk}")
        self.assertIsNone(entry, "the code survived more guesses than allowed")

    def test_removing_the_number_stops_the_messages(self):
        self.psy.phone = "+639171234567"
        self.psy.phone_verified = True
        self.psy.save()
        self._auth("p@racco1.gov.ph")
        resp = self.client.delete(URL)
        self.assertEqual(resp.status_code, 200)
        self.psy.refresh_from_db()
        self.assertEqual(self.psy.phone, "")
        self.assertFalse(self.psy.phone_verified)


class AnAdministratorCannotVerifySomebodyElsesNumberTest(SmsBase):
    """The phone field is readable in the directory and writable nowhere but
    the owner's own endpoint. Otherwise an administrator could point a
    colleague's notifications at a handset of their choosing."""

    def test_the_directory_cannot_write_the_number(self):
        self._auth("admin@racco1.gov.ph")
        resp = self.client.patch(
            f"/api/users/{self.psy.id}/",
            {"phone": "+639998887777", "phone_verified": True}, format="json")
        self.assertIn(resp.status_code, (200, 400))
        self.psy.refresh_from_db()
        self.assertEqual(self.psy.phone, "")
        self.assertFalse(self.psy.phone_verified)


class TheTestButtonTest(SmsBase):
    def test_only_an_administrator_can_use_it(self):
        self._auth("p@racco1.gov.ph")
        self.assertEqual(self.client.post("/api/sms-test/").status_code, 403)

    def test_it_refuses_when_there_is_nowhere_to_send(self):
        self._auth("admin@racco1.gov.ph")
        resp = self.client.post("/api/sms-test/")
        self.assertEqual(resp.status_code, 400)

    def test_it_reports_what_the_gateway_said(self):
        self.admin.phone = "+639998887777"
        self.admin.phone_verified = True
        self.admin.save()
        self._auth("admin@racco1.gov.ph")
        resp = self.client.post("/api/sms-test/")
        self.assertEqual(resp.status_code, 200, resp.data)
        for key in ("ok", "detail", "provider", "recipient"):
            self.assertIn(key, resp.data)

    def test_it_sends_only_to_the_caller(self):
        """A diagnostic that can text an arbitrary number is one somebody
        eventually points at a stranger."""
        self.admin.phone = "+639998887777"
        self.admin.phone_verified = True
        self.admin.save()
        self._auth("admin@racco1.gov.ph")
        resp = self.client.post("/api/sms-test/", {"phone": "+639171111111"},
                                format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("0999 888 7777", resp.data["recipient"])


@override_settings(SMS_PROVIDER="console")
class TheSenderRefusesBadInputTest(TestCase):
    def test_it_never_raises(self):
        for number, text in [("", "hi"), ("02 8123 4567", "hi"),
                             ("09171234567", ""), (None, "hi")]:
            with self.subTest(number=number):
                self.assertFalse(send_sms(number, text, "probe").ok)

    def test_an_over_long_message_is_trimmed_not_split(self):
        result = send_sms("09171234567", "x" * 400, "probe")
        self.assertTrue(result.ok)
