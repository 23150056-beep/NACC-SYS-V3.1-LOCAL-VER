"""Changing a password should end the session it was changed from.

It did not. The gate posted the new password, set `must_change_password` to
false in local state, and carried straight on with the same access and refresh
tokens it already had. Someone handed a temporary password, told to change it,
and changing it, kept exactly the session they started with - which is the one
thing a forced change is supposed to close.

The server says so now rather than leaving each screen to decide, because
there are two screens that change a password and they were free to disagree.

**What this does and does not buy.** The client discards both tokens and signs
in again, so the person re-authenticates with the credential they just set.
SimpleJWT is stateless here - no blacklist app is installed - so an access
token already issued stays technically valid until it expires, at most an
hour. Revoking it outright means adding token_blacklist, which is a schema
change and its own decision. For a forced change on a returning colleague this
is proportionate; against a stolen token it is not, and that is worth saying
out loud rather than implying.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from accounts.models import Role

User = get_user_model()


class ReauthenticateAfterChangeTest(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@racco1.gov.ph", username="staff", password="oldpass1234",
            role=Role.objects.create(role_name=Role.STAFF))
        self.tokens = self.client.post("/api/auth/login/", {
            "email": "staff@racco1.gov.ph", "password": "oldpass1234"}).data
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.tokens["access"])

    def _change(self, current="oldpass1234", new="BrandNew!2026"):
        return self.client.post("/api/auth/change-password/", {
            "current_password": current, "new_password": new}, format="json")

    def test_changing_the_password_works(self):
        self.assertEqual(200, self._change().status_code)

    def test_the_reply_tells_the_client_to_sign_in_again(self):
        # Server-side, not a decision each screen makes for itself: two
        # different screens change a password and they were free to disagree.
        self.assertTrue(self._change().data["reauthenticate"])

    def test_the_new_password_is_what_signs_in_afterwards(self):
        self._change()
        self.client.credentials()
        self.assertEqual(401, self.client.post("/api/auth/login/", {
            "email": "staff@racco1.gov.ph", "password": "oldpass1234"}).status_code)
        self.assertEqual(200, self.client.post("/api/auth/login/", {
            "email": "staff@racco1.gov.ph", "password": "BrandNew!2026"}).status_code)

    def test_the_old_refresh_token_no_longer_yields_a_session(self):
        """The half that actually matters, and the half that was missing.

        A refresh token outlives the access token by a day. If changing a
        password left it working, "sign in again" would be theatre - the old
        session could quietly renew itself all the way through tomorrow.
        """
        self._change()
        self.client.credentials()
        response = self.client.post("/api/auth/refresh/",
                                    {"refresh": self.tokens["refresh"]}, format="json")
        self.assertEqual(401, response.status_code)

    def test_a_wrong_current_password_changes_nothing(self):
        self.assertEqual(400, self._change(current="notitatall").status_code)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.check_password("oldpass1234"))
