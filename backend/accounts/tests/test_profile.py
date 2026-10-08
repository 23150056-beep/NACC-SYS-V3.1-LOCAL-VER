"""Your own optional details, and nobody else's.

Three social links is a small feature, but it is the first place in this
system where a person writes to their own account record rather than an
administrator writing to it for them. So the tests that matter are the ones
about reach: that the endpoint is bound to the caller, that there is no way to
name another account, and that what gets stored here does not surface on the
administrator's user directory — which is the whole reason this is a separate
table rather than three more columns on User.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role, UserProfile

URL = "/api/auth/me/profile/"
User = get_user_model()


class ProfileBase(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="pass1234",
            role=self.admin_role)
        self.me = User.objects.create_user(
            email="me@racco1.gov.ph", username="me", password="pass1234",
            role=self.psy_role)
        self.them = User.objects.create_user(
            email="them@racco1.gov.ph", username="them", password="pass1234",
            role=self.psy_role)

    def _auth(self, email):
        token = self.client.post("/api/auth/login/", {
            "email": email, "password": "pass1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)


class ProfileIsBoundToTheCallerTest(ProfileBase):
    def test_it_needs_a_signed_in_account(self):
        self.assertIn(self.client.get(URL).status_code, (401, 403))

    def test_reading_returns_an_empty_profile_rather_than_404(self):
        """An account that has never opened the page has no row. That is
        normal, not missing — the screen should render the empty form."""
        self._auth("me@racco1.gov.ph")
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["facebook"], "")

    def test_saving_creates_the_row_for_the_caller(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(URL, {"facebook": "maria.santos"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(UserProfile.objects.count(), 1)
        self.assertEqual(UserProfile.objects.get().user, self.me)

    def test_two_people_get_two_profiles(self):
        self._auth("me@racco1.gov.ph")
        self.client.patch(URL, {"facebook": "mine"}, format="json")
        self._auth("them@racco1.gov.ph")
        self.client.patch(URL, {"facebook": "theirs"}, format="json")
        self.assertEqual(
            UserProfile.objects.get(user=self.me).facebook, "facebook.com/mine")
        self.assertEqual(
            UserProfile.objects.get(user=self.them).facebook, "facebook.com/theirs")

    def test_naming_another_user_in_the_payload_is_ignored(self):
        """The only defence that matters. The view takes the account from the
        token; a `user` in the body must not redirect the write."""
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(
            URL, {"facebook": "mine", "user": self.them.id}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertFalse(UserProfile.objects.filter(user=self.them).exists())
        self.assertEqual(
            UserProfile.objects.get(user=self.me).facebook, "facebook.com/mine")

    def test_clearing_empties_the_fields(self):
        self._auth("me@racco1.gov.ph")
        self.client.patch(URL, {"facebook": "mine", "twitter": "@mine"}, format="json")
        resp = self.client.patch(
            URL, {"facebook": "", "twitter": "", "instagram": ""}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        p = UserProfile.objects.get(user=self.me)
        self.assertEqual((p.facebook, p.twitter, p.instagram), ("", "", ""))


class ProfileDoesNotLeakIntoTheDirectoryTest(ProfileBase):
    """The reason this is a separate table. UserSerializer backs /api/users/,
    which every administrator opens; a field added to User would appear there
    for every account. Nothing here should."""

    def test_an_administrator_reading_the_directory_sees_no_profile_fields(self):
        self._auth("me@racco1.gov.ph")
        self.client.patch(URL, {"facebook": "maria.santos"}, format="json")

        self._auth("admin@racco1.gov.ph")
        rows = self.client.get("/api/users/").data
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        blob = str(rows)
        for leaked in ("facebook", "twitter", "instagram", "maria.santos"):
            self.assertNotIn(leaked, blob,
                             f"{leaked!r} reached the administrator's user directory")

    def test_it_is_not_on_the_me_endpoint_either(self):
        self._auth("me@racco1.gov.ph")
        self.client.patch(URL, {"facebook": "maria.santos"}, format="json")
        me = self.client.get("/api/auth/me/").data
        self.assertNotIn("facebook", me)


class ProfileNormalisesWhatPeopleTypeTest(ProfileBase):
    """Four shapes mean one thing. Store one of them."""

    CASES = [
        ("facebook", "https://facebook.com/maria.santos", "facebook.com/maria.santos"),
        ("facebook", "www.facebook.com/maria.santos/", "facebook.com/maria.santos"),
        ("facebook", "facebook.com/maria.santos?ref=bookmarks", "facebook.com/maria.santos"),
        # A bare handle, and one with a dot in it — plenty of Facebook
        # usernames have one, and an early version read it as a hostname.
        ("facebook", "maria.santos", "facebook.com/maria.santos"),
        ("facebook", "@maria.santos", "facebook.com/maria.santos"),
        ("twitter", "https://twitter.com/mhandle", "x.com/mhandle"),
        ("twitter", "@mhandle", "x.com/mhandle"),
        ("instagram", "maria_santos", "instagram.com/maria_santos"),
    ]

    def test_every_shape_lands_on_the_same_stored_value(self):
        self._auth("me@racco1.gov.ph")
        for field, typed, expected in self.CASES:
            with self.subTest(typed=typed):
                resp = self.client.patch(URL, {field: typed}, format="json")
                self.assertEqual(resp.status_code, 200, resp.data)
                self.assertEqual(resp.data[field], expected)

    def test_another_site_is_refused(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(
            URL, {"facebook": "https://tiktok.com/@me"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_a_link_to_a_post_is_refused(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(
            URL, {"instagram": "instagram.com/p/abc123"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_the_bare_site_with_no_username_is_refused(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(URL, {"facebook": "facebook.com"}, format="json")
        self.assertEqual(resp.status_code, 400, resp.data)

    def test_the_refusal_reads_correctly_for_every_site(self):
        """The message names the site, so it must not end up saying
        "a instagram.com". Read far more often than it is written."""
        self._auth("me@racco1.gov.ph")
        for field in ("facebook", "twitter", "instagram"):
            with self.subTest(field=field):
                resp = self.client.patch(
                    URL, {field: "https://tiktok.com/@me"}, format="json")
                self.assertEqual(resp.status_code, 400)
                message = str(resp.data[field][0])
                self.assertNotRegex(message, r"a [aeiou]")

    def test_blank_is_accepted_and_means_blank(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(URL, {"facebook": "   "}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["facebook"], "")


class ProfileLicenseTest(ProfileBase):
    """The PRC license the Social Case Study Report's signature block prints.

    Staff and Psychologist hold one; the ISA is IT support and does not. It is
    the person's own statement about themselves, so it travels the same
    caller-bound endpoint as the links and appears nowhere else.
    """

    def setUp(self):
        super().setUp()
        staff_role = Role.objects.create(role_name=Role.STAFF)
        self.sw = User.objects.create_user(
            email="sw@racco1.gov.ph", username="sw", password="pass1234",
            role=staff_role)

    def test_a_psychologist_round_trips_their_license(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(URL, {
            "license_number": "  0012345  ", "license_valid_until": "2027-03-14"},
            format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["license_number"], "0012345")
        self.assertEqual(resp.data["license_valid_until"], "2027-03-14")
        again = self.client.get(URL).data
        self.assertEqual(again["license_number"], "0012345")
        self.assertEqual(again["license_valid_until"], "2027-03-14")
        p = UserProfile.objects.get(user=self.me)
        self.assertEqual(p.license_number, "0012345")
        self.assertEqual(str(p.license_valid_until), "2027-03-14")

    def test_a_social_worker_round_trips_theirs(self):
        self._auth("sw@racco1.gov.ph")
        resp = self.client.put(URL, {
            "license_number": "SW-778", "license_valid_until": "2028-01-31"},
            format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(self.client.get(URL).data["license_number"], "SW-778")

    def test_an_unset_license_reads_blank_and_null(self):
        self._auth("sw@racco1.gov.ph")
        data = self.client.get(URL).data
        self.assertEqual(data["license_number"], "")
        self.assertIsNone(data["license_valid_until"])

    def test_a_lapsed_license_is_a_fact_not_an_error(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(
            URL, {"license_valid_until": "2019-06-30"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["license_valid_until"], "2019-06-30")

    def test_the_validity_can_be_cleared(self):
        self._auth("me@racco1.gov.ph")
        self.client.patch(URL, {"license_valid_until": "2027-03-14"}, format="json")
        resp = self.client.patch(
            URL, {"license_valid_until": None, "license_number": ""}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIsNone(resp.data["license_valid_until"])

    def test_the_number_is_capped_at_fifty_characters(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(URL, {"license_number": "9" * 51}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("license_number", resp.data)
        ok = self.client.patch(URL, {"license_number": "9" * 50}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)

    def test_a_bad_date_is_refused(self):
        self._auth("me@racco1.gov.ph")
        resp = self.client.patch(
            URL, {"license_valid_until": "next year"}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("license_valid_until", resp.data)

    def test_an_administrator_is_not_offered_the_fields(self):
        self._auth("admin@racco1.gov.ph")
        data = self.client.get(URL).data
        self.assertNotIn("license_number", data)
        self.assertNotIn("license_valid_until", data)

    def test_an_administrator_who_writes_one_is_told_why(self):
        self._auth("admin@racco1.gov.ph")
        for body in ({"license_number": "0012345"},
                     {"license_valid_until": "2027-03-14"},
                     {"license_number": ""}):
            with self.subTest(body=body):
                resp = self.client.patch(URL, body, format="json")
                self.assertEqual(resp.status_code, 400, resp.data)
                field = next(iter(body))
                self.assertIn("no PRC license", str(resp.data[field][0]))
        self.assertFalse(UserProfile.objects.filter(
            user=self.admin).exclude(license_number="").exists())

    def test_an_administrator_can_still_save_their_links(self):
        self._auth("admin@racco1.gov.ph")
        resp = self.client.patch(URL, {"facebook": "isa.support"}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["facebook"], "facebook.com/isa.support")

    def test_nobody_reads_another_persons_license(self):
        """The endpoint is bound to the caller and takes no id, so the only
        way to see someone's license is to be them."""
        self._auth("me@racco1.gov.ph")
        self.client.patch(URL, {
            "license_number": "MINE-1", "license_valid_until": "2027-03-14"},
            format="json")

        for email in ("them@racco1.gov.ph", "sw@racco1.gov.ph", "admin@racco1.gov.ph"):
            with self.subTest(reader=email):
                self._auth(email)
                self.assertNotIn("MINE-1", str(self.client.get(URL).data))
        # And naming the account in the body or the query changes nothing.
        self._auth("them@racco1.gov.ph")
        resp = self.client.get(URL, {"user": self.me.id})
        self.assertEqual(resp.data["license_number"], "")
        resp = self.client.patch(
            URL, {"license_number": "THEIRS-9", "user": self.me.id}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(UserProfile.objects.get(user=self.me).license_number, "MINE-1")
        self.assertEqual(UserProfile.objects.get(user=self.them).license_number, "THEIRS-9")

    def test_it_is_in_neither_the_directory_nor_the_me_endpoint(self):
        self._auth("me@racco1.gov.ph")
        self.client.patch(URL, {
            "license_number": "MINE-1", "license_valid_until": "2027-03-14"},
            format="json")
        self.assertNotIn("license", str(self.client.get("/api/auth/me/").data))
        self._auth("admin@racco1.gov.ph")
        self.assertNotIn("MINE-1", str(self.client.get("/api/users/").data))
        self.assertNotIn("license", str(self.client.get("/api/users/").data))
