"""The agency's own details: who it is and who heads the office.

Printed reports read these, so every signed-in account may read them; only the
ISA writes them, the same split as the rest of Settings. And there is exactly
one row, however it is saved.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import AgencyProfile, Role

URL = "/api/agency-profile/"
User = get_user_model()


class AgencyProfileTest(APITestCase):
    def setUp(self):
        roles = {name: Role.objects.create(role_name=name) for name in (
            Role.ADMINISTRATOR, Role.STAFF, Role.PSYCHOLOGIST)}
        self.users = {
            name: User.objects.create_user(
                email=f"{name.lower()}@racco1.gov.ph", username=name.lower(),
                password="pass1234", role=role)
            for name, role in roles.items()}

    def _as(self, role_name):
        self.client.force_authenticate(self.users[role_name])

    def test_it_needs_a_signed_in_account(self):
        self.assertEqual(self.client.get(URL).status_code, 401)
        self.assertEqual(self.client.put(URL, {"agency_name": "X"}, format="json").status_code, 401)

    def test_an_empty_profile_reads_as_blanks_not_404(self):
        self._as(Role.STAFF)
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200, resp.data)
        for key in ("agency_name", "office_address", "contact_details",
                    "head_of_office_name", "head_of_office_title"):
            self.assertEqual(resp.data[key], "")

    def test_staff_and_psychologists_read_but_cannot_write(self):
        self._as(Role.ADMINISTRATOR)
        self.client.put(URL, {"head_of_office_name": "A. Reyes"}, format="json")
        for role in (Role.STAFF, Role.PSYCHOLOGIST):
            self._as(role)
            self.assertEqual(self.client.get(URL).data["head_of_office_name"], "A. Reyes")
            for verb in (self.client.put, self.client.patch):
                resp = verb(URL, {"head_of_office_name": "Someone Else"}, format="json")
                self.assertEqual(resp.status_code, 403, (role, resp.data))
        self.assertEqual(AgencyProfile.load().head_of_office_name, "A. Reyes")

    def test_the_administrator_writes_and_the_text_is_trimmed(self):
        self._as(Role.ADMINISTRATOR)
        resp = self.client.put(URL, {
            "agency_name": "  RACCO 1  ",
            "office_address": " Government Center, San Fernando City ",
            "contact_details": "0917 000 0000",
            "head_of_office_name": "  Maria Dela Cruz ",
            "head_of_office_title": "Regional Director",
        }, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        row = AgencyProfile.objects.get()
        self.assertEqual(row.agency_name, "RACCO 1")
        self.assertEqual(row.office_address, "Government Center, San Fernando City")
        self.assertEqual(row.head_of_office_name, "Maria Dela Cruz")
        self.assertEqual(row.head_of_office_title, "Regional Director")

    def test_a_field_can_be_cleared_again(self):
        self._as(Role.ADMINISTRATOR)
        self.client.put(URL, {"head_of_office_name": "A. Reyes"}, format="json")
        resp = self.client.patch(URL, {"head_of_office_name": ""}, format="json")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(AgencyProfile.load().head_of_office_name, "")

    def test_an_overlong_value_is_refused_with_the_field_named(self):
        self._as(Role.ADMINISTRATOR)
        resp = self.client.put(URL, {"head_of_office_name": "x" * 151}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("head_of_office_name", resp.data)
        resp = self.client.put(URL, {"office_address": "x" * 501}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("office_address", resp.data)
        self.assertEqual(AgencyProfile.load().head_of_office_name, "")

    def test_it_stays_one_row_however_often_it_is_saved(self):
        self._as(Role.ADMINISTRATOR)
        for name in ("One", "Two", "Three"):
            self.client.put(URL, {"agency_name": name}, format="json")
        self.client.get(URL)
        # A second instance saved directly still lands on the same row.
        AgencyProfile(agency_name="Direct").save()
        self.assertEqual(AgencyProfile.objects.count(), 1)
        self.assertEqual(AgencyProfile.objects.get().pk, 1)
        self.assertEqual(AgencyProfile.objects.get().agency_name, "Direct")
