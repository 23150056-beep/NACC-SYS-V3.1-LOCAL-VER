"""Reactivating a Google colleague used to lock them out for good.

Four correct decisions, and together they close a door nobody can open:

1. the Google door creates accounts with `set_unusable_password()` - approval
   grants a role, it does not hand out a credential;
2. `reactivate` sets `must_change_password`, on the reasoning that somebody
   coming back should not keep the credential they left with;
3. every protected route is gated on that flag; and
4. clearing the gate means POSTing the CURRENT password, checked with
   `check_password()` - which is False for every unusable password, always.

So a staff member who signs in with Google, is deactivated, and is reactivated
signs in perfectly well and then cannot get past the gate, because it demands
a password they were deliberately never given. The only way back was a new
account, which is exactly what was reported.

The fix is at step 2: an account with no password has no password to change,
so flagging it forces nothing and only ever deadlocks. The rest of the chain
is left alone - each part of it is right.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role

User = get_user_model()


class ReactivationTest(APITestCase):
    def setUp(self):
        self.admin_role = Role.objects.create(role_name=Role.ADMINISTRATOR)
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.psy_role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)

        # Signed up with a password, the ordinary way.
        self.staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="staff1234",
            role=self.staff_role)

        # Came in through the Google door: a role, and no credential at all.
        self.google_psy = User(
            email="g@racco1.gov.ph", username="g@racco1.gov.ph",
            role=self.psy_role, status=User.ACTIVE, google_sub="google-123")
        self.google_psy.set_unusable_password()
        self.google_psy.save()

        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _cycle(self, user):
        self.assertEqual(200, self.client.post(f"/api/users/{user.id}/archive/").status_code)
        response = self.client.post(f"/api/users/{user.id}/reactivate/")
        user.refresh_from_db()
        return response

    def test_the_google_account_has_no_password_to_begin_with(self):
        # The premise. If this ever stops being true the rest is moot.
        self.assertFalse(self.google_psy.has_usable_password())

    def test_a_password_account_is_still_asked_to_change_it(self):
        # Unchanged: somebody returning should not keep the credential they
        # left with, and they have one to replace.
        self._cycle(self.staff)
        self.assertEqual(User.ACTIVE, self.staff.status)
        self.assertTrue(self.staff.must_change_password)

    def test_a_google_account_is_NOT_flagged_for_a_password_change(self):
        # The fix. Flagging it forces nothing, because there is nothing to
        # change - it only closes the door.
        self._cycle(self.google_psy)
        self.assertEqual(User.ACTIVE, self.google_psy.status)
        self.assertFalse(self.google_psy.must_change_password)

    def test_the_reply_says_whether_a_password_change_is_coming(self):
        # The directory told everyone "they must set a new password at next
        # sign-in". For a Google colleague that is simply not true, and a
        # confident false instruction is worse than none.
        self.assertTrue(self._cycle(self.staff).data["must_change_password"])
        self.assertFalse(self._cycle(self.google_psy).data["must_change_password"])

    def test_the_reactivated_google_account_is_active_again(self):
        response = self._cycle(self.google_psy)
        self.assertEqual(200, response.status_code)
        self.assertTrue(self.google_psy.is_active)

    def test_the_gate_it_would_have_hit_is_genuinely_impassable(self):
        """Why the flag had to go rather than be worked around downstream.

        With no usable password there is no value that clears this gate. Not a
        blank one, not the right one - there isn't a right one.
        """
        self.google_psy.must_change_password = True
        self.google_psy.save(update_fields=["must_change_password"])
        for attempt in ("", "anything", "google-123"):
            self.assertFalse(self.google_psy.check_password(attempt))

    def test_reactivating_still_refuses_an_administrator(self):
        # The 2026-07-18 product decision, unchanged by any of this.
        other_admin = User.objects.create_user(
            email="a2@racco1.gov.ph", username="a2", password="pass1234",
            role=self.admin_role)
        self.client.post(f"/api/users/{other_admin.id}/archive/")
        response = self.client.post(f"/api/users/{other_admin.id}/reactivate/")
        self.assertEqual(400, response.status_code)

    def test_reactivating_still_refuses_an_account_never_approved(self):
        declined = User.objects.create_user(
            email="d@racco1.gov.ph", username="d", password="pass1234")
        declined.role = None
        declined.status = User.ARCHIVED
        declined.save()
        response = self.client.post(f"/api/users/{declined.id}/reactivate/")
        self.assertEqual(400, response.status_code)
