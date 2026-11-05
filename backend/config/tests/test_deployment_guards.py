"""Guards against deployments that look healthy and are not.

All three of these were real. A demo deployment on 27 Aug 2026 came up green
and was quietly broken in three separate ways, none of which was visible from
outside:

* `DATABASE_URL` was never set, so Django fell through to the zero-config
  SQLite path. `seed_initial_data` then seeded a fresh administrator into a
  file on the container's disk — which Render destroys on every deploy and
  every wake from sleep. `/healthz/` reported `{"database": "ok"}` throughout,
  because SQLite *is* a working database.
* `CORS_ALLOWED_ORIGINS` was never set, so the browser blocked every request
  while `curl` worked perfectly.

The failures share a shape: the service reports healthy, and the application is
broken. These guards make each one say so.
"""
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from config.guards import checked_site_url, cors_is_unconfigured, sqlite_fallback_is_a_mistake


class SqliteFallbackTest(SimpleTestCase):
    def test_a_deployment_without_a_database_url_is_a_mistake(self):
        self.assertTrue(sqlite_fallback_is_a_mistake(
            debug=False, database_url="", db_engine="sqlite"))

    def test_local_development_is_fine(self):
        # SQLite with DEBUG=True is the zero-config path, and it is correct.
        self.assertFalse(sqlite_fallback_is_a_mistake(
            debug=True, database_url="", db_engine="sqlite"))

    def test_a_database_url_is_fine(self):
        self.assertFalse(sqlite_fallback_is_a_mistake(
            debug=False, database_url="postgresql://host/db", db_engine="sqlite"))

    def test_an_explicit_postgres_engine_is_fine(self):
        # The on-premises path predating V3 sets DB_ENGINE rather than a URL.
        self.assertFalse(sqlite_fallback_is_a_mistake(
            debug=False, database_url="", db_engine="postgres"))

    def test_whitespace_is_not_a_database_url(self):
        self.assertTrue(sqlite_fallback_is_a_mistake(
            debug=False, database_url="   ", db_engine="sqlite"))


class CorsGuardTest(SimpleTestCase):
    LOCAL = ["http://localhost:5173", "http://127.0.0.1:5173"]

    def test_a_deployment_still_on_the_localhost_default_is_unconfigured(self):
        self.assertTrue(cors_is_unconfigured(debug=False, origins=self.LOCAL))

    def test_an_empty_list_is_unconfigured(self):
        self.assertTrue(cors_is_unconfigured(debug=False, origins=[]))

    def test_a_real_origin_is_configured(self):
        self.assertFalse(cors_is_unconfigured(
            debug=False, origins=["https://nacc-v3-demo-web.onrender.com"]))

    def test_local_development_is_fine(self):
        self.assertFalse(cors_is_unconfigured(debug=True, origins=self.LOCAL))

    def test_a_real_origin_alongside_the_local_ones_is_configured(self):
        self.assertFalse(cors_is_unconfigured(
            debug=False, origins=self.LOCAL + ["https://demo.example.gov.ph"]))


