"""Google Sign-In: who gets in, and — more importantly — who does not."""

from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from accounts.models import Role, User
from children.tests.test_child_collab import make_user

GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
VERIFY = "accounts.google_auth.id_token.verify_oauth2_token"


def claims(email, sub="google-sub-123", verified=True, **extra):
    payload = {
        "iss": "https://accounts.google.com",
        "aud": GOOGLE_CLIENT_ID,
        "sub": sub,
        "email": email,
        "email_verified": verified,
    }
    payload.update(extra)
    return payload


@override_settings(GOOGLE_OAUTH_CLIENT_ID=GOOGLE_CLIENT_ID, GOOGLE_ALLOWED_DOMAINS=[])
class GoogleLoginTests(APITestCase):
    def setUp(self):
        # The sign-up counter lives in the process-wide cache, which the test
        # database rollback does not touch — without this, tests leak their
        # allowance into each other.
        cache.clear()
        self.staff = make_user("staff@racco1.gov.ph", Role.STAFF)
        self.psych = make_user("psych@racco1.gov.ph", Role.PSYCHOLOGIST)
        self.admin = make_user("admin@racco1.gov.ph", Role.ADMINISTRATOR)

    def post(self, credential="tok"):
        return self.client.post("/api/auth/google/", {"credential": credential}, format="json")

    # --- accepted -------------------------------------------------------

    def test_staff_can_sign_in(self):
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph")):
            r = self.post()
        self.assertEqual(r.status_code, 200, r.data)
        # Same envelope as a password login, so the frontend stores it identically.
        self.assertEqual(set(r.data), {"refresh", "access", "user"})
        self.assertEqual(r.data["user"]["email"], "staff@racco1.gov.ph")

    def test_psychologist_can_sign_in(self):
        with mock.patch(VERIFY, return_value=claims("psych@racco1.gov.ph")):
            self.assertEqual(self.post().status_code, 200)

    def test_issued_token_actually_works(self):
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph")):
            token = self.post().data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        me = self.client.get("/api/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["email"], "staff@racco1.gov.ph")

    def test_first_sign_in_links_the_google_subject(self):
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph", sub="sub-abc")):
            self.post()
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.google_sub, "sub-abc")

    def test_later_sign_in_matches_on_subject_not_email(self):
        # Google-side email change must not lock the user out of their account.
        self.staff.google_sub = "sub-abc"
        self.staff.save(update_fields=["google_sub"])
        with mock.patch(VERIFY, return_value=claims("new-address@gmail.com", sub="sub-abc")):
            r = self.post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["user"]["email"], "staff@racco1.gov.ph")

    def test_forced_password_change_is_cleared(self):
        # A Google user has no password to rotate; leaving the flag set would
        # trap them in the change-password gate forever.
        self.staff.must_change_password = True
        self.staff.set_unusable_password()
        self.staff.save(update_fields=["must_change_password", "password"])
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph")):
            token = self.post().data["access"]
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.must_change_password)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(self.client.get("/api/children/").status_code, 200)

    # --- refused --------------------------------------------------------

    def test_administrator_is_refused(self):
        # Admins keep password login: tying the recovery account to a third
        # party means a Google outage locks the agency out of its own records.
        with mock.patch(VERIFY, return_value=claims("admin@racco1.gov.ph")):
            r = self.post()
        self.assertEqual(r.status_code, 401)
        self.assertIn("password", str(r.data).lower())

    def test_unknown_email_creates_a_request_and_grants_nothing(self):
        """Policy changed 2026-08-13: an unknown address now registers rather
        than bouncing. What must NOT change is that registering grants nothing
        — no role, no token, no access."""
        with mock.patch(VERIFY, return_value=claims("stranger@gmail.com")):
            r = self.post()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data["state"], "pending_approval")
        self.assertNotIn("access", r.data)
        created = User.objects.get(email="stranger@gmail.com")
        self.assertEqual(created.status, User.PENDING)
        self.assertIsNone(created.role)
        self.assertFalse(created.is_active)
        self.assertFalse(created.has_usable_password())

    def test_archived_user_is_refused(self):
        self.staff.status = User.ARCHIVED
        self.staff.save(update_fields=["status"])
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph")):
            self.assertEqual(self.post().status_code, 401)

    def test_archived_user_cannot_re_register_themselves(self):
        """Otherwise deactivation is undone by the deactivated person: sign in
        again, get a fresh pending request, wait for a distracted approval."""
        self.staff.status = User.ARCHIVED
        self.staff.save(update_fields=["status"])
        before = User.objects.count()
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph", sub="brand-new-sub")):
            r = self.post()
        self.assertEqual(r.status_code, 401)
        self.assertEqual(User.objects.count(), before)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.status, User.ARCHIVED)

    def test_unverified_google_email_is_refused(self):
        # Otherwise anyone could claim a staff address on a throwaway account.
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph", verified=False)):
            self.assertEqual(self.post().status_code, 401)

    def test_invalid_token_is_refused(self):
        with mock.patch(VERIFY, side_effect=ValueError("bad signature")):
            r = self.post()
        self.assertEqual(r.status_code, 401)
        # The reason must not leak back to the caller.
        self.assertNotIn("signature", str(r.data).lower())

    def test_missing_credential_is_refused(self):
        self.assertEqual(self.client.post("/api/auth/google/", {}, format="json").status_code, 401)

    def test_archived_and_wrong_role_denials_are_indistinguishable(self):
        """The refusals that remain still say the same thing as each other.

        Enumeration through this endpoint requires already controlling the
        Google account being probed, so an attacker only ever learns about
        themselves — which is why the pending path may be specific. These two
        refusals stay uniform anyway: they cost nothing, and neither reveals
        which agency addresses exist.
        """
        self.staff.status = User.ARCHIVED
        self.staff.save(update_fields=["status"])
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph")):
            archived = str(self.post().data)
        no_role = make_user("norole@racco1.gov.ph", Role.STAFF)
        no_role.role = None
        no_role.save(update_fields=["role"])
        with mock.patch(VERIFY, return_value=claims("norole@racco1.gov.ph", sub="sub-2")):
            roleless = str(self.post().data)
        self.assertEqual(archived, roleless)

    @override_settings(GOOGLE_ALLOWED_DOMAINS=["racco1.gov.ph"])
    def test_domain_allowlist_blocks_outside_addresses(self):
        outsider = make_user("someone@gmail.com", Role.STAFF)
        with mock.patch(VERIFY, return_value=claims(outsider.email, sub="sub-3")):
            self.assertEqual(self.post().status_code, 401)

    @override_settings(GOOGLE_ALLOWED_DOMAINS=["racco1.gov.ph"])
    def test_domain_allowlist_admits_agency_addresses(self):
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph")):
            self.assertEqual(self.post().status_code, 200)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="")
    def test_feature_off_refuses_cleanly(self):
        with mock.patch(VERIFY, return_value=claims("staff@racco1.gov.ph")) as verify:
            r = self.post()
        self.assertEqual(r.status_code, 401)
        verify.assert_not_called()

    # --- password login is untouched ------------------------------------

    def test_password_login_still_works_for_admin(self):
        self.admin.set_password("admin1234")
        self.admin.must_change_password = False
        self.admin.save()
        r = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("access", r.data)

    def test_password_login_still_works_for_staff(self):
        self.staff.set_password("staff1234")
        self.staff.must_change_password = False
        self.staff.save()
        r = self.client.post("/api/auth/login/", {
            "email": "staff@racco1.gov.ph", "password": "staff1234"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)


class GoogleConfigEndpointTests(APITestCase):
    @override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_ALLOWED_DOMAINS=[])
    def test_reports_disabled_when_unconfigured(self):
        r = self.client.get("/api/auth/google/config/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["enabled"])

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=GOOGLE_CLIENT_ID, GOOGLE_ALLOWED_DOMAINS=[])
    def test_reports_enabled_and_is_reachable_anonymously(self):
        # The login page has to read this before anyone has a token.
        r = self.client.get("/api/auth/google/config/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["enabled"])
        self.assertEqual(r.data["client_id"], GOOGLE_CLIENT_ID)


@override_settings(GOOGLE_OAUTH_CLIENT_ID=GOOGLE_CLIENT_ID, GOOGLE_ALLOWED_DOMAINS=[])
class GoogleSignUpTests(APITestCase):
    """Self-service registration. One button registers on first use and signs
    in afterwards; what it never does is let the person decide their own
    access."""

    def setUp(self):
        cache.clear()   # see GoogleLoginTests.setUp
        self.psych_role = Role.objects.get_or_create(role_name=Role.PSYCHOLOGIST)[0]
        self.staff_role = Role.objects.get_or_create(role_name=Role.STAFF)[0]
        self.admin_role = Role.objects.get_or_create(role_name=Role.ADMINISTRATOR)[0]

    def post(self, credential="tok", requested_role=None):
        body = {"credential": credential}
        if requested_role is not None:
            body["requested_role"] = requested_role
        return self.client.post("/api/auth/google/", body, format="json")

    def test_first_call_asks_for_a_role(self):
        with mock.patch(VERIFY, return_value=claims("new@gmail.com")):
            r = self.post()
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.data["role_required"])

    def test_second_call_records_the_claimed_role(self):
        with mock.patch(VERIFY, return_value=claims("new@gmail.com")):
            self.post()
            r = self.post(requested_role=Role.PSYCHOLOGIST)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(r.data["role_required"])
        user = User.objects.get(email="new@gmail.com")
        self.assertEqual(user.requested_role, self.psych_role)

    def test_the_claimed_role_is_not_granted(self):
        """The whole design in one test: asking to be a Psychologist records a
        claim and confers nothing."""
        with mock.patch(VERIFY, return_value=claims("new@gmail.com")):
            self.post(requested_role=Role.PSYCHOLOGIST)
        user = User.objects.get(email="new@gmail.com")
        self.assertEqual(user.requested_role, self.psych_role)
        self.assertIsNone(user.role)
        self.assertEqual(user.status, User.PENDING)
        self.assertFalse(user.is_active)

    def test_administrator_cannot_be_requested(self):
        """Otherwise the single-admin handover rule is bypassed by typing a
        different word into a dropdown."""
        with mock.patch(VERIFY, return_value=claims("sneaky@gmail.com")):
            self.post(requested_role=Role.ADMINISTRATOR)
        user = User.objects.get(email="sneaky@gmail.com")
        self.assertIsNone(user.requested_role)
        self.assertIsNone(user.role)

    def test_a_made_up_role_is_ignored(self):
        with mock.patch(VERIFY, return_value=claims("odd@gmail.com")):
            self.post(requested_role="Supreme Overlord")
        self.assertIsNone(User.objects.get(email="odd@gmail.com").requested_role)

    def test_repeat_sign_in_while_waiting_is_not_an_error(self):
        """A returning applicant sees the same waiting screen, not a failure
        they would retry forever."""
        with mock.patch(VERIFY, return_value=claims("new@gmail.com")):
            self.post(requested_role=Role.STAFF)
            again = self.post()
        self.assertEqual(again.status_code, 403)
        self.assertEqual(again.data["state"], "pending_approval")
        self.assertFalse(again.data["role_required"])
        self.assertEqual(User.objects.filter(email="new@gmail.com").count(), 1)

    def test_the_claim_cannot_be_upgraded_later(self):
        """Ask as Staff, come back claiming Psychologist: the request an
        administrator sees must be the one that was made."""
        with mock.patch(VERIFY, return_value=claims("new@gmail.com")):
            self.post(requested_role=Role.STAFF)
            self.post(requested_role=Role.PSYCHOLOGIST)
        self.assertEqual(
            User.objects.get(email="new@gmail.com").requested_role, self.staff_role)

    def test_signing_up_twice_creates_one_request(self):
        with mock.patch(VERIFY, return_value=claims("new@gmail.com")):
            self.post()
            self.post()
        self.assertEqual(User.objects.filter(email="new@gmail.com").count(), 1)

    def test_pending_account_gets_no_token(self):
        with mock.patch(VERIFY, return_value=claims("new@gmail.com")):
            r = self.post(requested_role=Role.STAFF)
        self.assertNotIn("access", r.data)
        self.assertNotIn("refresh", r.data)

    def test_name_is_taken_from_google(self):
        """So the approval queue shows a person, not just an address."""
        with mock.patch(VERIFY, return_value=claims(
                "new@gmail.com", given_name="Juan", family_name="Dela Cruz")):
            self.post()
        user = User.objects.get(email="new@gmail.com")
        self.assertEqual(user.first_name, "Juan")
        self.assertEqual(user.last_name, "Dela Cruz")

    @override_settings(GOOGLE_ALLOWED_DOMAINS=["racco1.gov.ph"])
    def test_domain_allowlist_still_gates_registration(self):
        """The allowlist is unused today (personal Gmail), but it must keep
        working for the day RACCO I issues agency addresses."""
        with mock.patch(VERIFY, return_value=claims("outsider@gmail.com")):
            r = self.post()
        self.assertEqual(r.status_code, 401)
        self.assertFalse(User.objects.filter(email="outsider@gmail.com").exists())

    def test_an_existing_address_linked_to_another_google_account_is_refused(self):
        """Same email, different Google subject: not a registration, and not a
        collision we should resolve in the stranger's favour."""
        existing = make_user("taken@racco1.gov.ph", Role.STAFF)
        existing.google_sub = "the-real-one"
        existing.save(update_fields=["google_sub"])
        before = User.objects.count()
        with mock.patch(VERIFY, return_value=claims("taken@racco1.gov.ph", sub="impostor")):
            r = self.post()
        self.assertEqual(r.status_code, 401)
        self.assertEqual(User.objects.count(), before)


