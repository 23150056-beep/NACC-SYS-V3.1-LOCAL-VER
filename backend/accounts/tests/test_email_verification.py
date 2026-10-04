"""Prove the address exists before anything is sent to it.

The sign-up form verifies nothing, and that matters at exactly one moment:
approval emails a temporary password. Approve a request whose address was
mistyped and the credential goes to whoever owns the typo, or nowhere at all
while the applicant waits for a mail that cannot arrive.

The pattern is the one already used for phone numbers - a six-digit code in
a database row (EmailVerification), a few minutes to use it, a limited number
of guesses - because a second way of doing the same thing is a second thing to
get wrong.

**Google requests skip it.** Google already verified the address; asking the
applicant to prove it again would be ceremony, and the queue has always
recorded which door a request came through for precisely this reason.

**Approval is refused until it is verified.** That is the whole point: the
check has to bite where the credential is issued, not merely be displayed
beside the row for somebody to notice.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts import email_verification, signup_limit
from accounts.models import EmailVerification, Role

User = get_user_model()

VERIFY = "/api/auth/signup/verify-email/"
RESEND = "/api/auth/signup/verify-email/resend/"
MAILER = "accounts.email_verification.send_verification_email"


class SignupAsksForProofTest(APITestCase):
    ADDRESS = "applicant@racco1.gov.ph"

    def setUp(self):
        cache.clear()
        Role.objects.create(role_name=Role.STAFF)

    def _code(self, email=ADDRESS):
        return EmailVerification.objects.get(user__email=email).code

    def _confirm(self, code, email=ADDRESS):
        return self.client.post(VERIFY, {"email": email, "code": code}, format="json")

    def _signup(self, email="applicant@racco1.gov.ph"):
        return self.client.post("/api/auth/signup/", {
            "email": email, "password": "Str0ngPass!2026",
            "first_name": "Ana", "last_name": "Lopez",
            "requested_role": "Staff"}, format="json")

    def test_signing_up_still_creates_a_pending_request(self):
        with patch(MAILER, return_value=True):
            self.assertEqual(202, self._signup().status_code)
        user = User.objects.get(email="applicant@racco1.gov.ph")
        self.assertEqual(User.PENDING, user.status)

    def test_the_address_starts_unverified(self):
        with patch(MAILER, return_value=True):
            self._signup()
        self.assertFalse(User.objects.get(email="applicant@racco1.gov.ph").email_verified)

    def test_a_code_is_sent_to_the_address_given(self):
        with patch(MAILER, return_value=True) as sent:
            self._signup()
        self.assertEqual("applicant@racco1.gov.ph", sent.call_args[0][0])

    def test_the_right_code_verifies_the_address(self):
        with patch(MAILER, return_value=True):
            self._signup()
        code = self._code()
        response = self._confirm(code)
        self.assertEqual(200, response.status_code, response.data)
        self.assertTrue(User.objects.get(email="applicant@racco1.gov.ph").email_verified)

    def test_a_wrong_code_does_not(self):
        with patch(MAILER, return_value=True):
            self._signup()
        response = self._confirm("000000")
        self.assertEqual(400, response.status_code)
        self.assertFalse(User.objects.get(email="applicant@racco1.gov.ph").email_verified)

    def test_guessing_is_limited(self):
        with patch(MAILER, return_value=True):
            self._signup()
        for _ in range(email_verification.MAX_ATTEMPTS + 1):
            self._confirm("000000")
        self.assertEqual("", self._code(), "the code should be burned after too many guesses")

    def test_an_unknown_address_is_answered_the_same_way(self):
        # Same reasoning as the sign-up form's single refusal message: a
        # different answer here would turn this into a way to ask whether
        # somebody works at the agency.
        response = self._confirm("000000", "stranger@example.com")
        self.assertEqual(400, response.status_code)

    def test_the_code_survives_a_different_worker(self):
        # Under gunicorn each worker has its own cache, so a code kept there
        # was "expired" to whichever worker took the reply.
        with patch(MAILER, return_value=True):
            self._signup()
        code = self._code()
        cache.clear()
        response = self._confirm(code)
        self.assertEqual(200, response.status_code, response.data)
        self.assertTrue(User.objects.get(email=self.ADDRESS).email_verified)

    def test_the_right_code_after_too_many_guesses_is_refused(self):
        with patch(MAILER, return_value=True):
            self._signup()
        real = self._code()
        wrong = "111111" if real != "111111" else "222222"
        for _ in range(email_verification.MAX_ATTEMPTS):
            self._confirm(wrong)
        response = self._confirm(real)
        self.assertEqual(400, response.status_code)
        self.assertFalse(User.objects.get(email=self.ADDRESS).email_verified)

    def test_an_expired_code_does_not_verify(self):
        with patch(MAILER, return_value=True):
            self._signup()
        EmailVerification.objects.filter(user__email=self.ADDRESS).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        response = self._confirm(self._code())
        self.assertEqual(400, response.status_code)
        self.assertFalse(User.objects.get(email=self.ADDRESS).email_verified)

    def test_the_code_lasts_fifteen_minutes_and_guessing_does_not_extend_it(self):
        with patch(MAILER, return_value=True):
            self._signup()
        row = EmailVerification.objects.get(user__email=self.ADDRESS)
        left = row.expires_at - timezone.now()
        self.assertTrue(timedelta(minutes=14) < left <= timedelta(minutes=15), left)
        self._confirm("000000" if row.code != "000000" else "000001")
        again = EmailVerification.objects.get(pk=row.pk)
        self.assertEqual(row.expires_at, again.expires_at)
        self.assertEqual(1, again.tries)

    def test_every_refusal_reads_the_same(self):
        # The phone flow tells "expired" from "wrong" from "too many". Copying
        # that here would turn an open endpoint into a way to ask who applied.
        second = "second@racco1.gov.ph"
        with patch(MAILER, return_value=True):
            self._signup()
            self._signup(second)
        real = self._code()
        responses = [self._confirm(real, "stranger@example.com"),
                     self._confirm("000000" if real != "000000" else "000001")]
        EmailVerification.objects.filter(user__email=self.ADDRESS).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        responses.append(self._confirm(real))
        burned = self._code(second)
        for _ in range(email_verification.MAX_ATTEMPTS + 1):
            responses.append(self._confirm("000000" if burned != "000000" else "000001", second))
        responses.append(self._confirm(burned, second))
        self.assertEqual({400}, {r.status_code for r in responses})
        self.assertEqual(1, len({r.data["detail"] for r in responses}),
                         [r.data["detail"] for r in responses])

    def test_a_code_does_not_vouch_for_an_address_it_was_not_sent_to(self):
        # An administrator can correct a pending applicant's address. A code
        # mailed to the typo must not confirm the corrected one.
        with patch(MAILER, return_value=True):
            self._signup()
        code = self._code()
        User.objects.filter(email=self.ADDRESS).update(email="corrected@racco1.gov.ph")
        self.assertEqual(400, self._confirm(code, "corrected@racco1.gov.ph").status_code)
        self.assertEqual(400, self._confirm(code, self.ADDRESS).status_code)
        self.assertFalse(User.objects.get(email="corrected@racco1.gov.ph").email_verified)

    def test_a_declined_request_cannot_be_confirmed(self):
        with patch(MAILER, return_value=True):
            self._signup()
        user = User.objects.get(email=self.ADDRESS)
        user.status = User.ARCHIVED   # what decline does
        user.save()
        response = self._confirm(self._code())
        self.assertEqual(400, response.status_code)
        self.assertFalse(User.objects.get(email=self.ADDRESS).email_verified)

    def test_a_refused_send_leaves_no_code(self):
        with patch(MAILER, return_value=False):
            response = self._signup()
        self.assertEqual(202, response.status_code)
        self.assertEqual("", self._code())
        self.assertEqual(400, self._confirm("123456").status_code)

    def test_the_code_goes_with_the_request(self):
        # No address outlives the request it was collected for.
        with patch(MAILER, return_value=True):
            self._signup()
        User.objects.get(email=self.ADDRESS).delete()
        self.assertEqual(0, EmailVerification.objects.count())

    def test_a_body_that_is_not_an_object_is_refused_not_a_crash(self):
        for body in ([], "text", [{"email": "a@b.co"}]):
            response = self.client.post(VERIFY, body, format="json")
            self.assertEqual(400, response.status_code, body)
        response = self.client.post(
            VERIFY, {"email": ["a@b.co"], "code": 123456}, format="json")
        self.assertEqual(400, response.status_code)


class ResendingTheCodeTest(APITestCase):
    """The confirm screen says "Ask for a new one". This is the asking."""

    ADDRESS = "applicant@racco1.gov.ph"

    def setUp(self):
        cache.clear()
        Role.objects.create(role_name=Role.STAFF)

    def _signup(self, email=ADDRESS):
        return self.client.post("/api/auth/signup/", {
            "email": email, "password": "Str0ngPass!2026",
            "first_name": "Ana", "last_name": "Lopez",
            "requested_role": "Staff"}, format="json")

    def _row(self, email=ADDRESS):
        return EmailVerification.objects.get(user__email=email)

    def _code(self, email=ADDRESS):
        return self._row(email).code

    def _confirm(self, code, email=ADDRESS):
        return self.client.post(VERIFY, {"email": email, "code": code}, format="json")

    def _resend(self, email=ADDRESS):
        return self.client.post(RESEND, {"email": email}, format="json")

    def _a_minute_passes(self, email=ADDRESS):
        # Moves the last send back; the hour's count keeps its place.
        EmailVerification.objects.filter(user__email=email).update(
            last_sent_at=timezone.now() - timedelta(seconds=61))

    def test_it_says_the_same_thing_to_everybody(self):
        # Whatever the address and whatever happened, or the door is a way
        # to ask who has applied.
        with patch(MAILER, return_value=True):
            self._signup()
            self._signup("verified@racco1.gov.ph")
            self._signup("archived@racco1.gov.ph")
            self._signup("active@racco1.gov.ph")
        User.objects.filter(email="verified@racco1.gov.ph").update(email_verified=True)
        archived = User.objects.get(email="archived@racco1.gov.ph")
        archived.status = User.ARCHIVED
        archived.save()
        active = User.objects.get(email="active@racco1.gov.ph")
        active.status = User.ACTIVE
        active.save()
        # A Google request that is not marked verified: only the door it came
        # through can keep it out here, not the flag.
        User.objects.create_user(
            email="google@racco1.gov.ph", username="google@racco1.gov.ph",
            password="x", status=User.PENDING, google_sub="sub-1",
            email_verified=False)

        answers = []
        for email in ("applicant@racco1.gov.ph", "nobody@racco1.gov.ph",
                      "verified@racco1.gov.ph", "archived@racco1.gov.ph",
                      "active@racco1.gov.ph", "google@racco1.gov.ph",
                      "APPLICANT@racco1.gov.ph", "", "not an address"):
            for other in ("applicant", "verified", "archived", "active"):
                self._a_minute_passes(f"{other}@racco1.gov.ph")
            with patch(MAILER, return_value=True) as sent:
                response = self._resend(email)
            answers.append((email, response.status_code, response.data))
            expected = 1 if email.lower() == "applicant@racco1.gov.ph" else 0
            self.assertEqual(expected, sent.call_count,
                             f"{email!r} mailed {sent.call_count} times")
        self.assertEqual({202}, {a[1] for a in answers}, answers)
        self.assertEqual(1, len({str(a[2]) for a in answers}), answers)
        self.assertEqual(email_verification.RESEND_REPLY, answers[0][2]["detail"])

    def test_a_pending_typed_request_gets_a_new_code(self):
        with patch(MAILER, return_value=True):
            self._signup()
        self._a_minute_passes()
        with patch(MAILER, return_value=True) as sent:
            response = self._resend()
        self.assertEqual(202, response.status_code)
        sent.assert_called_once_with(self.ADDRESS, self._code())
        self.assertEqual(200, self._confirm(self._code()).status_code)
        self.assertTrue(User.objects.get(email=self.ADDRESS).email_verified)

    def test_the_old_code_stops_working_when_a_new_one_is_issued(self):
        with patch("accounts.email_verification.secrets.randbelow",
                   side_effect=[111111, 222222]), patch(MAILER, return_value=True):
            self._signup()
            self._a_minute_passes()
            self._resend()
        self.assertEqual("222222", self._code())
        self.assertEqual(400, self._confirm("111111").status_code)
        self.assertFalse(User.objects.get(email=self.ADDRESS).email_verified)
        self.assertEqual(200, self._confirm("222222").status_code)

    def test_a_new_code_starts_the_guesses_and_the_clock_again(self):
        with patch(MAILER, return_value=True):
            self._signup()
            for _ in range(3):
                self._confirm("000000" if self._code() != "000000" else "000001")
            EmailVerification.objects.filter(user__email=self.ADDRESS).update(
                expires_at=timezone.now() + timedelta(seconds=5))
            self._a_minute_passes()
            self._resend()
        row = self._row()
        self.assertEqual(0, row.tries)
        self.assertTrue(row.expires_at - timezone.now() > timedelta(minutes=14))

    def test_a_burned_code_can_be_replaced(self):
        # The case this door exists for: anybody who knows an address can burn
        # its code with six wrong guesses, and the applicant has no other way
        # back.
        with patch(MAILER, return_value=True):
            self._signup()
        real = self._code()
        wrong = "111111" if real != "111111" else "222222"
        for _ in range(email_verification.MAX_ATTEMPTS + 1):
            self._confirm(wrong)
        self.assertEqual("", self._code())
        self._a_minute_passes()
        with patch(MAILER, return_value=True):
            self._resend()
        self.assertEqual(200, self._confirm(self._code()).status_code)

    def test_once_a_minute(self):
        with patch(MAILER, return_value=True) as sent:
            self._signup()
            first = self._code()
            # The sign-up's own mail counts: asking straight away sends nothing.
            self.assertEqual(202, self._resend().status_code)
            self.assertEqual(1, sent.call_count)
            self.assertEqual(first, self._code())
            self._a_minute_passes()
            self._resend()
            self.assertEqual(2, sent.call_count)
            # And that one counts too.
            self._resend()
            self.assertEqual(2, sent.call_count)

    def test_five_an_hour(self):
        with patch(MAILER, return_value=True) as sent:
            self._signup()
            for _ in range(email_verification.MAX_PER_HOUR - 1):
                self._a_minute_passes()
                self._resend()
            self.assertEqual(email_verification.MAX_PER_HOUR, sent.call_count)
            # A minute apart is fine; six in the hour is not.
            self._a_minute_passes()
            response = self._resend()
            self.assertEqual(202, response.status_code)
            self.assertEqual(email_verification.MAX_PER_HOUR, sent.call_count)
            # The window is an hour from its first send.
            EmailVerification.objects.filter(user__email=self.ADDRESS).update(
                window_started_at=timezone.now() - timedelta(minutes=61),
                last_sent_at=timezone.now() - timedelta(minutes=61))
            self._resend()
            self.assertEqual(email_verification.MAX_PER_HOUR + 1, sent.call_count)
            self.assertEqual(1, self._row().sent_in_window)

    def test_a_refused_send_does_not_count_against_the_limits(self):
        with patch(MAILER, return_value=True):
            self._signup()
        self._a_minute_passes()
        before = self._row()
        with patch(MAILER, return_value=False):
            self.assertEqual(202, self._resend().status_code)
        after = self._row()
        self.assertEqual("", after.code)
        self.assertEqual(before.sent_in_window, after.sent_in_window)
        self.assertEqual(before.last_sent_at, after.last_sent_at)
        # So the next ask, straight away, is not made to wait a minute.
        with patch(MAILER, return_value=True) as sent:
            self._resend()
        self.assertEqual(1, sent.call_count)
        self.assertNotEqual("", self._code())

    def test_the_limits_are_shared_by_every_worker(self):
        with patch(MAILER, return_value=True) as sent:
            self._signup()
            cache.clear()
            self._resend()
        self.assertEqual(1, sent.call_count)

    @override_settings(SIGNUP_RESEND_MAX_PER_IP=2)
    def test_one_address_cannot_work_through_requests_without_end(self):
        with patch(MAILER, return_value=True) as sent:
            for n in range(4):
                self._signup(f"person{n}@racco1.gov.ph")
            sent.reset_mock()
            for n in range(4):
                self._a_minute_passes(f"person{n}@racco1.gov.ph")
                response = self._resend(f"person{n}@racco1.gov.ph")
                self.assertEqual(202, response.status_code)
        self.assertEqual(2, sent.call_count)

    @override_settings(SIGNUP_MAX_PER_IP=3, SIGNUP_RESEND_MAX_PER_IP=50)
    def test_asking_for_codes_does_not_use_up_the_allowance_for_signing_up(self):
        with patch(MAILER, return_value=True):
            self._signup()
            for _ in range(5):
                self._a_minute_passes()
                self._resend()
            self.assertEqual(1, signup_limit.attempts_from("127.0.0.1"))
            self.assertEqual(202, self._signup("second@racco1.gov.ph").status_code)

    @override_settings(SIGNUP_MAX_PER_IP=1, SIGNUP_RESEND_MAX_PER_IP=50)
    def test_having_signed_up_does_not_stop_a_request_for_a_new_code(self):
        with patch(MAILER, return_value=True) as sent:
            self._signup()
            self.assertTrue(signup_limit.ip_is_throttled("127.0.0.1"))
            self._a_minute_passes()
            self._resend()
        self.assertEqual(2, sent.call_count)

    def test_a_body_that_is_not_an_object_gets_the_same_answer(self):
        for body in ([], "text", [{"email": "a@b.co"}], {"email": ["a@b.co"]},
                     {"email": 5}, {}):
            response = self.client.post(RESEND, body, format="json")
            self.assertEqual(202, response.status_code, body)
            self.assertEqual(email_verification.RESEND_REPLY, response.data["detail"])

    def test_it_needs_no_sign_in(self):
        # Open by design - the applicant cannot sign in - and a bad token is
        # not looked at.
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-token")
        self.assertEqual(202, self._resend().status_code)


class ApprovalRequiresItTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR))
        self.applicant = User.objects.create_user(
            email="applicant@racco1.gov.ph", username="applicant",
            password="pass1234", status=User.PENDING)
        self.applicant.role = None
        self.applicant.save()
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _approve(self):
        return self.client.post(f"/api/users/{self.applicant.id}/approve/",
                                {"role": self.staff_role.id}, format="json")

    def test_an_unverified_address_cannot_be_approved(self):
        response = self._approve()
        self.assertEqual(400, response.status_code)
        self.assertIn("verif", str(response.data).lower())

    def test_a_verified_address_can(self):
        self.applicant.email_verified = True
        self.applicant.save(update_fields=["email_verified"])
        self.assertEqual(200, self._approve().status_code, )

    def test_a_google_request_counts_as_verified(self):
        # Google checked the address. Making the applicant prove it again
        # would be ceremony, and the queue records which door they used.
        self.applicant.google_sub = "google-abc"
        self.applicant.email_verified = True
        self.applicant.save(update_fields=["google_sub", "email_verified"])
        self.assertEqual(200, self._approve().status_code)


class CorrectingAPendingAddressTest(APITestCase):
    """An administrator can fix a typo in a waiting request's address. What
    was proved was the OLD address, so the new one has to be proved again
    before approval mails it a temporary password."""

    def setUp(self):
        cache.clear()
        self.staff_role = Role.objects.create(role_name=Role.STAFF)
        self.admin = User.objects.create_user(
            email="admin@racco1.gov.ph", username="admin", password="admin1234",
            role=Role.objects.create(role_name=Role.ADMINISTRATOR))
        self.applicant = User.objects.create_user(
            email="applicant@racco1.gov.ph", username="applicant@racco1.gov.ph",
            password="pass1234", status=User.PENDING, email_verified=True,
            first_name="Ana", last_name="Lopez")
        self.applicant.role = None
        self.applicant.save()
        token = self.client.post("/api/auth/login/", {
            "email": "admin@racco1.gov.ph", "password": "admin1234"}).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token)

    def _verified(self):
        return User.objects.get(pk=self.applicant.pk).email_verified

    def _approve(self):
        return self.client.post(f"/api/users/{self.applicant.id}/approve/",
                                {"role": self.staff_role.id}, format="json")

    def test_a_patch_to_the_address_clears_it(self):
        response = self.client.patch(f"/api/users/{self.applicant.id}/",
                                     {"email": "ana.lopez@racco1.gov.ph"}, format="json")
        self.assertEqual(200, response.status_code, response.data)
        self.assertEqual("ana.lopez@racco1.gov.ph",
                         User.objects.get(pk=self.applicant.pk).email)
        self.assertFalse(self._verified())

    def test_a_put_to_the_address_clears_it(self):
        response = self.client.put(f"/api/users/{self.applicant.id}/", {
            "email": "ana.lopez@racco1.gov.ph", "first_name": "Ana",
            "last_name": "Lopez", "status": User.PENDING}, format="json")
        self.assertEqual(200, response.status_code, response.data)
        self.assertFalse(self._verified())

    def test_approval_then_needs_the_new_address_proved(self):
        self.client.patch(f"/api/users/{self.applicant.id}/",
                          {"email": "ana.lopez@racco1.gov.ph"}, format="json")
        response = self._approve()
        self.assertEqual(400, response.status_code)
        self.assertIn("verif", str(response.data).lower())

    def test_the_new_address_can_be_proved_by_asking_for_a_code(self):
        # The way back, end to end: the old code was mailed to the old
        # address, so a new one goes to the new address.
        EmailVerification.objects.create(
            user=self.applicant, email="applicant@racco1.gov.ph", code="999999",
            expires_at=timezone.now() + timedelta(minutes=10),
            last_sent_at=timezone.now() - timedelta(minutes=5),
            window_started_at=timezone.now() - timedelta(minutes=5), sent_in_window=1)
        self.client.patch(f"/api/users/{self.applicant.id}/",
                          {"email": "ana.lopez@racco1.gov.ph"}, format="json")
        self.client.credentials()
        # The code mailed to the old address does not count for the new one.
        old = self.client.post(VERIFY, {"email": "ana.lopez@racco1.gov.ph",
                                        "code": "999999"}, format="json")
        self.assertEqual(400, old.status_code)
        with patch(MAILER, return_value=True) as sent:
            self.client.post(RESEND, {"email": "ana.lopez@racco1.gov.ph"}, format="json")
        self.assertEqual("ana.lopez@racco1.gov.ph", sent.call_args[0][0])
        code = EmailVerification.objects.get(user=self.applicant).code
        done = self.client.post(VERIFY, {"email": "ana.lopez@racco1.gov.ph",
                                         "code": code}, format="json")
        self.assertEqual(200, done.status_code, done.data)
        self.assertTrue(self._verified())

    def test_other_edits_leave_it_alone(self):
        self.client.patch(f"/api/users/{self.applicant.id}/",
                          {"first_name": "Anna"}, format="json")
        self.assertTrue(self._verified())

    def test_naming_the_same_address_again_leaves_it_alone(self):
        # The edit form resends every field with each save.
        self.client.patch(f"/api/users/{self.applicant.id}/",
                          {"email": "applicant@racco1.gov.ph"}, format="json")
        self.assertTrue(self._verified())

    def test_a_change_of_letter_case_is_not_a_new_address(self):
        self.client.patch(f"/api/users/{self.applicant.id}/",
                          {"email": "Applicant@racco1.gov.ph"}, format="json")
        self.assertTrue(self._verified())

    def test_an_active_account_is_not_touched(self):
        colleague = User.objects.create_user(
            email="colleague@racco1.gov.ph", username="colleague@racco1.gov.ph",
            password="pass1234", role=self.staff_role, email_verified=True)
        response = self.client.patch(f"/api/users/{colleague.id}/",
                                     {"email": "colleague.new@racco1.gov.ph"}, format="json")
        self.assertEqual(200, response.status_code, response.data)
        colleague.refresh_from_db()
        self.assertEqual("colleague.new@racco1.gov.ph", colleague.email)
        self.assertTrue(colleague.email_verified)
