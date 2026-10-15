import importlib

from django.apps import apps
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APITestCase
from django.contrib.auth import get_user_model

from accounts import lockout
from accounts.models import Role
from activity.models import ActivityLog

User = get_user_model()

EMAIL = "staff@racco1.gov.ph"
PASSWORD = "staff1234"
IP = "127.0.0.1"  # Django test client's default REMOTE_ADDR


class LoginLockoutTest(APITestCase):
    """accounts.lockout wired into LoginView. Cache state is process-global
    (LocMemCache), so it's cleared before AND after every test — otherwise
    counters from one test would leak into the next."""

    def setUp(self):
        cache.clear()
        self.role = Role.objects.create(role_name=Role.STAFF)
        self.user = User.objects.create_user(
            email=EMAIL, username="staff", password=PASSWORD, role=self.role)

    def tearDown(self):
        cache.clear()

    def _login(self, email=EMAIL, password="wrongpass"):
        return self.client.post("/api/auth/login/", {"email": email, "password": password})

    # ---- core lockout behavior ----

    def test_normal_login_still_works(self):
        resp = self._login(password=PASSWORD)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("access", resp.data)

    def test_sixth_attempt_locked_even_with_correct_password(self):
        for _ in range(5):
            self.assertEqual(self._login().status_code, 401)
        resp = self._login(password=PASSWORD)
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Try again in", resp.data["detail"])

    def test_successful_login_before_threshold_clears_counter(self):
        for _ in range(4):
            self.assertEqual(self._login().status_code, 401)
        self.assertEqual(self._login(password=PASSWORD).status_code, 200)
        for _ in range(4):
            self.assertEqual(self._login().status_code, 401)
        # Still not locked — the success reset the combo counter.
        locked, _retry_after = lockout.is_locked(EMAIL, IP)
        self.assertFalse(locked)
        self.assertEqual(self._login(password=PASSWORD).status_code, 200)

    def test_lock_expires(self):
        for _ in range(5):
            self._login()
        self.assertEqual(self._login(password=PASSWORD).status_code, 429)

        # Simulate the lock's TTL elapsing rather than sleeping 15 minutes.
        cache.delete(lockout._combo_lock_key(EMAIL, IP))

        resp = self._login(password=PASSWORD)
        self.assertEqual(resp.status_code, 200)

    def test_unknown_email_failures_count_and_lock(self):
        unknown = "nobody@racco1.gov.ph"
        for _ in range(5):
            resp = self._login(email=unknown)
            self.assertEqual(resp.status_code, 401)
        resp = self._login(email=unknown, password="whatever")
        self.assertEqual(resp.status_code, 429)

    def test_ip_wide_lockout_across_many_emails(self):
        for i in range(20):
            resp = self._login(email=f"nouser{i}@racco1.gov.ph")
            self.assertEqual(resp.status_code, 401)
        # A brand-new email from the same IP is still blocked by the IP-wide lock.
        resp = self._login(email="yet-another@racco1.gov.ph")
        self.assertEqual(resp.status_code, 429)
        # Even the legitimate user with the correct password is blocked.
        resp = self._login(password=PASSWORD)
        self.assertEqual(resp.status_code, 429)

    def test_lockout_logs_activity_exactly_once(self):
        for _ in range(5):
            self._login()
        # Further blocked attempts must not add more log entries.
        for _ in range(3):
            self._login()

        logs = ActivityLog.objects.filter(
            category=ActivityLog.SECURITY, entity_label__contains="Login locked")
        self.assertEqual(logs.count(), 1)
        self.assertIn(EMAIL, logs.first().entity_label)

    def test_register_failure_says_which_lock_it_set(self):
        for n in range(4):
            self.assertEqual(lockout.register_failure(EMAIL, IP)[2], (), n)
        locked, _after, engaged = lockout.register_failure(EMAIL, IP)
        self.assertEqual((locked, engaged), (True, ("account",)))
        # Once set, the same lock is not reported again.
        self.assertEqual(lockout.register_failure(EMAIL, IP)[2], ())

        cache.clear()
        for n in range(19):
            self.assertEqual(lockout.register_failure(f"u{n}@racco1.gov.ph", IP)[2], (), n)
        self.assertEqual(lockout.register_failure("u19@racco1.gov.ph", IP)[2], ("address",))

        cache.clear()
        engaged = None
        for who in range(4):
            for _ in range(5):
                engaged = lockout.register_failure(f"w{who}@racco1.gov.ph", IP)[2]
        # The twentieth failure was also the fifth against the fourth name.
        self.assertEqual(engaged, ("account", "address"))

    def test_an_address_lock_is_logged_as_that_and_names_nobody(self):
        for i in range(20):
            self._login(email=f"nouser{i}@racco1.gov.ph")
        (line,) = ActivityLog.objects.filter(category=ActivityLog.SECURITY,
                                             action=ActivityLog.UPDATED)
        self.assertEqual(line.entity_label, lockout.ADDRESS_LOCK_LABEL)
        self.assertNotIn("nouser", line.entity_label)
        self.assertIsNone(line.actor_id)

    def test_a_typed_password_is_not_kept_in_the_lockout_line(self):
        for _ in range(5):
            self._login(email="Summer2024!")
        (line,) = ActivityLog.objects.filter(entity_label__startswith="Login locked")
        self.assertEqual(
            line.entity_label,
            f"Login locked for {lockout.NOT_AN_EMAIL} after repeated failures")

    # ---- an email that is not text ----
    #
    # The sign-in is open to anyone and a JSON body can hold any value under
    # "email". Each of these used to reach `.strip()` in the lockout keys and
    # answer 500 with nobody signed in.

    def _post_json(self, body):
        return self.client.post("/api/auth/login/", body, format="json")

    def test_an_email_that_is_not_text_ends_as_the_blank_one_does(self):
        blank = self._post_json({"email": "", "password": "x"})
        self.assertEqual(blank.status_code, 400)
        for typed in (["a@racco1.gov.ph"], [], 12345, 1.5, True,
                      {"email": "a@racco1.gov.ph"}, {}):
            with self.subTest(typed=typed):
                resp = self._post_json({"email": typed, "password": "x"})
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(list(resp.data), ["email"])

    def test_a_missing_or_null_email_is_the_same_400(self):
        for body in ({"password": "x"}, {"email": None, "password": "x"}):
            with self.subTest(body=body):
                resp = self._post_json(body)
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(list(resp.data), ["email"])

    def test_a_body_that_is_not_an_object_is_a_400_too(self):
        for body in (["a@racco1.gov.ph", "x"], "just text", 7):
            with self.subTest(body=body):
                self.assertEqual(self._post_json(body).status_code, 400)

    def test_an_email_that_is_not_text_counts_against_nobody(self):
        for _ in range(8):
            self._post_json({"email": ["a@racco1.gov.ph"], "password": "x"})
        # No failure was registered, so neither the blank bucket nor the
        # address was locked by those requests.
        self.assertEqual(lockout.is_locked("", IP), (False, 0))
        self.assertEqual(self._login(password=PASSWORD).status_code, 200)
        self.assertFalse(ActivityLog.objects.filter(
            entity_label__startswith="Login locked").exists())

    def test_the_lockout_keys_and_names_take_anything(self):
        for typed in (None, 5, ["x"], {"a": 1}, 1.5, b"x"):
            with self.subTest(typed=typed):
                self.assertEqual(lockout.is_locked(typed, IP), (False, 0))
                lockout.register_failure(typed, IP)
                lockout.clear_failures(typed, IP)
                self.assertEqual(lockout.readable_sign_in_name(typed), lockout.NOT_AN_EMAIL)