@override_settings(GOOGLE_OAUTH_CLIENT_ID=GOOGLE_CLIENT_ID, GOOGLE_ALLOWED_DOMAINS=[])
class GoogleSignUpThrottlingTests(APITestCase):
    """The sign-up endpoint is the one place anonymous traffic writes a row.
    These limits exist to keep the approval queue reviewable by a human — an
    administrator scrolling past a hundred fakes is how a real one gets waved
    through, and approval is the only access control this system has."""

    def setUp(self):
        Role.objects.get_or_create(role_name=Role.STAFF)
        cache.clear()

    def tearDown(self):
        cache.clear()

    def post(self, email, sub):
        with mock.patch(VERIFY, return_value=claims(email, sub=sub)):
            return self.client.post(
                "/api/auth/google/", {"credential": "tok"}, format="json")

    @override_settings(SIGNUP_MAX_PER_IP=3)
    def test_one_address_cannot_flood_the_queue(self):
        for i in range(3):
            self.assertEqual(self.post(f"a{i}@gmail.com", f"sub-{i}").status_code, 403)
        blocked = self.post("a99@gmail.com", "sub-99")
        self.assertEqual(blocked.status_code, 429)
        self.assertFalse(User.objects.filter(email="a99@gmail.com").exists())

    @override_settings(SIGNUP_MAX_PER_IP=1)
    def test_checking_your_own_status_is_never_throttled(self):
        """The limit counts rows created, not calls made. Someone coming back
        to see whether they have been approved must not be locked out of
        reading their own status by their own earlier request."""
        self.assertEqual(self.post("hopeful@gmail.com", "sub-1").status_code, 403)
        for _ in range(5):
            again = self.post("hopeful@gmail.com", "sub-1")
            self.assertEqual(again.status_code, 403)
            self.assertEqual(again.data["state"], "pending_approval")

    @override_settings(SIGNUP_MAX_PER_IP=1)
    def test_an_approved_user_can_still_sign_in_after_the_cap(self):
        """A throttled IP must not become a denial of service against people
        who already have accounts."""
        staff = make_user("real@racco1.gov.ph", Role.STAFF)
        self.post("filler@gmail.com", "sub-filler")
        r = self.post(staff.email, "sub-real")
        self.assertEqual(r.status_code, 200, r.data)

    @override_settings(SIGNUP_MAX_PENDING=2)
    def test_global_ceiling_holds_when_the_cache_does_not(self):
        """The durable half of the design. Counted in the database, so it
        survives a restart and is shared across workers — unlike the per-IP
        counter, which is neither."""
        self.assertEqual(self.post("q1@gmail.com", "sub-1").status_code, 403)
        self.assertEqual(self.post("q2@gmail.com", "sub-2").status_code, 403)
        cache.clear()   # as a restart or a second worker would
        blocked = self.post("q3@gmail.com", "sub-3")
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(User.objects.filter(status=User.PENDING).count(), 2)

    @override_settings(SIGNUP_MAX_PENDING=1)
    def test_the_ceiling_lifts_once_the_queue_is_cleared(self):
        """It is a ceiling on OUTSTANDING requests, not a lifetime quota —
        approving or declining makes room again."""
        self.assertEqual(self.post("first@gmail.com", "sub-1").status_code, 403)
        self.assertEqual(self.post("second@gmail.com", "sub-2").status_code, 429)
        User.objects.filter(email="first@gmail.com").update(status=User.ARCHIVED)
        self.assertEqual(self.post("second@gmail.com", "sub-2").status_code, 403)

    @override_settings(SIGNUP_MAX_PER_IP=1)
    def test_the_refusal_does_not_say_which_limit_was_hit(self):
        self.post("one@gmail.com", "sub-1")
        body = str(self.post("two@gmail.com", "sub-2").data).lower()
        for leak in ("queue", "pending", "ip", "address", "full"):
            self.assertNotIn(leak, body)


