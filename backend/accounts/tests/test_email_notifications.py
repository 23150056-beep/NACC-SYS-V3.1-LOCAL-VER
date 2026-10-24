"""Email notifications for psychologists and social workers (owner's request,
21 Oct 2026; accounts/email_notifications.py).

Three things have to stay true, and the tests are arranged around them:

* WHO is emailed is decided in one place (`wants`), and nobody is emailed about
  what they did themselves.
* WHAT leaves the building carries no child's name, ever. The last class here
  runs every trigger against a child with a name nobody else would have and
  searches everything that was handed to the sender for it.
* WHEN it leaves: after the commit, never with no mail service, never raising.

A key in the catalogue is stored in opt-out rows, so the list is pinned: a
renamed key would silently switch everybody's opt-out back on.
"""
import base64
import json
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timezone as dt_timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts import email_notifications
from accounts.display import NAME_LIMIT, entered_name, one_line
from accounts.email_notifications import (
    _AMBER_TEXT, _AMBER_TINT, FOOTER, KIND, KINDS, SAFETY, _queue, case_answered,
    case_request, case_taken_over, kinds_for, notify, records_transferred, render,
    request_withdrawn, wants, when)
from accounts.models import EmailOptOut, Role
from children import notifications
from children.models import AssignmentRequest, Child
from children.tests.payloads import complete, planted_text

User = get_user_model()

ORIGIN = "https://nacc.example.test"
# Wednesday 21 Oct 2026, 10:00 in Manila. Pinned, so a test never depends on
# the day it is run.
NOW = datetime(2026, 10, 21, 2, 0, tzinfo=dt_timezone.utc)
PREFERENCES = "/api/auth/me/email-notifications/"
KEYS = ["case_request", "request_withdrawn", "my_calendar",
        "case_accepted", "case_declined", "records_transferred",
        "case_taken_over", "child_sessions"]


