"""Where an email's link points (children/notifications.py `sign_in_url`,
SITE_URL; 23 Oct 2026).

Two things are held. The address comes from configuration: `SITE_URL` when it
is set, otherwise the first deployed CORS origin exactly as before. And it comes
from NOWHERE ELSE. A link built from the request's Host header is the classic
password-reset poisoning hole - send the server a forged Host and it mails a
colleague a link to your site, in a message that really does come from the
agency - so the second half is tested by sending the forgery through a real
endpoint and reading what would have gone to Brevo.

The guard that decides what a SITE_URL may look like is tested with the other
deployment guards (config/tests/test_deployment_guards.py).
"""
import inspect
import json

from django.test import SimpleTestCase, override_settings

from accounts.models import User
from accounts.tests.test_email_notifications import ORIGIN, MailBase
from children import notifications
from children.notifications import build_temporary_password_payload, sign_in_url

SITE = "https://nacc.example.gov.ph"
EVIL = "evil.example"


class SignInUrlTest(SimpleTestCase):
    @override_settings(SITE_URL=SITE, CORS_ALLOWED_ORIGINS=[ORIGIN])
    def test_the_setting_wins_over_the_cors_list(self):
        self.assertEqual(SITE, sign_in_url())

    @override_settings(SITE_URL=SITE, CORS_ALLOWED_ORIGINS=[])
    def test_the_setting_is_enough_with_no_cors_list_at_all(self):
        self.assertEqual(SITE, sign_in_url())

    @override_settings(SITE_URL=SITE, CORS_ALLOWED_ORIGINS=["http://localhost:5173"])
    def test_the_setting_is_what_a_local_copy_reached_from_elsewhere_needs(self):
        """The case it exists for: the CORS list there is localhost only."""
        self.assertEqual(SITE, sign_in_url())

    def test_unset_the_old_rule_stands_unchanged(self):
        cases = [
            (["http://localhost:5173", ORIGIN], ORIGIN),             # deployed beats localhost
            ([ORIGIN, "http://localhost:5173"], ORIGIN),
            (["http://127.0.0.1:5173", ORIGIN + "/"], ORIGIN),       # slash dropped
            (["http://localhost:5173", "http://127.0.0.1:5173"], "http://localhost:5173"),
            ([f"  {ORIGIN}  "], ORIGIN),
            (["", "  ", ORIGIN, "https://second.example"], ORIGIN),  # the first deployed one
            ([], ""),
            (["", "   "], ""),
        ]
        for origins, expected in cases:
            with self.subTest(origins=origins), override_settings(
                    SITE_URL="", CORS_ALLOWED_ORIGINS=origins):
                self.assertEqual(expected, sign_in_url())
                self.assertEqual(expected, notifications.origin_from_cors())

    @override_settings(SITE_URL=SITE, CORS_ALLOWED_ORIGINS=[ORIGIN])
    def test_the_old_rule_is_still_there_to_ask_whatever_the_setting_says(self):
        """The calendar UIDs sent before the UID was stored are built from it."""
        self.assertEqual(ORIGIN, notifications.origin_from_cors())

    def test_it_takes_nothing_from_a_request(self):
        self.assertEqual([], list(inspect.signature(sign_in_url).parameters))

    @override_settings(SITE_URL=SITE, CORS_ALLOWED_ORIGINS=[ORIGIN])
    def test_the_temporary_password_email_follows_the_setting_too(self):
        person = User(email="new@t.ph", username="new@t.ph", first_name="Ana", last_name="Lim")
        payload = build_temporary_password_payload(person, "TempPass9")
        self.assertIn(f'href="{SITE}"', payload["htmlContent"])
        self.assertIn(SITE, payload["textContent"])
        self.assertNotIn(ORIGIN, payload["htmlContent"] + payload["textContent"])


class ForgedHostTest(MailBase):
    """A request that says it was sent to another host changes no link in any
    email. Driven through the real endpoints, with ALLOWED_HOSTS wide open: the
    worst case, a deployment whose host list lets the forgery through."""

    def _forged(self, client, method, path, body):
        return getattr(client, method)(
            path, body, format="json", HTTP_HOST=EVIL, HTTP_X_FORWARDED_HOST=EVIL,
            HTTP_ORIGIN=f"https://{EVIL}", HTTP_REFERER=f"https://{EVIL}/")

    def _links(self, payload):
        return payload["htmlContent"] + payload["textContent"]

    def _assert_only_the_configured_address(self, sent, expected):
        self.assertTrue(sent, "nothing was sent, so nothing was checked")
        for kind, payload in sent:
            everything = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn(EVIL, everything, f"{kind} carries the forged host")
            self.assertIn(f'href="{expected}', payload["htmlContent"], kind)
            self.assertIn(f"Or open NACC SYS at {expected}\n", payload["textContent"], kind)
            self.assertIn(f"{expected}/profile", payload["textContent"], kind)

    @override_settings(ALLOWED_HOSTS=["*"], SITE_URL=SITE)
    def test_a_case_request_sent_to_a_forged_host_links_to_the_configured_site(self):
        with self.outbox() as sent:
            res = self._forged(self._as(self.sw), "patch", f"/api/children/{self.child.id}/",
                               {"psychologist": self.psy.id})
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["case_request"], [k for k, _ in sent])
        self._assert_only_the_configured_address(sent, SITE)
        self.assertIn(f'href="{SITE}/"', sent[0][1]["htmlContent"])

    @override_settings(ALLOWED_HOSTS=["*"], SITE_URL="")
    def test_with_no_setting_the_cors_list_is_still_the_only_source(self):
        with self.outbox() as sent:
            res = self._forged(self._as(self.sw), "patch", f"/api/children/{self.child.id}/",
                               {"psychologist": self.psy.id})
        self.assertEqual(200, res.status_code, res.data)
        self._assert_only_the_configured_address(sent, ORIGIN)

    @override_settings(ALLOWED_HOSTS=["*"], SITE_URL=SITE)
    def test_a_transfer_sent_to_a_forged_host_links_to_the_configured_site(self):
        with self.outbox() as sent:
            res = self._forged(self._as(self.admin), "post", "/api/children/transfer/", {
                "from_social_worker": self.sw.pk, "to_social_worker": self.other_sw.pk,
                "children": [self.child.pk]})
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["records_transferred"], [k for k, _ in sent])
        self._assert_only_the_configured_address(sent, SITE)
        self.assertIn(f'href="{SITE}/children"', sent[0][1]["htmlContent"])

    def test_a_host_that_is_not_allowed_is_refused_before_anything_is_mailed(self):
        """The first layer: with the host list as it is, the forgery never
        reaches the code that builds a link."""
        with self.outbox() as sent, self.assertLogs("django.security.DisallowedHost", "ERROR"):
            res = self._forged(self._as(self.sw), "patch", f"/api/children/{self.child.id}/",
                               {"psychologist": self.psy.id})
        self.assertEqual(400, res.status_code)
        self.assertEqual([], sent)
