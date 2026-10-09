"""The same submission of Add Record arriving twice (found 9 Oct 2026).

A double click on Save Record added the child twice, each with its own request
for the psychologist to answer. The button now ignores a second press; these
tests are for the server's half (children/duplicates.py), which holds when the
button cannot: a response that never came back and was retried, a second tab,
two requests that arrive together.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role
from children import duplicates
from children.models import AssignmentRequest, Child
from children.tests.payloads import complete

User = get_user_model()
TOKEN = "3f6c1d0e-8a41-4c53-9b1e-52f0a7d9c611"
ANOTHER_TOKEN = "9b2e7a54-0c3d-4f88-a6d1-1e5b3c7f0d22"


class SubmissionTokenTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        roles = {r: Role.objects.create(role_name=r)
                 for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        cls.sw = make("sw@t.ph", "Editha", "Pascua", Role.STAFF)
        cls.other_sw = make("sw2@t.ph", "Rosa", "Santos", Role.STAFF)
        cls.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        cls.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _add(self, user=None, token=TOKEN, **over):
        body = complete(psychologist=self.psy.id, **over)
        if token is not None:
            body["intake_token"] = token
        return self._as(user or self.sw).post("/api/children/", body, format="json")

    # --- The same submission twice ---------------------------------------

    def test_the_second_attempt_is_told_it_was_already_saved(self):
        first = self._add()
        self.assertEqual(201, first.status_code, first.data)
        second = self._add()
        self.assertEqual(409, second.status_code, second.data)
        self.assertEqual("This record was already saved.", second.data["detail"])
        self.assertEqual(first.data["id"], second.data["id"])

    def test_one_child_and_one_request_come_of_it(self):
        self._add()
        self._add()
        self._add()
        self.assertEqual(1, Child.objects.count())
        self.assertEqual(1, AssignmentRequest.objects.count())

    def test_the_token_is_kept_on_the_record_and_never_sent_back(self):
        res = self._add()
        self.assertNotIn("intake_token", res.data)
        self.assertEqual(TOKEN, Child.objects.get(pk=res.data["id"]).intake_token)
        listing = self._as(self.sw).get("/api/children/")
        self.assertTrue(all("intake_token" not in row for row in listing.data))
        detail = self._as(self.sw).get(f"/api/children/{res.data['id']}/")
        self.assertNotIn("intake_token", detail.data)

    def test_the_answer_is_the_same_for_a_retry_a_minute_later(self):
        """A lost response and a double click look the same to the server."""
        first = self._add()
        again = self._add(first_name="Bea")  # even if the form had changed
        self.assertEqual(409, again.status_code)
        self.assertEqual(first.data["id"], again.data["id"])
        self.assertEqual(1, Child.objects.count())

    # --- Who is told the id -------------------------------------------------

    def test_the_id_comes_back_to_the_isa_for_anyones_record(self):
        first = self._add()
        res = self._add(user=self.admin)
        self.assertEqual(409, res.status_code)
        self.assertEqual(first.data["id"], res.data["id"])

    def test_the_id_is_withheld_from_someone_the_record_is_not_for(self):
        self._add()
        res = self._add(user=self.other_sw)
        self.assertEqual(409, res.status_code)
        self.assertEqual({"detail": "This record was already saved."}, res.data)

    # --- Two attempts that arrive together ------------------------------------

    def test_two_attempts_arriving_together_are_one_record(self):
        """Both look for the token before either has written. The database
        lets one in, and the other is answered as a retry would be."""
        first = self._add()
        real, looks = duplicates.already_saved, []

        def blind_on_the_first_look(request, token):
            looks.append(token)
            return None if len(looks) == 1 else real(request, token)

        with patch.object(duplicates, "already_saved", blind_on_the_first_look):
            res = self._add()
        self.assertEqual([TOKEN, TOKEN], looks)
        self.assertEqual(409, res.status_code, res.data)
        self.assertEqual("This record was already saved.", res.data["detail"])
        self.assertEqual(first.data["id"], res.data["id"])
        self.assertEqual(1, Child.objects.count())
        self.assertEqual(1, AssignmentRequest.objects.count())

    def test_the_other_integrity_errors_are_not_swallowed(self):
        """Only a token that really has a record is "already saved"."""
        with patch.object(duplicates, "save_new", side_effect=IntegrityError("something else")):
            with self.assertRaises(IntegrityError):
                self._add(token=ANOTHER_TOKEN)
        self.assertEqual(0, Child.objects.count())

    # --- Not the token's business -------------------------------------------

    def test_a_form_with_no_token_still_saves(self):
        res = self._add(token=None)
        self.assertEqual(201, res.status_code, res.data)
        self.assertIsNone(Child.objects.get(pk=res.data["id"]).intake_token)

    def test_blank_tokens_do_not_collide(self):
        a = self._add(token="", first_name="Ana")
        b = self._add(token="", first_name="Bea")
        self.assertEqual((201, 201), (a.status_code, b.status_code))
        self.assertEqual({None}, set(Child.objects.values_list("intake_token", flat=True)))

    def test_a_different_token_is_a_different_submission(self):
        a = self._add(first_name="Ana")
        b = self._add(first_name="Bea", token=ANOTHER_TOKEN)
        self.assertEqual((201, 201), (a.status_code, b.status_code))
        self.assertEqual(2, Child.objects.count())

    def test_a_token_that_is_not_one_is_refused_not_stored(self):
        for bad in ("short", "has spaces in it, so no", "x" * 65, "<script>alert(1)</script>"):
            res = self._add(token=bad)
            self.assertEqual(400, res.status_code, bad)
            self.assertIn("intake_token", res.data)
        self.assertEqual(0, Child.objects.count())

    def test_an_edit_ignores_the_token(self):
        made = self._add()
        other = self._add(first_name="Bea", token=ANOTHER_TOKEN)
        child_id = made.data["id"]
        # Neither a new token nor one another record holds: no 409, no change.
        for token in ("a-brand-new-token-1234", ANOTHER_TOKEN):
            res = self._as(self.sw).patch(
                f"/api/children/{child_id}/", {"intake_token": token, "referral_reason": "x"},
                format="json")
            self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(TOKEN, Child.objects.get(pk=child_id).intake_token)
        self.assertEqual(ANOTHER_TOKEN, Child.objects.get(pk=other.data["id"]).intake_token)

    def test_a_record_made_with_no_token_cannot_be_given_one_by_an_edit(self):
        made = self._add(token=None)
        res = self._as(self.sw).patch(f"/api/children/{made.data['id']}/",
                                      {"intake_token": TOKEN}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertIsNone(Child.objects.get(pk=made.data["id"]).intake_token)