class MailBase(TestCase):
    """Two social workers, two psychologists and the ISA, each with an
    address, and `outbox()` to read what would have gone to Brevo."""

    def setUp(self):
        roles = self.roles = {r: Role.objects.create(role_name=r)
                              for r in (Role.STAFF, Role.PSYCHOLOGIST, Role.ADMINISTRATOR)}
        make = lambda email, first, last, role: User.objects.create_user(  # noqa: E731
            email=email, username=email.split("@")[0], password="pass12345",
            first_name=first, last_name=last, role=roles[role])
        self.sw = make("sw@t.ph", "Editha", "Pascua", Role.STAFF)
        self.other_sw = make("sw2@t.ph", "Rosa", "Santos", Role.STAFF)
        self.admin = make("admin@t.ph", "Ada", "Admin", Role.ADMINISTRATOR)
        self.psy = make("psy@t.ph", "Marivic", "Bulan", Role.PSYCHOLOGIST)
        self.psy2 = make("psy2@t.ph", "Jose", "Rizal", Role.PSYCHOLOGIST)
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.born = date(2016, 5, 4)
        self.child = Child.objects.create(
            first_name="Ana", last_name="Cruz", fullname="Ana Cruz",
            birth_date=self.born, case_type="Foster Care", case_category="Neglected",
            social_worker=self.sw)

    def _as(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def _accepted(self, child, psychologist, **fields):
        """An accepted request as accepting leaves it: the child is with the
        psychologist. An acceptance of a child who is with somebody else has
        been superseded and is not mailed (email_notifications._superseded)."""
        child.assigned_psychologist = psychologist
        child.save(update_fields=["assigned_psychologist"])
        return AssignmentRequest.objects.create(
            child=child, psychologist=psychologist, requested_by=self.sw,
            status=AssignmentRequest.ACCEPTED, **fields)

    @contextmanager
    def outbox(self):
        """A mail service is set up, and everything handed to the sender is
        collected as (kind, payload). The callbacks that wait for the commit
        run on the way out, as they would after a real save."""
        sent = []
        with override_settings(BREVO_API_KEY="test-key", CORS_ALLOWED_ORIGINS=[ORIGIN]), \
                patch("accounts.email_notifications._queue",
                      side_effect=lambda payload, description: sent.append(
                          (description.removesuffix(" email"), payload))), \
                self.captureOnCommitCallbacks(execute=True):
            yield sent

    def to(self, sent, user):
        return [(kind, payload) for kind, payload in sent
                if payload["to"][0]["email"] == user.email]

    def kinds_to(self, sent, user):
        return [kind for kind, _ in self.to(sent, user)]

    def _ask(self, psychologist, by=None, child=None):
        child = child or self.child
        res = self._as(by or self.sw).patch(
            f"/api/children/{child.id}/", {"psychologist": psychologist.id}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        return AssignmentRequest.objects.filter(child=child).first()

    def _answer(self, req, verb, user=None, **body):
        return self._as(user or self.psy).post(
            f"/api/assignment-requests/{req.id}/{verb}/", body, format="json")

    def _held(self, worker, first="Ben", last="Lim", **fields):
        return Child.objects.create(
            first_name=first, last_name=last, fullname=f"{first} {last}",
            birth_date=self.born, social_worker=worker, case_type="Foster Care",
            case_category="Neglected", **fields)

    def _ref(self, child):
        return f"C-{child.pk:04d}"


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

class CatalogueTest(SimpleTestCase):
    def test_the_keys_and_their_order_never_change(self):
        """The key is what an opt-out row stores. Rename one and everybody who
        switched that email off is quietly emailed again."""
        self.assertEqual(KEYS, [k.key for k in KINDS])

    def test_every_kind_belongs_to_one_of_the_two_roles_and_says_what_it_is(self):
        for kind in KINDS:
            self.assertIn(kind.role, (Role.PSYCHOLOGIST, Role.STAFF), kind.key)
            self.assertTrue(kind.label.strip(), kind.key)
            self.assertTrue(kind.description.strip(), kind.key)

    def test_the_psychologists_come_first_then_the_social_workers(self):
        self.assertEqual([Role.PSYCHOLOGIST] * 3 + [Role.STAFF] * 5, [k.role for k in KINDS])

    def test_the_lookup_has_every_kind_once(self):
        self.assertEqual(KEYS, list(KIND))
        self.assertIs(KINDS[0], KIND["case_request"])

    def test_the_calendar_switch_warns_that_it_also_stops_updates(self):
        text = KIND["my_calendar"].description
        self.assertIn("calendar invite", text)
        self.assertIn("also stops updates to sessions already added", text)


class KindsForTest(MailBase):
    def test_each_role_sees_only_its_own_in_catalogue_order(self):
        self.assertEqual(KEYS[:3], [k.key for k in kinds_for(self.psy)])
        self.assertEqual(KEYS[3:], [k.key for k in kinds_for(self.sw)])

    def test_the_isa_and_nobody_get_none(self):
        self.assertEqual([], kinds_for(self.admin))
        self.assertEqual([], kinds_for(None))
        self.assertEqual([], kinds_for(User.objects.create_user(
            email="norole@t.ph", username="norole", password="x")))


class WantsTest(MailBase):
    def test_an_active_account_in_the_role_with_an_address_does(self):
        self.assertTrue(wants(self.psy, "case_request"))
        self.assertTrue(wants(self.sw, "case_accepted"))

    def test_nobody_does(self):
        self.assertFalse(wants(None, "case_request"))

    def test_an_account_that_cannot_sign_in_does_not(self):
        self.psy.status = User.ARCHIVED
        self.psy.save()
        self.assertFalse(self.psy.is_active)
        self.assertFalse(wants(self.psy, "case_request"))

    def test_an_account_with_no_address_does_not(self):
        User.objects.filter(pk=self.psy.pk).update(email="")
        self.psy.refresh_from_db()
        self.assertFalse(wants(self.psy, "case_request"))

    def test_another_roles_email_is_not_theirs(self):
        self.assertFalse(wants(self.sw, "case_request"))
        self.assertFalse(wants(self.psy, "case_accepted"))
        self.assertFalse(wants(self.admin, "case_request"))
        self.assertFalse(wants(self.admin, "case_accepted"))

    def test_switching_it_off_stops_that_one_and_no_other(self):
        EmailOptOut.objects.create(user=self.psy, kind="case_request")
        self.assertFalse(wants(self.psy, "case_request"))
        self.assertTrue(wants(self.psy, "request_withdrawn"))
        self.assertTrue(wants(self.psy2, "case_request"))

    def test_an_unknown_key_is_a_bug_in_the_caller_not_a_quiet_no(self):
        with self.assertRaises(KeyError):
            wants(self.psy, "case_requset")


# --------------------------------------------------------------------------
# The message
# --------------------------------------------------------------------------

class WhenTest(SimpleTestCase):
    """12-hour, local time (Manila), built by hand: %-d raises on Windows."""

    def test_a_morning_in_local_time(self):
        self.assertEqual("Wed 21 Oct 2026, 9:30 AM",
                         when(datetime(2026, 10, 21, 1, 30, tzinfo=dt_timezone.utc)))

    def test_a_single_digit_day_has_no_zero_and_noon_is_12_pm(self):
        self.assertEqual("Mon 5 Oct 2026, 12:05 PM",
                         when(datetime(2026, 10, 5, 4, 5, tzinfo=dt_timezone.utc)))

    def test_local_midnight_is_12_am_of_the_next_day(self):
        self.assertEqual("Tue 6 Oct 2026, 12:00 AM",
                         when(datetime(2026, 10, 5, 16, 0, tzinfo=dt_timezone.utc)))

    def test_an_evening_hour(self):
        self.assertEqual("Wed 21 Oct 2026, 5:45 PM",
                         when(datetime(2026, 10, 21, 9, 45, tzinfo=dt_timezone.utc)))


@override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:5173", ORIGIN])
class RenderTest(SimpleTestCase):
    def test_a_branded_page_and_a_text_part(self):
        page, text = render("Heading", ["First.", "Second."], facts=[("Case", "C-0007")],
                            button=("Open it", "/schedule"))
        self.assertIn("NACC &ndash; RACCO 1", page)
        self.assertIn("National Authority for Child Care", page)
        self.assertNotIn("<style", page)            # Gmail strips it
        for part in (page, text):
            for needle in ("Heading", "First.", "Second.", "C-0007", "Open it"):
                self.assertIn(needle, part)
        self.assertIn("Case: C-0007", text)

    def test_the_button_points_at_the_deployed_origin_plus_the_path(self):
        page, text = render("H", ["P"], button=("Open it", "/report/child/42"))
        self.assertIn(f'href="{ORIGIN}/report/child/42"', page)
        self.assertNotIn("localhost", page + text)
        self.assertIn(f"Open it: {ORIGIN}/report/child/42", text)

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_no_address_known_means_no_button_rather_than_a_dead_one(self):
        page, text = render("H", ["P"], button=("Open it", "/"))
        self.assertNotIn("<a href", page)
        self.assertNotIn("Open it", page + text)

    def test_no_button_asked_for_means_none(self):
        page, _ = render("H", ["P"])
        self.assertNotIn("<a href", page)

    def test_the_footer_is_in_both_parts(self):
        page, text = render("H", ["P"])
        self.assertIn("You get this email because of your notification settings in NACC SYS "
                      "(My Profile, Email notifications). No case details are included. "
                      "Automated message; replies are not monitored.", FOOTER)
        self.assertIn(FOOTER, page)
        self.assertIn(FOOTER, text)
        # The way to the switches follows it, as text, in both parts.
        profile = f"Choose which emails you get: My Profile, Email notifications - {ORIGIN}/profile"
        self.assertIn(profile, page)
        self.assertTrue(text.endswith(f"{FOOTER}\n{profile}"))

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_with_no_address_the_footer_is_just_the_footer(self):
        page, text = render("H", ["P"])
        self.assertTrue(text.endswith(FOOTER))
        self.assertNotIn("Choose which emails", page + text)

    def test_everything_interpolated_is_escaped_in_the_page_and_raw_in_the_text(self):
        page, text = render('<b>Hi</b>', ["a & b <script>x</script>"],
                            facts=[("<i>Case</i>", "<u>C-1</u>")],
                            button=('Go "now"', "/x?a=1&b=2"))
        for raw in ("<b>Hi</b>", "<script>", "<i>Case</i>", "<u>C-1</u>", 'Go "now"'):
            self.assertNotIn(raw, page)
        self.assertIn("a &amp; b &lt;script&gt;", page)
        self.assertIn("/x?a=1&amp;b=2", page)
        self.assertIn("a & b <script>x</script>", text)


@override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN])
class EveryEmailTest(SimpleTestCase):
    """What the shared layout adds to every notification (23 Oct 2026): the
    preview line, the plain address under the button, the safety line and the
    way to the switches. The first two of those are text on purpose - a mail
    service rewrites links for click tracking, so a link cannot show the
    address it really goes to."""

    SAFETY_WORDS = ("NACC SYS never asks for your password or a code by email, and nothing "
                    "in this email signs you in. Not sure it is from us? Do not use the "
                    "button: open NACC SYS from your bookmark or the address you already "
                    "know, and tell the ISA.")

    def _page(self, **over):
        args = dict(button=("Open it", "/schedule"))
        args.update(over)
        return render("Heading", ["First paragraph.", "Second."], **args)

    def test_the_hidden_preview_is_the_first_thing_in_the_body(self):
        page, _ = self._page(preview="Case C-0007 is waiting for you.")
        body = page[page.index("<body"):]
        first = body[body.index(">") + 1:]
        self.assertTrue(first.startswith(
            '<div style="display:none;font-size:1px;line-height:1px;max-height:0;'
            'max-width:0;opacity:0;overflow:hidden;mso-hide:all;color:#f4f6fa;">'
            "Case C-0007 is waiting for you."), first[:300])
        # Padded, so the mail list does not run on into the body text.
        self.assertIn("&zwnj;&nbsp;" * 20, first)
        hidden = first[:first.index("</div>")]
        self.assertNotIn("Heading", hidden)

    def test_without_a_preview_it_is_the_first_paragraph(self):
        page, _ = self._page()
        hidden = page[page.index("color:#f4f6fa;"):page.index("</div>")]
        self.assertIn("First paragraph.", hidden)
        self.assertNotIn("Second.", hidden)

    def test_the_preview_is_escaped_and_an_empty_one_leaves_no_hidden_line(self):
        page, _ = self._page(preview="a & <b>")
        self.assertIn("a &amp; &lt;b&gt;", page)
        bare, _ = render("Heading", [])
        self.assertNotIn("display:none", bare)

    def test_the_page_says_its_language_and_is_titled_by_the_subject(self):
        page, _ = self._page(subject="The subject & more")
        self.assertIn('<html lang="en">', page)
        self.assertIn("<title>The subject &amp; more</title>", page)
        untitled, _ = self._page()
        self.assertIn("<title>Heading</title>", untitled)

    def test_notify_titles_the_page_with_the_subject(self):
        with override_settings(BREVO_API_KEY="k"), \
                patch("accounts.email_notifications._queue") as queue:
            psy = Mock(is_active=True, email="p@t.ph", fullname="P", role=Mock(
                role_name=Role.PSYCHOLOGIST), pk=1)
            with patch("accounts.email_notifications.wants", return_value=True):
                notify("case_request", psy, subject="The subject", heading="The heading",
                       paragraphs=["Body."], preview="Hidden line.")
        payload = queue.call_args.args[0]
        self.assertIn("<title>The subject</title>", payload["htmlContent"])
        self.assertIn("Hidden line.", payload["htmlContent"])

    def test_the_site_is_printed_in_words_under_the_button_and_is_not_a_link(self):
        page, text = self._page()
        self.assertIn("Or open NACC SYS at ", page)
        self.assertIn(f">{ORIGIN}</span>", page)
        # Exactly one link, the button. The address is text: no second anchor,
        # and nowhere an href that is the bare site.
        self.assertEqual(1, page.count("<a "))
        self.assertNotIn(f'href="{ORIGIN}"', page)
        self.assertIn(f'href="{ORIGIN}/schedule"', page)
        # The bare site, not the deep path.
        line = [t for t in text.split("\n") if t.startswith("Or open")][0]
        self.assertEqual(f"Or open NACC SYS at {ORIGIN}", line)

    def test_the_address_is_escaped_in_the_page(self):
        with override_settings(SITE_URL='https://nacc.example"><b>'):
            page, _ = self._page()
        self.assertNotIn("<b>", page)

    def test_the_safety_line_follows_the_address_in_both_parts(self):
        self.assertEqual(self.SAFETY_WORDS, SAFETY)
        page, text = self._page()
        self.assertIn(self.SAFETY_WORDS, page)
        self.assertIn(self.SAFETY_WORDS, text)
        for part in (page, text):
            self.assertLess(part.index("Or open NACC SYS at"), part.index("NACC SYS never asks"))

    def test_the_way_to_the_switches_is_text_with_the_address(self):
        page, text = self._page()
        line = f"Choose which emails you get: My Profile, Email notifications - {ORIGIN}/profile"
        self.assertIn(line, page)
        self.assertIn(line, text)
        self.assertNotIn('href="https://nacc.example.test/profile"', page)

    def test_the_safety_line_does_not_offer_a_check_that_a_look_alike_passes(self):
        """The first wording asked the reader to compare the address printed
        under the button with the one they know. That text is independent of
        where the button goes: a forged email can print the genuine address,
        carry the same sentence and point its button elsewhere, and so pass the
        very check the email taught. The check left is the one that does not
        use the email at all."""
        self.assertNotIn("address above", SAFETY)
        self.assertNotIn("is not the one you know", SAFETY)
        self.assertIn("Do not use the button", SAFETY)
        self.assertIn("open NACC SYS from your bookmark or the address you already know", SAFETY)
        self.assertIn("tell the ISA", SAFETY)

    def test_the_safety_line_does_not_say_where_the_button_goes(self):
        """A button called "Open the schedule" under a line saying it "only
        opens the sign-in page" read as a contradiction, and was only true in a
        tab that was signed out. What is certain is that nothing signs anyone in."""
        self.assertNotIn("sign-in page", SAFETY)
        self.assertIn("nothing in this email signs you in", SAFETY)

    def test_the_button_keeps_the_look_that_makes_it_something_to_tap(self):
        page, _ = self._page()
        anchor = page[page.index("<a "):page.index("</a>")]
        for style in ("display:inline-block", f"background:{notifications._BRAND}",
                      "color:#ffffff", "padding:13px 30px", "border-radius:8px",
                      "text-decoration:none"):
            self.assertIn(style, anchor)

    def test_the_address_is_on_its_own_line_and_breaks_only_when_it_must(self):
        """`word-break:break-all` cut a short address in the middle of a word
        on a phone ("exam" / "ple.ph"). The address now drops whole to a line
        of its own and is split only if it is longer than the card."""
        page, _ = self._page()
        span = page[page.index('<span style="display:block'):page.index(f">{ORIGIN}</span>")]
        self.assertIn("overflow-wrap:anywhere", span)
        self.assertIn("word-wrap:break-word", span)
        self.assertNotIn("break-all", page)

    def test_the_address_in_the_footer_wraps_too(self):
        """It is one unbreakable word as well, and a long host overflowed the
        footer on a narrow phone."""
        page, _ = self._page()
        start = page.index('<div style="padding-top:6px;')
        footer_line = page[start:page.index("Choose which emails")]
        self.assertIn("overflow-wrap:anywhere", footer_line)
        self.assertIn("word-wrap:break-word", footer_line)

    def test_a_time_never_parts_from_its_am_or_pm_in_the_page_only(self):
        """"Fri 23 Oct 2026, 10:00" over a lone "AM" on a phone. A no-break
        space in the page keeps them together; the text part, which a person
        copies from, keeps the ordinary space."""
        page, text = render("H", ["P"], facts=[
            ("When", "Tue 27 Oct 2026, 9:30 AM to 10:30 AM"),
            ("Was", "Mon 26 Oct 2026, 2:00 PM"),
            ("Case", "C-0042 PMS 12 AM")])
        self.assertIn(">Tue 27 Oct 2026, 9:30&nbsp;AM to 10:30&nbsp;AM</td>", page)
        self.assertIn(">Mon 26 Oct 2026, 2:00&nbsp;PM</td>", page)
        self.assertIn(">C-0042 PMS 12 AM</td>", page)          # not a time, left alone
        self.assertIn("When: Tue 27 Oct 2026, 9:30 AM to 10:30 AM", text)
        self.assertNotIn("\u00a0", text)
        self.assertNotIn("&nbsp;", text)

    def test_the_preview_line_is_hidden_in_a_client_that_drops_display_none(self):
        """The hiding recipe's other half: a collapsed box and the page's own
        background as the text colour, so the sentence is not seen there."""
        page, _ = self._page(preview="Hidden.")
        style = page[page.index("<div style=") + 12:page.index('">Hidden.')]
        for part in ("display:none", "font-size:1px", "line-height:1px", "max-height:0",
                     "max-width:0", "opacity:0", "overflow:hidden", "mso-hide:all",
                     f"color:{notifications._PAPER}"):
            self.assertIn(part, style)

    def test_a_button_path_that_does_not_begin_with_a_slash_is_refused(self):
        """The path is appended to the site's address, so "@elsewhere.example"
        would give https://site.example@elsewhere.example. Nothing passes one
        today; the guard is for the next caller."""
        for path in ("@evil.example", ".evil.example", "evil.example", "",
                     "https://evil.example/"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                render("H", ["P"], button=("Go", path))
        page, _ = render("H", ["P"], button=("Go", "/"))
        self.assertIn(f'href="{ORIGIN}/"', page)
        render("H", ["P"])                                        # no button, no path to check

    def test_such_a_path_sends_nothing_and_is_logged(self):
        psy = Mock(is_active=True, email="p@t.ph", fullname="P",
                   role=Mock(role_name=Role.PSYCHOLOGIST), pk=1)
        with override_settings(BREVO_API_KEY="k"), \
                patch("accounts.email_notifications._queue") as queue, \
                patch("accounts.email_notifications.wants", return_value=True), \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            queued = notify("case_request", psy, subject="S", heading="H",
                            paragraphs=["P"], button=("Go", "@evil.example"))
        self.assertFalse(queued)
        queue.assert_not_called()

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_with_no_address_known_none_of_it_is_printed(self):
        page, text = self._page()
        for part in (page, text):
            self.assertNotIn("Or open", part)
            self.assertNotIn("NACC SYS never asks", part)
            self.assertNotIn("Choose which emails", part)
            self.assertNotIn("Open it", part)
        self.assertNotIn("<a ", page)

    def test_with_no_button_there_is_no_address_line_or_safety_line_either(self):
        """Both talk about "the button"; the footer's way to the switches does
        not, and stays."""
        page, text = render("H", ["P"])
        for part in (page, text):
            self.assertNotIn("Or open", part)
            self.assertNotIn("NACC SYS never asks", part)
            self.assertIn("Choose which emails you get", part)

    def test_the_site_setting_wins_over_the_cors_list_in_every_link(self):
        with override_settings(SITE_URL="https://sw.example.gov.ph"):
            page, text = self._page()
        for part in (page, text):
            self.assertIn("https://sw.example.gov.ph", part)
            self.assertNotIn(ORIGIN, part)
        self.assertIn('href="https://sw.example.gov.ph/schedule"', page)

    def test_the_optional_parts_are_escaped_in_the_page_and_raw_in_the_text(self):
        page, text = render("H", ["P"], chip="<i>Now</i>", greeting="Hello <b>Ana</b>,",
                            steps=["Open <u>it</u> & go"])
        for raw in ("<i>Now</i>", "<b>Ana</b>", "<u>it</u>"):
            self.assertNotIn(raw, page)
            self.assertIn(raw, text)
        self.assertIn("&lt;u&gt;it&lt;/u&gt; &amp; go", page)

    def test_an_email_that_asks_for_none_of_the_optional_parts_has_none_of_them(self):
        page, text = self._page()
        self.assertNotIn("What to do next", page + text)
        self.assertNotIn("Hello", page + text)
        self.assertNotIn("border-radius:999px", page)         # the chip


class AmberChipTest(SimpleTestCase):
    def test_the_chip_text_clears_wcag_aa_on_its_tint(self):
        """4.5:1 is what AA asks of text this size. Worked out here from the two
        colours rather than quoted, so changing one cannot go unnoticed."""
        def luminance(hex_colour):
            r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
            lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
            return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
        light, dark = sorted((luminance(_AMBER_TEXT), luminance(_AMBER_TINT)), reverse=True)
        ratio = (light + 0.05) / (dark + 0.05)
        self.assertEqual(("#92400e", "#fef3c7"), (_AMBER_TEXT, _AMBER_TINT))
        self.assertGreaterEqual(ratio, 4.5)
        self.assertAlmostEqual(6.37, ratio, places=2)


class EnteredNameTest(SimpleTestCase):
    """A name is printed inside an email that really does come from the agency
    (23 Oct 2026, security review). Anyone can ask for an account under any
    name, so it comes back as one plain, short line."""

    def _name(self, text, fallback=""):
        return entered_name(SimpleNamespace(fullname=text), fallback)

    def test_an_ordinary_name_is_left_exactly_as_typed(self):
        for name in ("Editha Pascua", "Ma. Cristina Dela Cruz-Villanueva",
                     "PE\u00d1AMORA, Jose Jr.", "Mar\u00eda Jos\u00e9 Pe\u00f1a", "Li Wei"):
            self.assertEqual(name, self._name(name))

    def test_a_right_to_left_override_and_zero_width_characters_are_dropped(self):
        self.assertEqual("Ana Bulan", self._name("Ana \u202eBulan"))
        self.assertEqual("AnaBulan", self._name("Ana\u200b\u200dBulan\ufeff"))
        for name in (self._name("Ana\u2066 Bulan\u2069"), self._name("\u202e")):
            for ch in name:
                self.assertNotEqual("Cf", unicodedata.category(ch), repr(name))

    def test_newlines_and_other_whitespace_become_one_space(self):
        self.assertEqual("Editha Pascua Please reply",
                         self._name("Editha\r\nPascua\n\n\tPlease\u2028reply"))
        self.assertEqual("Ana Bulan", self._name("  Ana   Bulan  "))

    def test_other_control_characters_are_dropped(self):
        self.assertEqual("AnaBulan", self._name("Ana\x00\x1b[31mBulan".replace("[31m", "")))

    def test_a_long_name_is_cut_with_an_ellipsis_to_the_limit(self):
        name = self._name("Maria " * 60)
        self.assertEqual(NAME_LIMIT, len(name))
        self.assertTrue(name.endswith("\u2026"))
        self.assertFalse(name[:-1].endswith(" "))
        self.assertEqual("x" * NAME_LIMIT, one_line("x" * NAME_LIMIT))       # at the limit: whole
        self.assertEqual(NAME_LIMIT, len(one_line("x" * (NAME_LIMIT + 1))))

    def test_a_name_that_is_nothing_visible_is_no_name(self):
        for name in ("", "   ", "\u202e\u200b", "\n\r\n"):
            self.assertEqual("A colleague", self._name(name, "A colleague"), repr(name))
            self.assertEqual("", self._name(name))

    def test_the_fallback_is_used_for_a_person_with_no_name_at_all(self):
        self.assertEqual("Fallback", entered_name(None, "Fallback"))
        self.assertEqual("Fallback", entered_name(SimpleNamespace(), "Fallback"))


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------

@override_settings(BREVO_API_KEY="test-key", BREVO_SENDER_EMAIL="from@racco1.gov.ph",
                   BREVO_SENDER_NAME="NACC RACCO1", CORS_ALLOWED_ORIGINS=[ORIGIN])
class NotifyTest(MailBase):
    def _notify(self, user=None, kind="case_request", **over):
        args = dict(subject="The subject", heading="The heading", paragraphs=["Body."])
        args.update(over)
        return notify(kind, user or self.psy, **args)

    def test_the_payload_brevo_is_given(self):
        with patch("accounts.email_notifications._queue") as queue:
            ok = self._notify(facts=[("Case", "C-0007")], button=("Open it", "/x"))
        self.assertTrue(ok)
        payload, description = queue.call_args.args
        self.assertEqual("case_request email", description)
        self.assertEqual({"name": "NACC RACCO1", "email": "from@racco1.gov.ph"},
                         payload["sender"])
        self.assertEqual([{"email": "psy@t.ph", "name": "Marivic Bulan"}], payload["to"])
        self.assertEqual("The subject", payload["subject"])
        self.assertIn("C-0007", payload["htmlContent"])
        self.assertIn("Case: C-0007", payload["textContent"])
        self.assertNotIn("attachment", payload)

    def test_an_attachment_is_called_once_and_sent_as_base64(self):
        invite = Mock(return_value=("invite.ics", "BEGIN:VCALENDAR\r\nSUMMARY:Séance\r\n"))
        with patch("accounts.email_notifications._queue") as queue:
            self.assertTrue(self._notify(attachment=invite))
        invite.assert_called_once_with()
        (attached,) = queue.call_args.args[0]["attachment"]
        self.assertEqual("invite.ics", attached["name"])
        self.assertEqual("BEGIN:VCALENDAR\r\nSUMMARY:Séance\r\n",
                         base64.b64decode(attached["content"]).decode("utf-8"))

    def test_the_attachment_is_not_built_for_an_email_that_will_not_be_sent(self):
        """The calendar invite counts itself as sent inside this callable, so
        calling it for a mail that goes nowhere would number an invite nobody
        has."""
        invite = Mock(return_value=("invite.ics", "x"))
        EmailOptOut.objects.create(user=self.psy, kind="case_request")
        with patch("accounts.email_notifications._queue") as queue:
            self.assertFalse(self._notify(attachment=invite))                  # opted out
            self.assertFalse(self._notify(user=self.sw, attachment=invite))    # wrong role
            with override_settings(BREVO_API_KEY=""), self.assertLogs(
                    "accounts.email_notifications", level="WARNING"):
                self.assertFalse(self._notify(user=self.psy2, attachment=invite))  # no service
        invite.assert_not_called()
        queue.assert_not_called()

    def test_it_never_raises_because_a_save_is_in_the_middle_of_it(self):
        with patch("accounts.email_notifications._queue", side_effect=RuntimeError("boom")), \
                self.assertLogs("accounts.email_notifications", level="ERROR") as logged:
            self.assertFalse(self._notify())
        self.assertIn("case_request", "\n".join(logged.output))

    def test_a_failing_attachment_does_not_raise_and_queues_nothing(self):
        def broken():
            raise ValueError("no invite")
        with patch("accounts.email_notifications._queue") as queue, \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            self.assertFalse(self._notify(attachment=broken))
        queue.assert_not_called()

    def test_a_bad_key_does_not_raise_either(self):
        with patch("accounts.email_notifications._queue") as queue, \
                self.assertLogs("accounts.email_notifications", level="ERROR"):
            self.assertFalse(self._notify(kind="no_such_kind"))
        queue.assert_not_called()


class ConsoleModeTest(MailBase):
    """The local copy has no BREVO_API_KEY: one line in the log, and nothing
    is sent or recorded as sent."""

    @override_settings(BREVO_API_KEY="")
    def test_one_warning_naming_the_kind_and_the_subject(self):
        invite = Mock(return_value=("invite.ics", "x"))
        with patch("accounts.email_notifications._queue") as queue, \
                self.assertLogs("accounts.email_notifications", level="WARNING") as logged:
            sent = notify("case_request", self.psy, subject="A case is waiting",
                          heading="H", paragraphs=["P"], attachment=invite)
        self.assertFalse(sent)
        queue.assert_not_called()
        invite.assert_not_called()
        (line,) = logged.records
        self.assertEqual("WARNING", line.levelname)
        message = line.getMessage()
        self.assertEqual("EMAIL (console) case_request to psy@t.ph: A case is waiting", message)

    @override_settings(BREVO_API_KEY="")
    def test_nothing_is_logged_for_someone_who_would_not_have_been_mailed(self):
        EmailOptOut.objects.create(user=self.psy, kind="case_request")
        with self.assertNoLogs("accounts.email_notifications", level="WARNING"):
            self.assertFalse(notify("case_request", self.psy, subject="S",
                                    heading="H", paragraphs=["P"]))

    def test_sending_follows_the_key(self):
        with override_settings(BREVO_API_KEY=""):
            self.assertFalse(email_notifications.sending())
        with override_settings(BREVO_API_KEY="k"):
            self.assertTrue(email_notifications.sending())


class QueueTest(MailBase):
    PAYLOAD = {"subject": "S"}

    def test_it_waits_for_the_commit_then_posts_on_a_daemon_thread(self):
        with patch.object(email_notifications.threading, "Thread") as thread:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                _queue(self.PAYLOAD, "case_request email")
            thread.assert_not_called()
            self.assertEqual(1, len(callbacks))
            callbacks[0]()
        thread.assert_called_once_with(
            target=notifications._post, args=(self.PAYLOAD, "case_request email"),
            name=email_notifications._THREAD_NAME, daemon=True)
        thread.return_value.start.assert_called_once_with()

    def test_the_callback_is_robust_so_a_thread_that_cannot_start_cannot_fail_the_save(self):
        """Inside an explicit atomic block the callback runs at the commit,
        outside notify's try/except. A robust one is logged and dropped; a plain
        one raised out of the block after the save was made, answering 500 and
        skipping every callback queued behind it."""
        with self.captureOnCommitCallbacks(execute=False):
            _queue(self.PAYLOAD, "case_request email")
            (_, _, robust) = connection.run_on_commit[-1]
        self.assertIs(True, robust)

    def test_a_rolled_back_save_mails_nothing(self):
        class Rollback(Exception):
            pass
        with patch.object(email_notifications.threading, "Thread") as thread, \
                override_settings(BREVO_API_KEY="test-key"):
            with self.captureOnCommitCallbacks(execute=True):
                try:
                    with transaction.atomic():
                        self._ask(self.psy)
                        raise Rollback
                except Rollback:
                    pass
        thread.assert_not_called()

    def test_a_committed_save_mails_once_through_the_real_sender(self):
        with patch.object(email_notifications.threading, "Thread") as thread, \
                override_settings(BREVO_API_KEY="test-key", CORS_ALLOWED_ORIGINS=[ORIGIN]):
            with self.captureOnCommitCallbacks(execute=True):
                self._ask(self.psy)
        thread.assert_called_once()
        payload, description = thread.call_args.kwargs["args"]
        self.assertIs(notifications._post, thread.call_args.kwargs["target"])
        self.assertEqual("case_request email", description)
        self.assertEqual("psy@t.ph", payload["to"][0]["email"])


# --------------------------------------------------------------------------
# The wording
# --------------------------------------------------------------------------

@override_settings(BREVO_API_KEY="test-key", CORS_ALLOWED_ORIGINS=[ORIGIN])
class WordingTest(MailBase):
    def _one(self, call):
        with patch("accounts.email_notifications._queue") as queue:
            call()
        queue.assert_called_once()
        return queue.call_args.args[0]

    def test_case_request(self):
        payload = self._one(lambda: case_request(self.child, self.psy, by=self.sw))
        ref = self._ref(self.child)
        self.assertEqual("A case is waiting for your answer", payload["subject"])
        self.assertIn(f"Editha Pascua, a social worker, asked you to take case {ref}. It "
                      "joins your records only once you accept it.", payload["textContent"])
        self.assertIn(f"Review the request: {ORIGIN}/", payload["textContent"])

    def test_request_withdrawn_for_a_child_and_for_a_reference_already_written(self):
        ref = self._ref(self.child)
        for ref_or_child in (self.child, ref):
            payload = self._one(lambda: request_withdrawn(self.psy, ref_or_child, by=self.admin))
            self.assertEqual(f"Case {ref} is no longer waiting for you", payload["subject"])
            self.assertIn(f"Ada Admin withdrew the request for you to take case {ref}. "
                          "There is nothing for you to do.", payload["textContent"])
            self.assertIn(f"Open NACC SYS: {ORIGIN}/", payload["textContent"])

    def test_request_withdrawn_by_closing_the_case_names_nobody_as_the_one_who_withdrew(self):
        self.child.status = Child.INACTIVE
        self.child.save()
        ref = self._ref(self.child)
        payload = self._one(lambda: request_withdrawn(self.psy, self.child, by=self.admin))
        self.assertEqual(f"Case {ref} is no longer waiting for you", payload["subject"])
        self.assertIn(f"Case {ref} was closed, so the request for you to take it is no "
                      "longer open. There is nothing for you to do.", payload["textContent"])
        self.assertNotIn("withdrew", payload["textContent"])
        self.assertNotIn("Ada", payload["textContent"] + payload["htmlContent"])

    def test_accepted_and_declined(self):
        ref = self._ref(self.child)
        for status, verb, kind, more in (
                (AssignmentRequest.ACCEPTED, "accepted", "case_accepted",
                 "It is now in their records. No session is booked yet; you will get "
                 "an email when one is."),
                (AssignmentRequest.DECLINED, "declined", "case_declined",
                 "Sign in to read their reason and ask another psychologist.")):
            if status == AssignmentRequest.ACCEPTED:
                req = self._accepted(self.child, self.psy)
            else:
                req = AssignmentRequest.objects.create(
                    child=self.child, psychologist=self.psy, requested_by=self.sw,
                    status=status)
            with patch("accounts.email_notifications._queue") as queue:
                case_answered(req, by=self.psy)
            payload, description = queue.call_args.args
            self.assertEqual(f"{kind} email", description)
            self.assertEqual(f"Marivic Bulan {verb} case {ref}", payload["subject"])
            self.assertIn(f"Marivic Bulan {verb} case {ref}. {more}", payload["textContent"])
            self.assertIn(f"Open the record: {ORIGIN}/report/child/{self.child.pk}",
                          payload["textContent"])
            self.assertEqual("sw@t.ph", payload["to"][0]["email"])

    def test_a_request_still_waiting_or_withdrawn_is_not_an_answer(self):
        for status in (AssignmentRequest.PENDING, AssignmentRequest.WITHDRAWN):
            req = AssignmentRequest.objects.create(
                child=self.child, psychologist=self.psy, requested_by=self.sw, status=status)
            with patch("accounts.email_notifications._queue") as queue:
                case_answered(req, by=self.psy)
            queue.assert_not_called()

    def test_records_transferred_in_the_singular_and_the_plural(self):
        for count, noun in ((1, "1 record"), (7, "7 records")):
            payload = self._one(lambda: records_transferred(
                self.sw, self.other_sw, count, by=self.admin))
            self.assertEqual(f"{noun} moved to you", payload["subject"])
            self.assertIn(f"Ada Admin moved {noun} to you from Rosa Santos.",
                          payload["textContent"])
            self.assertIn(f"Open Records: {ORIGIN}/children", payload["textContent"])

    def test_case_taken_over(self):
        payload = self._one(lambda: case_taken_over(self.child, self.other_sw, by=self.sw))
        ref = self._ref(self.child)
        self.assertEqual(f"Case {ref} was reopened by Editha Pascua", payload["subject"])
        self.assertIn(f"Editha Pascua reopened case {ref}, a closed case in your records, "
                      "at intake. It is now in their records.", payload["textContent"])
        self.assertIn(f"Open NACC SYS: {ORIGIN}/", payload["textContent"])


class ReferenceGuardTest(MailBase):
    """`request_withdrawn` takes a Child or a reference already written out.
    The string goes straight into a subject and a body, so only a reference is
    taken: a name handed to it by mistake mails nothing."""

    def test_a_string_that_is_not_a_case_reference_mails_nobody_and_is_logged(self):
        for text in ("Zyxwanita Qorvath", "C-1", "C-0001 Zyxwanita", "c-0001", ""):
            with self.outbox() as sent, self.assertLogs(
                    "accounts.email_notifications", level="ERROR"):
                request_withdrawn(self.psy, text, by=self.sw)
            self.assertEqual([], sent, text)

    def test_a_reference_is_taken_as_written(self):
        for text in ("C-0001", "C-0042", "C-12345"):
            with self.outbox() as sent:
                request_withdrawn(self.psy, text, by=self.sw)
            self.assertEqual(1, len(sent), text)
            self.assertIn(text, sent[0][1]["subject"])


class NamelessStaffTest(MailBase):
    """`username` is the email address on every way an account is made
    (accounts/serializers.py, google_auth.py), and a name is optional. The test
    users above are made with a short username and always a name, which is why
    an address in a colleague's subject line was never seen: these are made the
    way production makes them."""

    def setUp(self):
        super().setUp()
        make = lambda address, role: User.objects.create_user(  # noqa: E731
            email=address, username=address, password="pass12345", role=self.roles[role])
        self.nameless_sw = make("noname@gmail.com", Role.STAFF)
        self.nameless_psy = make("nopsy@gmail.com", Role.PSYCHOLOGIST)
        self.nameless_admin = make("noadmin@gmail.com", Role.ADMINISTRATOR)
        self.assertEqual("", self.nameless_sw.fullname)

    def assertNoAddress(self, sent):
        self.assertTrue(sent, "nothing was sent, so nothing was checked")
        for kind, payload in sent:
            written = {k: v for k, v in payload.items() if k not in ("to", "sender")}
            for part in ("noname", "nopsy", "noadmin", "@gmail"):
                self.assertNotIn(part, json.dumps(written, ensure_ascii=False),
                                 f"{kind} carries {part!r}")

    def test_a_social_worker_with_no_name_is_a_colleague_not_an_address(self):
        held = self._held(self.nameless_sw, "Eli", "Tan")
        with self.outbox() as sent:
            self._ask(self.psy, by=self.nameless_sw, child=held)
            self._ask(self.psy2, by=self.nameless_sw, child=held)    # withdraws the first
            case_taken_over(held, self.other_sw, by=self.nameless_sw)
        self.assertEqual(["case_request", "request_withdrawn", "case_request",
                          "case_taken_over"], [k for k, _ in sent])
        self.assertNoAddress(sent)
        self.assertIn("A colleague, a social worker, asked you to take case",
                      sent[0][1]["textContent"])
        self.assertIn("A colleague withdrew the request", sent[1][1]["textContent"])
        self.assertEqual(f"Case {self._ref(held)} was reopened by A colleague",
                         sent[3][1]["subject"])

    def test_the_isa_with_no_name_is_still_not_an_address(self):
        with self.outbox() as sent:
            records_transferred(self.sw, self.nameless_sw, 2, by=self.nameless_admin)
            request_withdrawn(self.psy, self.child, by=self.nameless_admin)
        self.assertNoAddress(sent)
        text = sent[0][1]["textContent"]
        self.assertIn("The ISA moved 2 records to you from a colleague.", text)

    def test_a_psychologist_with_no_name_is_not_an_address_in_their_answer(self):
        req = self._accepted(self.child, self.nameless_psy)
        with self.outbox() as sent:
            case_answered(req, by=self.nameless_psy)
        self.assertNoAddress(sent)
        self.assertEqual(f"A psychologist accepted case {self._ref(self.child)}",
                         sent[0][1]["subject"])

    def test_a_recipient_with_no_name_is_sent_to_by_address_alone(self):
        with self.outbox() as sent:
            case_request(self.child, self.nameless_psy, by=self.sw)
        (payload,) = [p for _, p in sent]
        self.assertEqual([{"email": "nopsy@gmail.com"}], payload["to"])


class CaseRequestEmailTest(MailBase):
    """The fuller layout of "A case is waiting for your answer" (23 Oct 2026):
    the one email that asks something of its reader, so the one a look-alike
    would copy. It says who asked and in what role, where to find the question
    once signed in, and what the button is and is not."""

    LEAD_END = "asked you to take case {ref}. It joins your records only once you accept it."

    def _sent(self, by=None, to=None, child=None):
        with self.outbox() as sent:
            case_request(child or self.child, to or self.psy, by=by or self.sw)
        ((kind, payload),) = sent
        self.assertEqual("case_request", kind)
        return payload

    def test_the_subject_is_the_question_it_always_was(self):
        self.assertEqual("A case is waiting for your answer", self._sent()["subject"])

    def test_an_action_needed_chip_sits_over_the_heading_in_both_parts(self):
        payload = self._sent()
        page, text = payload["htmlContent"], payload["textContent"]
        self.assertIn("Action needed", page)
        self.assertIn(f"background:{_AMBER_TINT};color:{_AMBER_TEXT}", page)
        self.assertLess(page.index(">Action needed<"),
                        page.index(">A case is waiting for your answer</div>"))
        self.assertTrue(text.startswith("Action needed\nA case is waiting for your answer\n\n"))

    def test_the_recipient_is_greeted_by_the_name_they_typed(self):
        payload = self._sent()
        self.assertIn(">Hello Marivic Bulan,</p>", payload["htmlContent"])
        self.assertIn("\n\nHello Marivic Bulan,\n\n", payload["textContent"])
        self.assertLess(payload["htmlContent"].index("Hello Marivic Bulan,"),
                        payload["htmlContent"].index("asked you to take case"))

    def test_a_recipient_with_no_name_is_not_greeted_by_an_empty_one(self):
        nameless = User.objects.create_user(
            email="nopsy@gmail.com", username="nopsy@gmail.com", password="pass12345",
            role=self.roles[Role.PSYCHOLOGIST])
        payload = self._sent(to=nameless)
        for part in (payload["htmlContent"], payload["textContent"]):
            self.assertNotIn("Hello", part)
        # And not by the address either.
        self.assertNotIn("nopsy", json.dumps({k: v for k, v in payload.items() if k != "to"}))

    def test_the_lead_names_the_social_worker_and_their_role(self):
        ref = self._ref(self.child)
        payload = self._sent()
        lead = f"Editha Pascua, a social worker, {self.LEAD_END.format(ref=ref)}"
        self.assertIn(lead, payload["textContent"])
        self.assertIn(lead, payload["htmlContent"])

    def test_the_lead_names_the_isa_as_the_isa(self):
        payload = self._sent(by=self.admin)
        self.assertIn(f"Ada Admin, the ISA, {self.LEAD_END.format(ref=self._ref(self.child))}",
                      payload["textContent"])

    def test_a_nameless_asker_is_a_colleague_with_a_role_and_never_an_address(self):
        nameless = User.objects.create_user(
            email="noname@gmail.com", username="noname@gmail.com", password="pass12345",
            role=self.roles[Role.STAFF])
        payload = self._sent(by=nameless)
        self.assertIn(f"A colleague, a social worker, "
                      f"{self.LEAD_END.format(ref=self._ref(self.child))}",
                      payload["textContent"])
        self.assertIn("Asked by: A colleague", payload["textContent"])
        self.assertNotIn("noname", json.dumps({k: v for k, v in payload.items() if k != "to"}))

    def test_a_role_that_is_not_one_of_the_two_that_ask_is_not_named(self):
        payload = self._sent(by=self.psy2)
        self.assertIn(f"Jose Rizal {self.LEAD_END.format(ref=self._ref(self.child))}",
                      payload["textContent"])

    def test_the_fact_card_says_the_case_who_asked_and_when(self):
        payload = self._sent()
        text = payload["textContent"]
        self.assertIn(f"Case: {self._ref(self.child)}\nAsked by: Editha Pascua\n"
                      "Asked on: Wed 21 Oct 2026, 10:00 AM", text)
        for label in ("Case", "Asked by", "Asked on"):
            self.assertIn(f">{label}</td>", payload["htmlContent"])
        self.assertIn(">Wed 21 Oct 2026, 10:00&nbsp;AM</td>", payload["htmlContent"])

    def test_three_steps_that_name_the_panel_where_the_question_waits(self):
        ref = self._ref(self.child)
        payload = self._sent()
        steps = ["Open NACC SYS and sign in.",
                 f'Find {ref} under "Waiting for your answer" at the top of your Dashboard.',
                 "Accept it, or decline it with a reason for the social worker."]
        self.assertIn("What to do next:\n" + "\n".join(
            f"{i}. {step}" for i, step in enumerate(steps, 1)), payload["textContent"])
        page = payload["htmlContent"]
        self.assertEqual(3, page.count("<li>"))
        self.assertIn("<ol ", page)
        self.assertIn(f"Find {ref} under &quot;Waiting for your answer&quot; at the top",
                      page)
        # Facts card, then the steps, then the button.
        self.assertLess(page.index("Asked on"), page.index("What to do next"))
        self.assertLess(page.index("What to do next"), page.index("Review the request"))

    def test_the_panel_the_steps_name_is_the_one_the_dashboard_really_has(self):
        """The wording is in two places, here and in the screen. A psychologist
        told to look for a panel that is called something else is lost."""
        screen = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "components"
                  / "AssignmentRequests.jsx")
        if not screen.exists():
            self.skipTest("the frontend is not checked out beside the backend")
        self.assertIn("Waiting for your answer", screen.read_text(encoding="utf-8"))

    def test_the_button_is_review_the_request_and_opens_the_front_door(self):
        payload = self._sent()
        self.assertIn(f'href="{ORIGIN}/"', payload["htmlContent"])
        self.assertIn(">Review the request</a>", payload["htmlContent"])
        self.assertIn(f"Review the request: {ORIGIN}/", payload["textContent"])

    def test_the_plain_address_and_the_safety_line_are_there(self):
        payload = self._sent()
        for part in (payload["htmlContent"], payload["textContent"]):
            self.assertIn("Or open NACC SYS at", part)
            self.assertIn(SAFETY, part)
        self.assertIn(f"Or open NACC SYS at {ORIGIN}\n", payload["textContent"])
        self.assertEqual(1, payload["htmlContent"].count("<a "))

    def test_the_inbox_preview_says_which_case_and_what_to_do(self):
        payload = self._sent()
        page = payload["htmlContent"]
        self.assertIn(f"color:#f4f6fa;\">Case {self._ref(self.child)} is waiting for you to "
                      "accept or decline.&zwnj;", page)
        self.assertLess(page.index("mso-hide:all"), page.index("NACC &ndash; RACCO 1"))

    def test_with_no_address_known_there_is_no_button_and_no_address_line(self):
        with override_settings(BREVO_API_KEY="k", CORS_ALLOWED_ORIGINS=[]), \
                patch("accounts.email_notifications._queue") as queue:
            case_request(self.child, self.psy, by=self.sw)
        payload = queue.call_args.args[0]
        for part in (payload["htmlContent"], payload["textContent"]):
            self.assertNotIn("Or open", part)
            self.assertNotIn("Review the request", part)
            self.assertNotIn("NACC SYS never asks", part)
            self.assertIn("What to do next", part)        # the email still says what to do
        self.assertNotIn("<a ", payload["htmlContent"])

    def test_it_is_sent_with_the_site_setting_when_there_is_one(self):
        with override_settings(SITE_URL="https://nacc.example.gov.ph"):
            payload = self._sent()
        self.assertIn('href="https://nacc.example.gov.ph/"', payload["htmlContent"])
        self.assertIn("Or open NACC SYS at https://nacc.example.gov.ph\n", payload["textContent"])
        self.assertNotIn(ORIGIN, payload["htmlContent"] + payload["textContent"])


