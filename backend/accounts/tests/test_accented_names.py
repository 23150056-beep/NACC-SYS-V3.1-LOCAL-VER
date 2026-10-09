"""A name with an Ñ survives every door that takes one (9 Oct 2026).

The owner's account showed in the left rail as "JOHN REYNOLD PE" + U+FFFD +
"…". The code that carries a name was read end to end - the Google ID token, the
sign-up form, the user form, the Add Record form, the serializers, the JSON the
screens read it from - and every one of those keeps the letter. What does not is
a client posting a FORM-encoded body in Latin-1 (a script, curl, PowerShell
5.1): Django decodes form data leniently and swaps the byte it cannot read for
U+FFFD, so "PEÑAMORA" is saved as "PE" + U+FFFD + "AMORA" without a word
(accounts/names.py). Those are now refused where they are written.
"""
import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Role, User
from children.models import Child
from children.tests.payloads import complete

SURNAME = "PEÑAMORA"
GIVEN = "JOHN REYNOLD"
BROKEN = "PE\ufffdAMORA"
VERIFY = "accounts.google_auth.id_token.verify_oauth2_token"
CLIENT_ID = "test-client-id.apps.googleusercontent.com"
PASSWORD = "a-long-enough-passphrase-9"


def latin1_form(**fields):
    """What a non-browser client sends: a form body whose Ñ is the byte 0xD1."""
    return "&".join(f"{k}={v}" for k, v in fields.items()).replace("Ñ", "%D1").encode("ascii")


class AccentedNameTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roles = {r: Role.objects.create(role_name=r)
                     for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        cls.admin = User.objects.create_user(
            email="admin@t.ph", username="admin", password="pass12345",
            first_name="Ada", last_name="Admin", role=cls.roles[Role.ADMINISTRATOR])
        cls.staff = User.objects.create_user(
            email="sw@t.ph", username="sw", password="pass12345",
            first_name="Editha", last_name="Pascua", role=cls.roles[Role.STAFF])

    def setUp(self):
        cache.clear()

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _assert_kept(self, response):
        """The bytes on the wire are UTF-8 and carry the letter."""
        text = response.content.decode("utf-8")
        self.assertNotIn("\ufffd", text)
        json.loads(text)                      # and it is JSON a browser will read
        self.assertIn(SURNAME, text)          # the letter itself, not an escape