class SiteUrlGuardTest(SimpleTestCase):
    """SITE_URL goes into the button of every email, on somebody else's
    machine. It must be a bare site, and a refusal must say why without ever
    repeating what was typed: a password in the value would otherwise be
    written to the API log."""

    def test_nothing_set_is_nothing_wrong(self):
        for raw in ("", "   ", None):
            with self.subTest(raw=raw):
                self.assertEqual(("", ""), checked_site_url(raw, debug=False))

    def test_an_https_address_is_accepted(self):
        self.assertEqual(("https://nacc.example.gov.ph", ""),
                         checked_site_url("https://nacc.example.gov.ph", debug=False))

    def test_a_port_and_an_address_with_digits_are_accepted(self):
        self.assertEqual(("https://192.168.1.20:8443", ""),
                         checked_site_url("https://192.168.1.20:8443", debug=False))
        self.assertEqual(("https://[::1]:5173", ""),
                         checked_site_url("https://[::1]:5173", debug=True))

    def test_http_is_accepted_only_while_debugging(self):
        self.assertEqual(("http://192.168.1.20:5173", ""),
                         checked_site_url("http://192.168.1.20:5173", debug=True))
        url, reason = checked_site_url("http://192.168.1.20:5173", debug=False)
        self.assertEqual("", url)
        self.assertIn("https://", reason)

    def test_a_trailing_slash_is_dropped_and_so_is_the_space_around_it(self):
        for raw in ("https://nacc.example/", "  https://nacc.example/  ",
                    "https://nacc.example"):
            with self.subTest(raw=raw):
                self.assertEqual(("https://nacc.example", ""),
                                 checked_site_url(raw, debug=False))

    def test_the_scheme_and_host_are_written_in_lower_case(self):
        self.assertEqual(("https://nacc.example.gov.ph", ""),
                         checked_site_url("HTTPS://NACC.Example.GOV.ph/", debug=False))

    def test_every_other_shape_is_refused_with_a_reason(self):
        refused = {
            "a path": "https://nacc.example/children",
            "a deep path": "https://nacc.example/a/b/c/",
            "a query": "https://nacc.example/?next=/evil",
            "an empty query": "https://nacc.example?",
            "a fragment": "https://nacc.example/#top",
            "an empty fragment": "https://nacc.example#",
            "a user name": "https://colleague@elsewhere.example",
            "an empty user name": "https://@nacc.example",
            "a user name and password": "https://admin:hunter2@nacc.example",
            "ftp": "ftp://nacc.example",
            "javascript": "javascript:alert(1)",
            "no scheme": "nacc.example.gov.ph",
            "a protocol-relative address": "//nacc.example",
            "no host": "https://",
            "no host but a path": "https:///children",
            "a port that is not a number": "https://nacc.example:abc",
            "a port out of range": "https://nacc.example:99999",
            "an unclosed address": "https://[::1",
            "words": "not a url",
            "a space in the host": "https://nacc example.gov.ph",
            "an underscore in the host": "https://nacc_sys.example",
            "a host outside ASCII": "https://b\u00fccher.example",
            "a zone on an IPv6 address": "https://[fe80::1%25eth0]",
            "text after the zone": "https://[::1%x y]",
            "a quote after the zone": 'https://[::1%"onmouseover="x]',
            "a control character after the zone": "https://[::1%\x00]",
            "a right-to-left override after the zone": "https://[::1%\u202e]",
        }
        for why, raw in refused.items():
            with self.subTest(why):
                url, reason = checked_site_url(raw, debug=True)
                self.assertEqual("", url)
                self.assertTrue(reason.strip(), "a refusal has to say why")
                self.assertTrue(reason.endswith("."), "a sentence")

    def test_a_plain_ipv6_address_is_still_accepted(self):
        """The zone is what is refused, not the address. A browser cannot open
        a zone, and `ipaddress` takes almost any text after one."""
        for raw in ("https://[::1]", "https://[2001:db8::1]:8443",
                    "https://[2001:DB8::1]"):
            with self.subTest(raw=raw):
                url, reason = checked_site_url(raw, debug=False)
                self.assertEqual("", reason)
                self.assertTrue(url.startswith("https://[2001:db8::1]")
                                or url == "https://[::1]", url)

    def test_the_reason_never_repeats_what_was_typed(self):
        """The guard is the thing between a mistyped credential and the log."""
        for raw in ("https://admin:hunter2@nacc.example", "https://hunter2@nacc.example/",
                    "https://nacc.example/?token=hunter2", "https://nacc.example/hunter2",
                    "ftp://hunter2.example", "hunter2", "https://hunter2",
                    "http://hunter2.example", "https://hunter2.example:hunter2",
                    "https://hunter2 .example"):
            with self.subTest(raw=raw):
                for debug in (True, False):
                    url, reason = checked_site_url(raw, debug=debug)
                    if not reason:                       # a bare host passes: nothing to say
                        self.assertTrue(url, raw)
                    self.assertNotIn("hunter2", reason)
                    self.assertNotIn("admin", reason)

    def test_the_first_thing_wrong_is_what_is_reported(self):
        """A user name AND a path: the user name is the dangerous one."""
        _, reason = checked_site_url("https://admin@nacc.example/children", debug=False)
        self.assertIn("user name", reason)