class HostileNameTest(MailBase):
    """The same, end to end: a name typed to be read as something else reaches
    nobody as anything but a plain name. Searched for in every part that
    leaves, because a name is in the subject, the page, the text part and the
    log line."""

    def _rename(self, user, first, last=""):
        user.first_name, user.last_name = first, last
        user.save()
        return user

    def assertPlain(self, payload):
        for part in ("subject", "htmlContent", "textContent"):
            for ch in payload[part]:
                category = unicodedata.category(ch)
                self.assertNotEqual("Cf", category, f"{part}: U+{ord(ch):04X}")
                if category == "Cc":
                    self.assertIn(ch, "\n", f"{part}: U+{ord(ch):04X}")
        self.assertNotIn("\n", payload["subject"])

    def _sent(self, by):
        with self.outbox() as sent:
            case_request(self.child, self.psy, by=by)
        ((_, payload),) = sent
        return payload

    def test_a_right_to_left_override_in_the_askers_name_does_not_reach_the_email(self):
        payload = self._sent(self._rename(self.sw, "Ana \u202eBulan", "-- skae"))
        self.assertPlain(payload)
        self.assertIn("Ana Bulan -- skae, a social worker, asked you to take case",
                      payload["textContent"])
        self.assertIn("Ana Bulan -- skae, a social worker, asked you to take case",
                      payload["htmlContent"])

    def test_a_name_with_newlines_stays_on_one_line_of_the_lead_and_the_fact(self):
        payload = self._sent(self._rename(
            self.sw, "Editha\nPascua\n\nPlease reply with your password"))
        self.assertPlain(payload)
        lines = payload["textContent"].split("\n")
        (lead,) = [t for t in lines if "asked you to take case" in t]
        (fact,) = [t for t in lines if t.startswith("Asked by:")]
        self.assertTrue(lead.startswith("Editha Pascua Please reply with your password, "), lead)
        self.assertEqual("Asked by: Editha Pascua Please reply with your password", fact)

    def test_a_long_name_is_a_name_and_not_a_paragraph(self):
        sentence = ("Notice: your NACC SYS session expired. Sign in again at "
                    "https://nacc-v3-demo.onrender.com.evil.example/login to keep your cases")
        payload = self._sent(self._rename(self.sw, sentence[:150], sentence[150:]))
        self.assertPlain(payload)
        (fact,) = [t for t in payload["textContent"].split("\n") if t.startswith("Asked by:")]
        self.assertEqual(len("Asked by: ") + NAME_LIMIT, len(fact))
        self.assertNotIn("evil.example", payload["textContent"] + payload["htmlContent"])

    def test_a_psychologists_name_is_cleaned_in_their_answers_subject_too(self):
        self._rename(self.psy, "Marivic\r\nBcc: attacker@evil.example", "Bu\u202elan")
        req = AssignmentRequest.objects.create(
            child=self.child, psychologist=self.psy, requested_by=self.sw,
            status=AssignmentRequest.DECLINED)
        with self.outbox() as sent:
            case_answered(req, by=self.psy)
        ((_, payload),) = sent
        self.assertPlain(payload)
        self.assertEqual(f"Marivic Bcc: attacker@evil.example Bulan declined case "
                         f"{self._ref(self.child)}", payload["subject"])

    def test_the_greeting_uses_the_cleaned_name(self):
        self._rename(self.psy, "Marivic\n", "Bu\u200blan")
        payload = self._sent(self.sw)
        self.assertPlain(payload)
        self.assertIn("\n\nHello Marivic Bulan,\n\n", payload["textContent"])

    def test_the_console_line_is_one_line_when_no_mail_service_is_set_up(self):
        self._rename(self.psy, "Marivic\r\nFAKE LOG LINE", "Bulan")
        req = self._accepted(self.child, self.psy)
        with override_settings(BREVO_API_KEY=""), self.assertLogs(
                "accounts.email_notifications", level="WARNING") as logged:
            case_answered(req, by=self.psy)
        (line,) = logged.output
        self.assertNotIn("\n", line)
        self.assertIn("Marivic FAKE LOG LINE Bulan accepted case", line)