    # --- The doors that keep the letter ------------------------------------

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID, GOOGLE_ALLOWED_DOMAINS=[])
    def test_a_google_name_reaches_the_screen(self):
        claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "g-1",
                  "email": "reynold@gmail.com", "email_verified": True,
                  "given_name": GIVEN, "family_name": SURNAME}
        with mock.patch(VERIFY, return_value=claims):
            res = APIClient().post("/api/auth/google/", {"credential": "tok"}, format="json")
        self.assertEqual(403, res.status_code, res.data)        # a request, not an account
        user = User.objects.get(email="reynold@gmail.com")
        self.assertEqual((GIVEN, SURNAME), (user.first_name, user.last_name))
        self.assertEqual(f"{GIVEN} {SURNAME}", user.fullname)
        listing = self._as(self.admin).get("/api/users/?status=pending")
        self._assert_kept(listing)
        # Once approved, the name the signed-in person sees about themselves.
        User.objects.filter(pk=user.pk).update(status=User.ACTIVE, is_active=True,
                                               role=self.roles[Role.STAFF])
        user.refresh_from_db()
        me = self._as(user).get("/api/auth/me/")
        self._assert_kept(me)
        self.assertEqual(f"{GIVEN} {SURNAME}", me.data["fullname"])

    def test_the_signup_form(self):
        res = APIClient().post("/api/auth/signup/", {
            "first_name": GIVEN, "last_name": SURNAME, "email": "reynold@gmail.com",
            "password": PASSWORD}, format="json")
        self.assertEqual(202, res.status_code, res.data)
        self.assertEqual(SURNAME, User.objects.get(email="reynold@gmail.com").last_name)

    def test_the_signup_form_sent_as_utf_8_bytes(self):
        body = json.dumps({"first_name": GIVEN, "last_name": SURNAME, "email": "r2@gmail.com",
                           "password": PASSWORD}, ensure_ascii=False).encode("utf-8")
        res = APIClient().post("/api/auth/signup/", data=body,
                               content_type="application/json; charset=utf-8")
        self.assertEqual(202, res.status_code, res.data)
        self.assertEqual(SURNAME, User.objects.get(email="r2@gmail.com").last_name)

    def test_the_isa_creates_and_edits_a_user(self):
        res = self._as(self.admin).post("/api/users/", {
            "email": "reynold@racco1.gov.ph", "first_name": GIVEN, "last_name": SURNAME,
            "role": self.roles[Role.STAFF].id}, format="json")
        self.assertEqual(201, res.status_code, res.data)
        user = User.objects.get(email="reynold@racco1.gov.ph")
        self.assertEqual(SURNAME, user.last_name)
        res = self._as(self.admin).patch(f"/api/users/{self.staff.pk}/",
                                         {"last_name": "NIÑO"}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual("NIÑO", User.objects.get(pk=self.staff.pk).last_name)
        self._assert_kept(self._as(self.admin).get("/api/users/"))

    def test_a_name_is_kept_as_it_was_typed_composed_or_not(self):
        decomposed = "PEÑAMORA"
        res = self._as(self.admin).patch(f"/api/users/{self.staff.pk}/",
                                         {"last_name": decomposed}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(decomposed, User.objects.get(pk=self.staff.pk).last_name)

    def test_the_add_record_form(self):
        res = self._as(self.staff).post(
            "/api/children/", complete(first_name="Niño", middle_name="Dela Peña",
                                       last_name=SURNAME), format="json")
        self.assertEqual(201, res.status_code, res.data)
        child = Child.objects.get(pk=res.data["id"])
        self.assertEqual((SURNAME, "Dela Peña", "Niño"),
                         (child.last_name, child.middle_name, child.first_name))
        self.assertEqual(f"Niño D. {SURNAME}", child.fullname)
        self._assert_kept(self._as(self.staff).get("/api/children/"))

    # --- The door that did not ------------------------------------------------

    def test_a_latin_1_form_body_is_refused_not_saved_with_a_hole_in_it(self):
        body = latin1_form(first_name="JOHN+REYNOLD", last_name="PEÑAMORA",
                           email="bad@gmail.com", password=PASSWORD)
        res = APIClient().post("/api/auth/signup/", data=body,
                               content_type="application/x-www-form-urlencoded")
        self.assertEqual(400, res.status_code, res.data)
        self.assertIn("last_name", res.data)
        self.assertFalse(User.objects.filter(email="bad@gmail.com").exists())

    def test_the_same_for_a_user_the_isa_creates(self):
        body = latin1_form(email="bad@racco1.gov.ph", first_name="JOHN", last_name="PEÑAMORA",
                           role=self.roles[Role.STAFF].id)
        res = self._as(self.admin).post("/api/users/", data=body,
                                        content_type="application/x-www-form-urlencoded")
        self.assertEqual(400, res.status_code, res.data)
        self.assertIn("last_name", res.data)
        self.assertFalse(User.objects.filter(email="bad@racco1.gov.ph").exists())

    def test_the_same_for_a_new_child_record(self):
        fields = {k: str(v).replace(" ", "+") for k, v in complete().items()}
        fields["last_name"] = "PEÑAMORA"
        res = self._as(self.staff).post("/api/children/", data=latin1_form(**fields),
                                        content_type="application/x-www-form-urlencoded")
        self.assertEqual(400, res.status_code, res.data)
        self.assertIn("last_name", res.data)
        self.assertEqual(0, Child.objects.count())

    def test_a_json_body_that_is_not_utf_8_was_always_refused(self):
        body = json.dumps({"first_name": GIVEN, "last_name": SURNAME, "email": "x@gmail.com",
                           "password": PASSWORD}, ensure_ascii=False).encode("latin-1")
        res = APIClient().post("/api/auth/signup/", data=body, content_type="application/json")
        self.assertEqual(400, res.status_code)

    # --- A name already damaged does not lock the record -------------------------

    def test_an_account_already_holding_the_damaged_name_can_still_be_edited(self):
        User.objects.filter(pk=self.staff.pk).update(last_name=BROKEN)
        res = self._as(self.admin).patch(
            f"/api/users/{self.staff.pk}/", {"last_name": BROKEN, "contact_details": "x"},
            format="json")
        self.assertEqual(200, res.status_code, res.data)
        # Typing it again, with the letter, is the fix.
        res = self._as(self.admin).patch(f"/api/users/{self.staff.pk}/",
                                         {"last_name": SURNAME}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(SURNAME, User.objects.get(pk=self.staff.pk).last_name)

    def test_but_a_different_damaged_name_is_refused(self):
        User.objects.filter(pk=self.staff.pk).update(last_name=BROKEN)
        res = self._as(self.admin).patch(f"/api/users/{self.staff.pk}/",
                                         {"last_name": "CA\ufffdEDO"}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertIn("last_name", res.data)

    def test_a_record_already_holding_the_damaged_name_is_still_editable(self):
        child = Child.objects.create(first_name="JOHN", last_name=BROKEN, birth_date="2016-01-10",
                                     social_worker=self.staff, referral_reason="a")
        res = self._as(self.staff).patch(f"/api/children/{child.pk}/",
                                         {"referral_reason": "b"}, format="json")
        self.assertEqual(200, res.status_code, res.data)