class SiteUrlSettingsTest(SimpleTestCase):
    """settings.py reads SITE_URL through the guard. Settings load once at
    import, so each case is a fresh interpreter importing them."""

    BACKEND = Path(__file__).resolve().parents[2]

    def _load(self, site_url, **env):
        code = ("import sys; sys.path.insert(0, %r); import config.settings as s; "
                "print(repr(s.SITE_URL))" % str(self.BACKEND))
        full = {**os.environ, "SITE_URL": site_url, "DJANGO_DEBUG": "True"}
        full.update(env)
        done = subprocess.run([sys.executable, "-c", code], env=full, capture_output=True,
                              text=True, timeout=120, cwd=str(self.BACKEND))
        self.assertEqual(0, done.returncode, done.stderr)
        return done.stdout.strip(), done.stderr

    def test_a_good_value_is_used_and_nothing_is_said(self):
        shown, noise = self._load("https://nacc.example.gov.ph/")
        self.assertEqual("'https://nacc.example.gov.ph'", shown)
        self.assertNotIn("SITE_URL", noise)

    def test_unset_is_empty_and_nothing_is_said(self):
        shown, noise = self._load("")
        self.assertEqual("''", shown)
        self.assertNotIn("SITE_URL", noise)

    def test_a_bad_value_is_ignored_with_a_warning_and_the_boot_goes_on(self):
        shown, noise = self._load("https://admin:hunter2@nacc.example/children")
        self.assertEqual("''", shown)
        self.assertIn("SITE_URL is ignored", noise)
        self.assertIn("user name", noise)
        self.assertNotIn("hunter2", noise)
        self.assertNotIn("admin", noise)

    def test_http_is_ignored_on_a_deployed_copy(self):
        shown, noise = self._load(
            "http://192.168.1.20:5173", DJANGO_DEBUG="False", DJANGO_SECRET_KEY="a-test-key",
            DATABASE_URL="postgresql://u:p@127.0.0.1:5432/db",
            CORS_ALLOWED_ORIGINS="https://nacc.example.gov.ph")
        self.assertEqual("''", shown)
        self.assertIn("SITE_URL is ignored", noise)
        self.assertIn("https://", noise)

    def test_https_is_used_on_a_deployed_copy(self):
        shown, _ = self._load(
            "https://nacc.example.gov.ph", DJANGO_DEBUG="False", DJANGO_SECRET_KEY="a-test-key",
            DATABASE_URL="postgresql://u:p@127.0.0.1:5432/db",
            CORS_ALLOWED_ORIGINS="https://nacc.example.gov.ph")
        self.assertEqual("'https://nacc.example.gov.ph'", shown)

    def test_the_test_run_never_reads_it_from_a_developers_environment(self):
        """A SITE_URL set in backend/.env for a copy others reach would
        otherwise win over the CORS list every email test pins. Under
        `manage.py test` the setting is empty whatever the environment says."""
        self.assertEqual("", settings.SITE_URL)


class HealthzTest(TestCase):
    """`{"database": "ok"}` did not distinguish Neon from an ephemeral SQLite
    file. It has to name what it reached."""

    def test_reports_ok(self):
        res = self.client.get("/healthz/")
        self.assertEqual(200, res.status_code)
        self.assertEqual("ok", res.json()["status"])

    def test_names_the_database_engine(self):
        """It must name the engine it ACTUALLY reached, whichever that is.

        Asserting "sqlite" here passed on a developer's machine and failed on
        every CI run from 27 Aug 2026, because CI runs PostgreSQL on purpose —
        the whole point being that queries which pass on SQLite can still fail
        on the engine production uses. A test that only holds on one engine
        cannot check a field whose job is to tell the two apart.
        """
        body = self.client.get("/healthz/").json()
        self.assertIn("engine", body)
        expected = settings.DATABASES["default"]["ENGINE"].rsplit(".", 1)[-1]
        self.assertEqual(expected, body["engine"])
        self.assertNotEqual("unknown", body["engine"])

    def test_names_the_host_so_the_wrong_database_is_visible(self):
        body = self.client.get("/healthz/").json()
        self.assertIn("host", body)

    def test_never_leaks_a_password(self):
        # The host is deployment detail; the credential is not.
        raw = self.client.get("/healthz/").content.decode().lower()
        for secret in ("password", "@", "://"):
            self.assertNotIn(secret, raw)


class BuildEnvironmentTest(SimpleTestCase):
    """The Dockerfile must still be able to import settings.

    The boot guard broke the image build the first time it shipped:
    `collectstatic` runs with DJANGO_DEBUG=False and no DATABASE_URL, and
    importing settings runs the guard whether or not the command needs a
    database. The test suite could not catch it — it is a build environment,
    not a test one — so the Dockerfile's own values are asserted here.
    """

    def test_the_dockerfile_supplies_every_value_the_guards_demand(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(
            encoding="utf-8")
        # The RUN block itself, not the comments above it — "collectstatic"
        # appears in the explanatory comment first.
        start = text.index("RUN DJANGO_DEBUG=False")
        build_step = text[start:text.index("--noinput", start)]
        for needed in ("DJANGO_SECRET_KEY=", "DATABASE_URL=",
                       "CORS_ALLOWED_ORIGINS="):
            with self.subTest(variable=needed):
                self.assertIn(needed, build_step,
                              f"{needed} missing from the collectstatic build "
                              "step; the image build will fail on the boot guard")

    def test_the_dockerfile_values_would_not_trip_the_guards(self):
        self.assertFalse(sqlite_fallback_is_a_mistake(
            debug=False,
            database_url="postgresql://build:build@127.0.0.1:5432/build-time-only",
            db_engine="sqlite"))
        self.assertFalse(cors_is_unconfigured(
            debug=False, origins=["https://build-time-only.invalid"]))