# --------------------------------------------------------------------------
# The triggers, through the real endpoints
# --------------------------------------------------------------------------

class TriggersTest(MailBase):
    def test_a_social_worker_adds_a_record_with_a_psychologist_and_the_psychologist_is_asked(self):
        with self.outbox() as sent:
            res = self._as(self.sw).post(
                "/api/children/", complete(psychologist=self.psy.id), format="json")
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual(["case_request"], [k for k, _ in sent])
        ((_, payload),) = self.to(sent, self.psy)
        self.assertIn(f"C-{res.data['id']:04d}", payload["textContent"])
        self.assertIn("Editha Pascua", payload["textContent"])

    def test_a_record_with_no_psychologist_mails_nobody(self):
        with self.outbox() as sent:
            res = self._as(self.sw).post("/api/children/", complete(), format="json")
        self.assertEqual(201, res.status_code, res.data)
        self.assertEqual([], sent)

    def test_switching_psychologist_withdraws_from_the_first_and_asks_the_second(self):
        self._ask(self.psy)
        with self.outbox() as sent:
            self._ask(self.psy2)
        self.assertEqual(["request_withdrawn"], self.kinds_to(sent, self.psy))
        self.assertEqual(["case_request"], self.kinds_to(sent, self.psy2))
        self.assertEqual(2, len(sent))
        withdrawn = self.to(sent, self.psy)[0][1]
        self.assertEqual(f"Case {self._ref(self.child)} is no longer waiting for you",
                         withdrawn["subject"])
        self.assertIn("Editha Pascua withdrew the request", withdrawn["textContent"])

    def test_asking_the_same_psychologist_again_mails_nobody(self):
        self._ask(self.psy)
        with self.outbox() as sent:
            self._ask(self.psy)
        self.assertEqual([], sent)

    def test_the_social_worker_withdrawing_it_tells_the_psychologist(self):
        req = self._ask(self.psy)
        with self.outbox() as sent:
            res = self._as(self.sw).post(f"/api/assignment-requests/{req.id}/withdraw/")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["request_withdrawn"], self.kinds_to(sent, self.psy))
        self.assertEqual(1, len(sent))

    def _held_by_psy_with_a_question_to_psy2(self):
        """psy holds the child, and the social worker has asked psy2 to take
        it over: one open request, to someone who is not the holder."""
        self.child.assigned_psychologist = self.psy
        self.child.save()
        self._ask(self.psy2)

    def _the_only_mail_is_to_psy2(self, sent):
        self.assertEqual(["request_withdrawn"], self.kinds_to(sent, self.psy2))
        self.assertEqual(1, len(sent), [(k, p["to"][0]["email"]) for k, p in sent])
        self.assertEqual(f"Case {self._ref(self.child)} is no longer waiting for you",
                         sent[0][1]["subject"])
        return sent[0][1]

    def test_leaving_the_child_unassigned_withdraws_the_question_and_says_so(self):
        self._held_by_psy_with_a_question_to_psy2()
        with self.outbox() as sent:
            res = self._as(self.sw).patch(f"/api/children/{self.child.id}/",
                                          {"psychologist": None}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        payload = self._the_only_mail_is_to_psy2(sent)
        self.assertIn("Editha Pascua withdrew the request", payload["textContent"])

    def test_picking_the_holder_back_withdraws_the_question_and_says_so(self):
        self._held_by_psy_with_a_question_to_psy2()
        with self.outbox() as sent:
            res = self._as(self.sw).patch(f"/api/children/{self.child.id}/",
                                          {"psychologist": self.psy.id}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        payload = self._the_only_mail_is_to_psy2(sent)
        self.assertIn("Editha Pascua withdrew the request", payload["textContent"])

    def test_closing_the_case_takes_the_question_back_without_blaming_the_closer(self):
        self._held_by_psy_with_a_question_to_psy2()
        for closer, reason in ((self.admin, "Services completed"), (self.psy, "Other")):
            AssignmentRequest.objects.filter(child=self.child).update(
                status=AssignmentRequest.PENDING)
            Child.objects.filter(pk=self.child.pk).update(
                status=Child.ACTIVE, case_status=Child.STAGE_PRE_ASSESSMENT)
            with self.outbox() as sent:
                res = self._as(closer).post(
                    f"/api/children/{self.child.id}/terminate/",
                    {"reason_category": reason, "note": "Done."}, format="json")
            self.assertEqual(200, res.status_code, res.data)
            payload = self._the_only_mail_is_to_psy2(sent)
            self.assertIn("was closed, so the request for you to take it is no longer open",
                          payload["textContent"])
            self.assertNotIn("withdrew", payload["textContent"])
            self.assertNotIn(closer.fullname, payload["textContent"] + payload["htmlContent"])

    def test_the_psychologist_accepting_tells_the_social_worker(self):
        """Not at the moment of accepting: the acceptance waits for what the
        psychologist does next (test_answer_email.py has the three ways it goes).
        Here the dialog ends with no booking."""
        req = self._ask(self.psy)
        with self.outbox() as sent:
            res = self._answer(req, "accept")
            self.assertEqual(200, res.status_code, res.data)
            self.assertEqual([], sent)
            self.assertEqual(200, self._answer(req, "mail-answer").status_code)
        self.assertEqual(["case_accepted"], self.kinds_to(sent, self.sw))
        self.assertEqual(1, len(sent))
        payload = sent[0][1]
        ref = self._ref(self.child)
        self.assertEqual(f"Marivic Bulan accepted case {ref}", payload["subject"])
        self.assertIn(f"{ORIGIN}/report/child/{self.child.pk}", payload["htmlContent"])

    def test_declining_tells_the_social_worker_and_never_says_why(self):
        req = self._ask(self.psy)
        reason = "Quillon caseload is already full until March"
        with self.outbox() as sent:
            res = self._answer(req, "decline", reason=reason)
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["case_declined"], self.kinds_to(sent, self.sw))
        self.assertEqual(1, len(sent))
        payload = sent[0][1]
        self.assertEqual(f"Marivic Bulan declined case {self._ref(self.child)}",
                         payload["subject"])
        self.assertIn("Sign in to read their reason", payload["textContent"])
        self.assertNotIn("Quillon", json.dumps(payload))
        self.assertNotIn("March", json.dumps(payload))

    def test_a_refused_answer_mails_nobody(self):
        req = self._ask(self.psy)
        with self.outbox() as sent:
            self.assertEqual(400, self._answer(req, "decline", reason="").status_code)
            self.assertEqual(404, self._answer(req, "accept", user=self.psy2).status_code)
        self.assertEqual([], sent)

    def test_the_record_holder_hears_even_when_the_isa_was_the_one_who_asked(self):
        req = self._ask(self.psy, by=self.admin)
        with self.outbox() as sent:
            self._answer(req, "accept")
            self._answer(req, "mail-answer")
        self.assertEqual(["case_accepted"], self.kinds_to(sent, self.sw))
        self.assertEqual([], self.to(sent, self.admin))

    def test_the_isa_asking_about_a_record_nobody_holds_is_not_emailed_the_answer(self):
        accepted = self._ask(self.psy, by=self.admin, child=self._held(None, "Cara", "Diaz"))
        declined = self._ask(self.psy2, by=self.admin, child=self._held(None, "Dino", "Reyes"))
        self.assertEqual(self.admin, accepted.requested_by)
        with self.outbox() as sent:
            self.assertEqual(200, self._answer(accepted, "accept").status_code)
            self.assertEqual(200, self._answer(declined, "decline", user=self.psy2,
                                               reason="Full.").status_code)
        self.assertEqual([], sent)

    def test_when_nobody_holds_the_record_the_social_worker_who_asked_hears(self):
        """The fallback `_record_holder` has: whoever asked where the record
        has no social worker. Not reachable through the endpoints (a social
        worker asks only on their own records), so the function is called."""
        orphan = self._held(None, "Cara", "Diaz")
        req = self._accepted(orphan, self.psy)
        with self.outbox() as sent:
            case_answered(req, by=self.psy)
        self.assertEqual(["case_accepted"], self.kinds_to(sent, self.sw))
        self.assertEqual(1, len(sent))

    def test_the_isa_moving_records_tells_the_receiving_social_worker(self):
        moved = [self._held(self.other_sw, "Eli", "Tan"), self._held(self.other_sw, "Fe", "Go")]
        with self.outbox() as sent:
            res = self._as(self.admin).post("/api/children/transfer/", {
                "from_social_worker": self.other_sw.pk, "to_social_worker": self.sw.pk,
                "children": [c.pk for c in moved]}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["records_transferred"], self.kinds_to(sent, self.sw))
        self.assertEqual(1, len(sent))
        payload = sent[0][1]
        self.assertEqual("2 records moved to you", payload["subject"])
        self.assertIn("Ada Admin moved 2 records to you from Rosa Santos.",
                      payload["textContent"])

    def test_one_record_is_a_record(self):
        one = self._held(self.other_sw, "Eli", "Tan")
        with self.outbox() as sent:
            self._as(self.admin).post("/api/children/transfer/", {
                "from_social_worker": self.other_sw.pk, "to_social_worker": self.sw.pk,
                "children": [one.pk]}, format="json")
        self.assertEqual("1 record moved to you", sent[0][1]["subject"])

    def test_a_refused_move_mails_nobody(self):
        closed = self._held(self.other_sw, "Eli", "Tan", status=Child.INACTIVE)
        with self.outbox() as sent:
            res = self._as(self.admin).post("/api/children/transfer/", {
                "from_social_worker": self.other_sw.pk, "to_social_worker": self.sw.pk,
                "children": [closed.pk]}, format="json")
        self.assertEqual(409, res.status_code)
        self.assertEqual([], sent)

    def _closed(self, worker):
        return self._held(worker, "Ben", "Lim", status=Child.INACTIVE,
                          case_status=Child.STAGE_TERMINATED)

    def test_reopening_another_workers_closed_case_at_intake_tells_that_worker(self):
        closed = self._closed(self.other_sw)
        with self.outbox() as sent:
            res = self._as(self.sw).post(f"/api/children/{closed.pk}/reopen/",
                                         {"first_name": "Ben", "last_name": "Lim"},
                                         format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["case_taken_over"], self.kinds_to(sent, self.other_sw))
        self.assertEqual(1, len(sent))
        self.assertEqual(f"Case {self._ref(closed)} was reopened by Editha Pascua",
                         sent[0][1]["subject"])

    def test_reopening_your_own_closed_case_or_one_nobody_held_mails_nobody(self):
        mine = self._closed(self.sw)
        nobody = self._closed(None)
        with self.outbox() as sent:
            self.assertEqual(200, self._as(self.sw).post(
                f"/api/children/{mine.pk}/reopen/", {}, format="json").status_code)
            self.assertEqual(200, self._as(self.sw).post(
                f"/api/children/{nobody.pk}/reopen/",
                {"first_name": "Ben", "last_name": "Lim"}, format="json").status_code)
        self.assertEqual([], sent)

    def test_the_isa_reopening_takes_nothing_over_and_mails_nobody(self):
        closed = self._closed(self.other_sw)
        with self.outbox() as sent:
            res = self._as(self.admin).post(f"/api/children/{closed.pk}/reopen/", {},
                                            format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual([], sent)

    def test_removing_a_duplicate_tells_the_psychologist_whose_question_went(self):
        original, duplicate = self._held(self.sw), self._held(self.sw)
        AssignmentRequest.objects.create(child=duplicate, psychologist=self.psy,
                                         requested_by=self.sw)
        # A declined one was already answered: no second mail for it.
        AssignmentRequest.objects.create(child=duplicate, psychologist=self.psy2,
                                         requested_by=self.sw,
                                         status=AssignmentRequest.DECLINED, reason="Full.")
        ref = self._ref(duplicate)
        with self.outbox() as sent:
            res = self._as(self.admin).post(
                f"/api/children/{duplicate.pk}/remove-duplicate/",
                {"duplicate_of": original.pk, "case_reference": ref}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual(["request_withdrawn"], self.kinds_to(sent, self.psy))
        self.assertEqual(1, len(sent))
        self.assertEqual(f"Case {ref} is no longer waiting for you", sent[0][1]["subject"])
        self.assertIn("Ada Admin withdrew the request", sent[0][1]["textContent"])

    def test_a_refused_removal_mails_nobody(self):
        original, duplicate = self._held(self.sw), self._held(self.sw)
        AssignmentRequest.objects.create(child=duplicate, psychologist=self.psy,
                                         requested_by=self.sw)
        with self.outbox() as sent:
            res = self._as(self.admin).post(
                f"/api/children/{duplicate.pk}/remove-duplicate/",
                {"duplicate_of": original.pk, "case_reference": "C-9999"}, format="json")
        self.assertEqual(400, res.status_code)
        self.assertEqual([], sent)


class NeverForYourOwnActionTest(MailBase):
    """Rule 3. The endpoints seldom put the actor on the receiving end, so the
    domain functions are called with the actor as the recipient directly."""

    def test_nobody_is_emailed_about_what_they_did_themselves(self):
        accepted = self._accepted(self.child, self.psy)
        with self.outbox() as sent:
            case_request(self.child, self.psy, by=self.psy)
            request_withdrawn(self.psy, self.child, by=self.psy)
            request_withdrawn(self.psy, "C-0001", by=self.psy)
            case_answered(accepted, by=self.sw)            # the holder is the actor
            records_transferred(self.sw, self.other_sw, 3, by=self.sw)
            case_taken_over(self.child, self.sw, by=self.sw)
        self.assertEqual([], sent)

    def test_the_same_calls_do_mail_someone_else(self):
        accepted = self._accepted(self.child, self.psy)
        with self.outbox() as sent:
            case_request(self.child, self.psy, by=self.sw)
            request_withdrawn(self.psy, self.child, by=self.sw)
            case_answered(accepted, by=self.psy)
            records_transferred(self.sw, self.other_sw, 3, by=self.admin)
            case_taken_over(self.child, self.sw, by=self.other_sw)
        self.assertEqual(["case_request", "request_withdrawn", "case_accepted",
                          "records_transferred", "case_taken_over"], [k for k, _ in sent])

    def test_a_failure_while_preparing_one_is_logged_and_the_save_stands(self):
        accepted = self._accepted(self.child, self.psy)
        with self.outbox() as sent, \
                patch("accounts.email_notifications.entered_name", side_effect=ValueError), \
                self.assertLogs("accounts.email_notifications", level="ERROR") as logged:
            case_request(self.child, self.psy, by=self.sw)
            request_withdrawn(self.psy, self.child, by=self.sw)
            case_answered(accepted, by=self.psy)
            records_transferred(self.sw, self.other_sw, 2, by=self.admin)
            case_taken_over(self.child, self.sw, by=self.other_sw)
        self.assertEqual([], sent)
        self.assertEqual(5, len(logged.records))

    def test_a_missing_recipient_is_nobody(self):
        with self.outbox() as sent:
            case_request(self.child, None, by=self.sw)
            request_withdrawn(None, self.child, by=self.sw)
            records_transferred(None, self.sw, 1, by=self.admin)
            case_taken_over(self.child, None, by=self.sw)
        self.assertEqual([], sent)


class OptOutTest(MailBase):
    def test_a_switch_stops_exactly_its_own_kind(self):
        EmailOptOut.objects.create(user=self.psy, kind="case_request")
        with self.outbox() as sent:
            req = self._ask(self.psy)                      # case_request: switched off
            self._as(self.sw).post(f"/api/assignment-requests/{req.id}/withdraw/")
        self.assertEqual(["request_withdrawn"], self.kinds_to(sent, self.psy))

    def test_a_social_worker_switching_off_accepted_still_hears_declined(self):
        EmailOptOut.objects.create(user=self.sw, kind="case_accepted")
        accepted = self._ask(self.psy)
        declined = self._ask(self.psy2, child=self._held(self.sw))
        with self.outbox() as sent:
            self._answer(accepted, "accept")
            self._answer(accepted, "mail-answer")
            self._answer(declined, "decline", user=self.psy2, reason="Full.")
        self.assertEqual(["case_declined"], self.kinds_to(sent, self.sw))

    def test_one_persons_switch_is_not_everyones(self):
        EmailOptOut.objects.create(user=self.psy, kind="case_request")
        with self.outbox() as sent:
            self._ask(self.psy2)
        self.assertEqual(["case_request"], self.kinds_to(sent, self.psy2))

    def test_switching_it_back_on_brings_it_back(self):
        EmailOptOut.objects.create(user=self.psy, kind="case_request")
        EmailOptOut.objects.filter(user=self.psy, kind="case_request").delete()
        with self.outbox() as sent:
            self._ask(self.psy)
        self.assertEqual(["case_request"], self.kinds_to(sent, self.psy))


# --------------------------------------------------------------------------
# What leaves the building
# --------------------------------------------------------------------------

class PrivacyTest(MailBase):
    """Rule 1, searched for rather than assumed. Every trigger is run against
    children carrying a name, a birth date, a case type, a custodian and a token
    in every other free-text column (`planted_text`) that nothing else in the
    system would produce, and every payload handed to the sender is searched
    for them - subject, page and text part, in any letter case. The accounts
    that act are made the way production makes them, with no name."""

    REASON = "Quillon caseload is already full until March"

    def _sentinel_child(self, worker, **fields):
        return Child.objects.create(
            first_name="Zyxwanita", last_name="Qorvath", fullname="Zyxwanita Qorvath",
            birth_date=self.born, case_type="Foster Care", case_category="Neglected",
            custodian_name="Brunhilda Vexley", referral_reason=self.REASON,
            social_worker=worker, **planted_text(), **fields)

    def test_no_email_carries_the_child(self):
        with self.outbox() as sent:
            # A new record, asked of a psychologist.
            res = self._as(self.sw).post("/api/children/", complete(
                first_name="Zyxwanita", last_name="Qorvath", birth_date=str(self.born),
                custodian_name="Brunhilda Vexley (foster parent)",
                psychologist=self.psy.id), format="json")
            self.assertEqual(201, res.status_code, res.data)
            new = Child.objects.get(pk=res.data["id"])
            # Switched to another psychologist: the first is told it was withdrawn.
            self._ask(self.psy2, child=new)
            # Accepted.
            req = AssignmentRequest.objects.get(child=new, status=AssignmentRequest.PENDING)
            self.assertEqual(200, self._answer(req, "accept", user=self.psy2).status_code)
            self.assertEqual(200, self._answer(req, "mail-answer", user=self.psy2).status_code)
            # Declined, with a reason.
            held = self._sentinel_child(self.sw)
            req = self._ask(self.psy, child=held)
            self.assertEqual(200, self._answer(req, "decline", reason=self.REASON).status_code)
            # Moved to another social worker.
            moved = self._sentinel_child(self.other_sw)
            self.assertEqual(200, self._as(self.admin).post("/api/children/transfer/", {
                "from_social_worker": self.other_sw.pk, "to_social_worker": self.sw.pk,
                "children": [moved.pk]}, format="json").status_code)
            # A closed case reopened at intake.
            closed = self._sentinel_child(self.other_sw, status=Child.INACTIVE,
                                          case_status=Child.STAGE_TERMINATED)
            self.assertEqual(200, self._as(self.sw).post(
                f"/api/children/{closed.pk}/reopen/",
                {"first_name": "Zyxwanita", "last_name": "Qorvath"},
                format="json").status_code)
            # A duplicate removed with a request still open.
            original, duplicate = self._sentinel_child(self.sw), self._sentinel_child(self.sw)
            AssignmentRequest.objects.create(child=duplicate, psychologist=self.psy2,
                                             requested_by=self.sw)
            self.assertEqual(200, self._as(self.admin).post(
                f"/api/children/{duplicate.pk}/remove-duplicate/",
                {"duplicate_of": original.pk, "case_reference": self._ref(duplicate)},
                format="json").status_code)

        # Every trigger really ran, or the search below proves nothing.
        self.assertEqual({"case_request", "request_withdrawn", "case_accepted",
                          "case_declined", "records_transferred", "case_taken_over"},
                         {kind for kind, _ in sent})
        # And the request searched is the fuller layout of 23 Oct 2026: its
        # greeting, fact card and steps are the new places a name could slip in.
        requests = [p for kind, p in sent if kind == "case_request"]
        self.assertTrue(requests)
        for payload in requests:
            for part in ("Action needed", "Hello ", "Asked by", "What to do next",
                         "Or open NACC SYS at", SAFETY):
                self.assertIn(part, payload["htmlContent"])
        planted = planted_text()
        self.assertGreaterEqual(len(planted), 15, "the columns to plant were not found")
        sentinels = ("Zyxwanita", "Qorvath", "Brunhilda", "Vexley", "Quillon", "March",
                     "Z. Qorvath", "Qorvath, Z", "Foster Care", "Neglected",
                     self.born.isoformat(), self.born.strftime("%d %b %Y"),
                     self.born.strftime("%B %d, %Y"), *planted.values())
        for kind, payload in sent:
            # Not ASCII-escaped: json.dumps writes "ñ" as an escape sequence, and
            # a search for "Peña" would then find nothing in a payload carrying
            # it. Lower-cased, so an upper-cased name is found as well.
            everything = json.dumps(payload, ensure_ascii=False).lower()
            for sentinel in sentinels:
                self.assertNotIn(sentinel.lower(), everything, f"{kind} carries {sentinel!r}")
            # The page and the text part are in the payload as strings; a name
            # escaped for JSON would still have been found above, but say so.
            for part in ("subject", "htmlContent", "textContent"):
                self.assertTrue(payload[part].strip(), f"{kind} has no {part}")


# --------------------------------------------------------------------------
# My Profile's switches
# --------------------------------------------------------------------------

class PreferencesApiTest(MailBase):
    def _kinds(self, response):
        return {k["key"]: k["enabled"] for k in response.data["kinds"]}

    def test_the_route(self):
        self.assertEqual(PREFERENCES, reverse("my-email-notifications"))

    def test_anonymous_gets_401(self):
        client = APIClient()
        self.assertEqual(401, client.get(PREFERENCES).status_code)
        self.assertEqual(401, client.put(PREFERENCES, {"enabled": {}}, format="json").status_code)

    @override_settings(BREVO_API_KEY="")
    def test_a_psychologist_sees_their_three_all_on_by_default(self):
        res = self._as(self.psy).get(PREFERENCES)
        self.assertEqual(200, res.status_code)
        self.assertEqual("psy@t.ph", res.data["address"])
        self.assertIs(False, res.data["sending"])
        self.assertEqual(KEYS[:3], [k["key"] for k in res.data["kinds"]])
        first = res.data["kinds"][0]
        self.assertEqual({"key", "label", "description", "enabled"}, set(first))
        self.assertEqual(KIND["case_request"].label, first["label"])
        self.assertEqual(KIND["case_request"].description, first["description"])
        self.assertEqual([True] * 3, [k["enabled"] for k in res.data["kinds"]])

    @override_settings(BREVO_API_KEY="test-key")
    def test_a_social_worker_sees_their_five_and_whether_a_service_is_set_up(self):
        res = self._as(self.sw).get(PREFERENCES)
        self.assertIs(True, res.data["sending"])
        self.assertEqual(KEYS[3:], [k["key"] for k in res.data["kinds"]])

    def test_an_administrator_gets_none(self):
        res = self._as(self.admin).get(PREFERENCES)
        self.assertEqual(200, res.status_code)
        self.assertEqual("admin@t.ph", res.data["address"])
        self.assertEqual([], res.data["kinds"])

    def test_the_get_reads_the_opt_outs_and_ignores_a_retired_key(self):
        EmailOptOut.objects.create(user=self.psy, kind="my_calendar")
        EmailOptOut.objects.create(user=self.psy, kind="a_key_retired_long_ago")
        res = self._as(self.psy).get(PREFERENCES)
        self.assertEqual({"case_request": True, "request_withdrawn": True,
                          "my_calendar": False}, self._kinds(res))

    def test_put_persists_and_round_trips(self):
        client = self._as(self.psy)
        res = client.put(PREFERENCES, {"enabled": {"case_request": False}}, format="json")
        self.assertEqual(200, res.status_code, res.data)
        self.assertEqual({"case_request": False, "request_withdrawn": True,
                          "my_calendar": True}, self._kinds(res))
        self.assertEqual(["case_request"], list(
            EmailOptOut.objects.filter(user=self.psy).values_list("kind", flat=True)))
        self.assertEqual(self._kinds(res), self._kinds(client.get(PREFERENCES)))

        res = client.put(PREFERENCES, {"enabled": {"case_request": True,
                                                   "my_calendar": False}}, format="json")
        self.assertEqual({"case_request": True, "request_withdrawn": True,
                          "my_calendar": False}, self._kinds(res))
        self.assertEqual(["my_calendar"], list(
            EmailOptOut.objects.filter(user=self.psy).values_list("kind", flat=True)))

    def test_only_the_keys_sent_change_and_saying_it_twice_is_one_row(self):
        EmailOptOut.objects.create(user=self.psy, kind="my_calendar")
        client = self._as(self.psy)
        for _ in range(2):
            res = client.put(PREFERENCES, {"enabled": {"case_request": False}}, format="json")
            self.assertEqual(200, res.status_code, res.data)
        self.assertEqual({"case_request", "my_calendar"}, set(
            EmailOptOut.objects.filter(user=self.psy).values_list("kind", flat=True)))
        self.assertEqual(2, EmailOptOut.objects.filter(user=self.psy).count())

    def test_an_empty_choice_changes_nothing(self):
        res = self._as(self.sw).put(PREFERENCES, {"enabled": {}}, format="json")
        self.assertEqual(200, res.status_code)
        self.assertFalse(EmailOptOut.objects.exists())

    def test_it_changes_only_the_callers_own_rows(self):
        self._as(self.psy).put(PREFERENCES, {"enabled": {"case_request": False}},
                               format="json")
        self.assertTrue(wants(self.psy2, "case_request"))

    def test_a_choice_really_does_stop_the_email(self):
        self._as(self.psy).put(PREFERENCES, {"enabled": {"case_request": False}},
                               format="json")
        with self.outbox() as sent:
            self._ask(self.psy)
        self.assertEqual([], sent)

    def _refused(self, user, body):
        before = set(EmailOptOut.objects.values_list("user_id", "kind"))
        res = self._as(user).put(PREFERENCES, body, format="json")
        self.assertEqual(400, res.status_code, (body, res.data))
        self.assertEqual(["enabled"], list(res.data))
        self.assertEqual(before, set(EmailOptOut.objects.values_list("user_id", "kind")),
                         "a refused request wrote something")
        return res.data["enabled"]

    def test_a_key_that_is_not_in_the_catalogue_is_refused_by_name(self):
        message = self._refused(self.psy, {"enabled": {"no_such_email": False}})
        self.assertIn("no_such_email", message)

    def test_another_roles_key_is_refused_by_name(self):
        self.assertIn("case_accepted",
                      self._refused(self.psy, {"enabled": {"case_accepted": False}}))
        self.assertIn("my_calendar",
                      self._refused(self.sw, {"enabled": {"my_calendar": False}}))
        self.assertIn("case_request",
                      self._refused(self.admin, {"enabled": {"case_request": False}}))

    def test_a_value_that_is_not_a_boolean_is_refused_by_name(self):
        for value in ("false", "true", 0, 1, None, [], {}, "off"):
            message = self._refused(self.psy, {"enabled": {"case_request": value}})
            self.assertIn("case_request", message, value)

    def test_one_bad_entry_means_none_of_them_are_written(self):
        self._refused(self.psy, {"enabled": {"case_request": False, "my_calendar": "no"}})
        self._refused(self.psy, {"enabled": {"case_request": False, "case_accepted": False}})
        self.assertFalse(EmailOptOut.objects.exists())

    def test_a_body_of_the_wrong_shape_is_refused(self):
        for body in ({}, {"enabled": None}, {"enabled": ["case_request"]},
                     {"enabled": "case_request"}, {"case_request": False}):
            self._refused(self.psy, body)
        res = self._as(self.psy).put(PREFERENCES, ["case_request"], format="json")
        self.assertEqual(400, res.status_code)
        self.assertFalse(EmailOptOut.objects.exists())