class ScrubOldLockoutNamesTest(TestCase):
    """Migration activity 0009, run on the real model."""

    def setUp(self):
        self.scrub = importlib.import_module(
            "activity.migrations.0009_scrub_lockout_names").keep_only_addresses

    def line(self, label, **extra):
        fields = dict(actor_label="System", action=ActivityLog.UPDATED,
                      category=ActivityLog.SECURITY, entity_type="User", entity_label=label)
        fields.update(extra)
        return ActivityLog.objects.create(**fields).pk

    def label(self, pk):
        return ActivityLog.objects.get(pk=pk).entity_label

    def test_what_is_not_an_address_goes_and_an_address_stays(self):
        kept = self.line("Login locked for Editha@Racco1.gov.ph after repeated failures")
        password = self.line("Login locked for Summer2024! after repeated failures")
        sentence = self.line("Login locked for my password is x after repeated failures")
        odd = self.line("Login locked for p@ss.word! after repeated failures")
        blank = self.line("Login locked for  after repeated failures")
        self.scrub(apps, None)
        self.assertEqual(self.label(kept),
                         "Login locked for Editha@Racco1.gov.ph after repeated failures")
        for pk in (password, sentence, odd, blank):
            self.assertEqual(self.label(pk),
                             "Login locked for an unrecognised sign-in name after repeated failures")

    def test_only_the_lockout_lines_are_touched(self):
        same_words = "Login locked for Summer2024! after repeated failures"
        # The same words from another writer (an actor, another action) are not
        # the login view's line and are left exactly as they are.
        signed_in = User.objects.create_user(email="a@t.ph", username="a", password="x")
        others = [
            self.line(same_words, actor=signed_in),
            self.line(same_words, action=ActivityLog.CREATED),
            self.line(same_words, category=ActivityLog.USER),
            self.line(same_words, entity_id=5),
            self.line("Changed own password"),
            self.line(lockout.ADDRESS_LOCK_LABEL),
        ]
        before = [self.label(pk) for pk in others]
        self.scrub(apps, None)
        self.assertEqual([self.label(pk) for pk in others], before)

    def test_running_it_twice_changes_nothing_more(self):
        pk = self.line("Login locked for hunter2 after repeated failures")
        self.scrub(apps, None)
        once = self.label(pk)
        self.scrub(apps, None)
        self.assertEqual(self.label(pk), once)