@override_settings(GOOGLE_OAUTH_CLIENT_ID=GOOGLE_CLIENT_ID, GOOGLE_ALLOWED_DOMAINS=[])
class AccessRequestJourneyTests(APITestCase):
    """The whole path, end to end, in the order it actually happens.

    The individual guards are covered elsewhere; this is here because they can
    each pass while the journey between them is broken, and the journey is what
    a person experiences.
    """

    def setUp(self):
        cache.clear()
        self.staff_role = Role.objects.get_or_create(role_name=Role.STAFF)[0]
        self.psych_role = Role.objects.get_or_create(role_name=Role.PSYCHOLOGIST)[0]
        self.admin_role = Role.objects.get_or_create(role_name=Role.ADMINISTRATOR)[0]
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=self.admin_role)

    def google(self, email, sub, requested_role=None):
        body = {"credential": "tok"}
        if requested_role:
            body["requested_role"] = requested_role
        with mock.patch(VERIFY, return_value=claims(email, sub=sub)):
            return self.client.post("/api/auth/google/", body, format="json")

    def as_admin(self):
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def test_request_then_approval_then_sign_in(self):
        # 1. A new psychologist signs up and says what she does.
        first = self.google("maria@gmail.com", "sub-maria")
        self.assertEqual(first.status_code, 403)
        self.assertTrue(first.data["role_required"])
        self.google("maria@gmail.com", "sub-maria", requested_role=Role.PSYCHOLOGIST)

        maria = User.objects.get(email="maria@gmail.com")
        self.assertEqual(maria.status, User.PENDING)

        # 2. While waiting she has nothing: no token was ever issued, and the
        #    account cannot authenticate by any route.
        waiting = self.google("maria@gmail.com", "sub-maria")
        self.assertNotIn("access", waiting.data)
        self.assertFalse(maria.is_active)

        # 3. The administrator approves her — as Staff, not the Psychologist
        #    she asked for, because that is the administrator's call.
        self.as_admin()
        approved = self.client.post(f"/api/users/{maria.id}/approve/",
                                    {"role": self.staff_role.id})
        self.assertEqual(approved.status_code, 200, approved.data)
        self.client.credentials()

        # 4. Now the same Google account signs straight in, with the role the
        #    administrator chose.
        signed_in = self.google("maria@gmail.com", "sub-maria")
        self.assertEqual(signed_in.status_code, 200, signed_in.data)
        self.assertEqual(signed_in.data["user"]["role_name"], Role.STAFF)

        # 5. And the token works against a real endpoint, not just the login.
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + signed_in.data["access"])
        self.assertEqual(self.client.get("/api/children/").status_code, 200)

    def test_request_then_decline_then_locked_out_for_good(self):
        self.google("chancer@gmail.com", "sub-chancer", requested_role=Role.PSYCHOLOGIST)
        chancer = User.objects.get(email="chancer@gmail.com")

        self.as_admin()
        declined = self.client.post(f"/api/users/{chancer.id}/decline/")
        self.assertEqual(declined.status_code, 200)
        self.client.credentials()

        # Refused, and — the part that matters — not quietly re-queued as a
        # fresh request for a later administrator to wave through.
        again = self.google("chancer@gmail.com", "sub-chancer")
        self.assertEqual(again.status_code, 401)
        self.assertEqual(User.objects.filter(email="chancer@gmail.com").count(), 1)
        self.assertEqual(User.objects.filter(status=User.PENDING).count(), 0)

    def test_an_approved_person_appears_in_the_directory_as_active(self):
        self.google("newstaff@gmail.com", "sub-new", requested_role=Role.STAFF)
        user = User.objects.get(email="newstaff@gmail.com")
        self.as_admin()
        self.client.post(f"/api/users/{user.id}/approve/", {"role": self.staff_role.id})
        row = next(u for u in self.client.get("/api/users/").data
                   if u["email"] == "newstaff@gmail.com")
        self.assertEqual(row["status"], User.ACTIVE)
        self.assertEqual(row["role_name"], Role.STAFF)
        self.assertTrue(row["google_linked"])
        # The claim is still on the record afterwards: the audit answer to
        # "what did they ask for?" must survive the decision.
        self.assertEqual(row["requested_role_name"], Role.STAFF)
