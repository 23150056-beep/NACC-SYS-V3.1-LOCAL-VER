"""The calendar invite is a file other people's software has to accept
(scheduling/calendar_invite.py), so these tests read it the way a calendar
would: line by line, with the folds undone.

The format's traps are each pinned: CRLF on every line, TEXT escaping, quoted
CN parameters, and the 75-OCTET limit - octets, not characters, which is what a
long name with an "ñ" in it breaks and a character count never notices.

Nothing here books anything. The invite is built from an appointment that was
never saved, which is also the point: it must not need the child's row, so it
cannot reach for the child's name.
"""
import hashlib
import re
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.display import NAME_LIMIT
from accounts.models import AgencyProfile, Role
from scheduling.calendar_invite import (
    FALLBACK_LOCATION, LINE_LIMIT, _cn, _text, build_ics, fold, legacy_uid)
from scheduling.models import Appointment

User = get_user_model()

ORIGIN = "https://nacc.example.test"
NOW = datetime(2026, 10, 19, 2, 0, tzinfo=dt_timezone.utc)
# What the caller (session_mail.py) hands build_ics. It is not worked out here.
UID = "appointment-7-0a1b2c3d4e@nacc-sys"


def logical(ics):
    """The content lines with every fold undone (RFC 5545 3.1: a CRLF followed
    by one space or tab is not a line break)."""
    assert ics.endswith("\r\n"), "the last line must end its CRLF too"
    return ics[:-2].replace("\r\n ", "").split("\r\n")


def line(ics, name):
    """The one logical line that starts `name` (NAME: or NAME;), or fail."""
    found = [text for text in logical(ics)
             if text.startswith(name + ":") or text.startswith(name + ";")]
    assert len(found) == 1, f"{name}: {found}"
    return found[0]


def value(ics, name):
    return line(ics, name).split(":", 1)[1]


@override_settings(CORS_ALLOWED_ORIGINS=[ORIGIN], BREVO_SENDER_EMAIL="from@racco1.gov.ph",
                   BREVO_SENDER_NAME="NACC RACCO1")
class BuildIcsTest(TestCase):
    def setUp(self):
        role = Role.objects.create(role_name=Role.PSYCHOLOGIST)
        self.psy = User.objects.create_user(
            email="psy@t.ph", username="psy", password="pass12345",
            first_name="Marivic", last_name="Bulan", role=role)
        # Never saved: the invite is built from the appointment's own columns
        # and must not need anything else.
        self.start = timezone.make_aware(datetime(2026, 10, 21, 9, 30))   # Manila
        self.appt = Appointment(pk=7, child_id=42, psychologist=self.psy,
                                start=self.start, duration_minutes=60)
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def ics(self, **over):
        args = dict(method="REQUEST", sequence=1, uid=UID)
        args.update(over)
        return build_ics(self.appt, self.psy, **args)

    # -- the shape of the file ----------------------------------------------

    def test_a_calendar_with_one_event(self):
        text = logical(self.ics())
        self.assertEqual("BEGIN:VCALENDAR", text[0])
        self.assertEqual("END:VCALENDAR", text[-1])
        self.assertEqual(["BEGIN:VEVENT"], [t for t in text if t == "BEGIN:VEVENT"])
        self.assertEqual(["END:VEVENT"], [t for t in text if t == "END:VEVENT"])
        self.assertEqual(["PRODID:-//NACC SYS//Appointments//EN", "VERSION:2.0",
                          "CALSCALE:GREGORIAN", "METHOD:REQUEST"], text[1:5])
        self.assertEqual("BEGIN:VEVENT", text[5])
        self.assertEqual("TRANSP:OPAQUE", text[-3])

    def test_every_line_ends_in_crlf_and_nothing_else_breaks_one(self):
        ics = self.ics()
        self.assertTrue(ics.endswith("\r\n"))
        self.assertEqual(ics.count("\r"), ics.count("\r\n"), "a bare CR")
        self.assertEqual(ics.count("\n"), ics.count("\r\n"), "a bare LF")

    def test_a_newline_in_a_value_is_escaped_not_a_line_break(self):
        self.psy.first_name = "Marivic\r\nATTENDEE:mailto:evil@x.ph\n"
        ics = self.ics()
        self.assertEqual(ics.count("\n"), ics.count("\r\n"))
        self.assertEqual(1, len([t for t in logical(ics) if t.startswith("ATTENDEE")]))

    # -- identity ------------------------------------------------------------

    def test_the_event_id_is_the_one_the_caller_gives_whatever_is_sent(self):
        self.assertEqual(UID, value(self.ics(sequence=1), "UID"))
        self.assertEqual(UID, value(self.ics(sequence=4), "UID"))
        self.assertEqual(UID, value(self.ics(method="CANCEL", sequence=9), "UID"))

    def test_the_event_id_is_never_worked_out_from_the_deployment(self):
        """It used to be a hash of the site's address, so changing the address
        moved it. Now nothing here reads either."""
        before = value(self.ics(), "UID")
        with override_settings(CORS_ALLOWED_ORIGINS=["https://elsewhere.example.test"],
                               SITE_URL="https://another.example.test"):
            after = value(self.ics(), "UID")
        self.assertEqual(before, after)

    def test_a_different_id_is_a_different_event(self):
        self.assertNotEqual(value(self.ics(uid="appointment-7-aaaaaaaaaa@nacc-sys"), "UID"),
                            value(self.ics(uid="appointment-8-aaaaaaaaaa@nacc-sys"), "UID"))

    def test_the_caller_must_give_one(self):
        with self.assertRaises(TypeError):
            build_ics(self.appt, self.psy, method="REQUEST", sequence=1)

    def test_an_id_cannot_start_a_new_line_of_the_file(self):
        ics = self.ics(uid="appointment-7-x@nacc-sys\r\nATTENDEE:mailto:evil@x.ph")
        self.assertEqual(ics.count("\n"), ics.count("\r\n"))
        self.assertEqual(1, len([t for t in logical(ics) if t.startswith("ATTENDEE")]))
        self.assertEqual("appointment-7-x@nacc-sysATTENDEE:mailto:evil@x.ph",
                         value(ics, "UID"))

    # -- the identity invites were sent under before it was stored ------------

    def test_the_old_event_id_is_stable_and_tagged_with_the_deployment(self):
        self.assertRegex(legacy_uid(self.appt), r"^appointment-7-[0-9a-f]{10}@nacc-sys$")
        self.assertEqual(legacy_uid(self.appt), legacy_uid(self.appt))
        other = Appointment(pk=8, child_id=42, start=self.start)
        self.assertNotEqual(legacy_uid(self.appt), legacy_uid(other))
        with override_settings(CORS_ALLOWED_ORIGINS=["https://demo.example.test"]):
            demo = legacy_uid(self.appt)
        with override_settings(CORS_ALLOWED_ORIGINS=[]):
            local = legacy_uid(self.appt)
        with override_settings(CORS_ALLOWED_ORIGINS=["http://localhost:5173"]):
            localhost = legacy_uid(self.appt)
        self.assertEqual(4, len({legacy_uid(self.appt), demo, local, localhost}),
                         "two deployments share an id for one calendar")
        # With no address at all the tag is a fixed word, not a changing value.
        self.assertEqual(f"appointment-7-{hashlib.sha256(b'local').hexdigest()[:10]}@nacc-sys",
                         local)

    def test_the_old_event_id_is_the_cors_tag_even_with_a_site_setting(self):
        """Invites sent before the UID was stored were built from the CORS rule.
        A deployment that sets SITE_URL in the same release must still find
        them, or the first move adds an event beside each old one."""
        expected = f"appointment-7-{hashlib.sha256(ORIGIN.encode()).hexdigest()[:10]}@nacc-sys"
        self.assertEqual(expected, legacy_uid(self.appt))
        with override_settings(SITE_URL="https://another.example.test"):
            self.assertEqual(expected, legacy_uid(self.appt))

    # -- time ----------------------------------------------------------------

    def test_a_manila_morning_is_written_in_utc(self):
        ics = self.ics()
        self.assertEqual("20261021T013000Z", value(ics, "DTSTART"))
        self.assertEqual("20261021T023000Z", value(ics, "DTEND"))
        self.assertEqual("20261019T020000Z", value(ics, "DTSTAMP"))

    def test_the_end_follows_the_duration(self):
        self.appt.duration_minutes = 90
        self.assertEqual("20261021T030000Z", value(self.ics(), "DTEND"))

    def test_a_local_morning_that_is_the_previous_day_in_utc(self):
        self.appt.start = timezone.make_aware(datetime(2026, 10, 22, 7, 0))
        ics = self.ics()
        self.assertEqual("20261021T230000Z", value(ics, "DTSTART"))
        self.assertEqual("20261022T000000Z", value(ics, "DTEND"))

    def test_a_start_already_in_utc_is_the_same_instant(self):
        self.appt.start = datetime(2026, 10, 21, 1, 30, tzinfo=dt_timezone.utc)
        self.assertEqual("20261021T013000Z", value(self.ics(), "DTSTART"))

    # -- method, status, sequence -------------------------------------------

    def test_a_request_is_confirmed_and_a_cancel_is_cancelled(self):
        request = self.ics(method="REQUEST")
        self.assertEqual("REQUEST", value(request, "METHOD"))
        self.assertEqual("CONFIRMED", value(request, "STATUS"))
        cancel = self.ics(method="CANCEL")
        self.assertEqual("CANCEL", value(cancel, "METHOD"))
        self.assertEqual("CANCELLED", value(cancel, "STATUS"))

    def test_any_other_method_is_a_bug_in_the_caller(self):
        for method in ("PUBLISH", "request", "", None):
            with self.assertRaises(ValueError):
                self.ics(method=method)

    def test_the_sequence_is_the_one_given(self):
        for number in (0, 1, 12):
            self.assertEqual(str(number), value(self.ics(sequence=number), "SEQUENCE"))

    # -- the words -----------------------------------------------------------

    def test_the_summary_and_description_name_the_case_and_nobody_else(self):
        ics = self.ics()
        self.assertEqual("NACC session · C-0042", value(ics, "SUMMARY"))
        self.assertEqual(f"Case C-0042. Sign in to NACC SYS for the details: "
                         f"{ORIGIN}/?for={self.psy.pk}", value(ics, "DESCRIPTION"))

    @override_settings(SITE_URL="https://nacc.example.gov.ph")
    def test_the_description_follows_the_site_setting(self):
        self.assertEqual("Case C-0042. Sign in to NACC SYS for the details: "
                         f"https://nacc.example.gov.ph/?for={self.psy.pk}",
                         value(self.ics(), "DESCRIPTION"))

    @override_settings(CORS_ALLOWED_ORIGINS=[])
    def test_with_no_address_known_the_description_has_no_dead_link(self):
        ics = self.ics()
        self.assertEqual("Case C-0042. Sign in to NACC SYS for the details.",
                         value(ics, "DESCRIPTION"))
        # No site, so no `for` either: an id with nothing to open is just noise.
        self.assertNotIn("for=", ics)
        self.assertNotIn("http", ics)

    # -- whose click it is ----------------------------------------------------

    def test_the_link_names_the_attendee_and_no_other_id(self):
        """`for` tells the page a click opens whose open session to borrow
        (design note 2026-10-26-shared-tab-session-design.md), so it must be
        the attendee's id: not the child's (42), not the appointment's (7),
        not whoever booked. The ids are chosen to be told apart on sight."""
        attendee = User(pk=9137, email="lea@t.ph", username="lea@t.ph",
                        first_name="Lea", last_name="Dizon")
        self.assertTrue({42, 7}.isdisjoint({attendee.pk, self.psy.pk}))
        for method in ("REQUEST", "CANCEL"):
            with self.subTest(method=method):
                ics = build_ics(self.appt, attendee, method=method, sequence=1, uid=UID)
                self.assertEqual(
                    f"Case C-0042. Sign in to NACC SYS for the details: {ORIGIN}/?for=9137",
                    value(ics, "DESCRIPTION"))
                # The one and only `for` in the file: the attendee's, and no
                # other id (the child's 42 and the appointment's 7 included).
                self.assertEqual(["9137"], re.findall(r"\bfor=(\d+)", "\n".join(logical(ics))))
        # The same appointment built for somebody else names that person.
        self.assertEqual([str(self.psy.pk)],
                         re.findall(r"\bfor=(\d+)", "\n".join(logical(self.ics()))))

    def test_the_description_stays_correctly_escaped_and_folded_with_the_id_on(self):
        """The link makes DESCRIPTION longer, so it is folded; undone, it is
        whole, and `?` and `=` are not TEXT's special characters, so nothing
        in it is escaped."""
        attendee = User(pk=123456789, email="lea@t.ph", username="lea@t.ph",
                        first_name="Lea", last_name="Dizon")
        with override_settings(SITE_URL="https://nacc.region-one.example.gov.ph"):
            ics = build_ics(self.appt, attendee, method="REQUEST", sequence=1, uid=UID)
        expected = ("Case C-0042. Sign in to NACC SYS for the details: "
                    "https://nacc.region-one.example.gov.ph/?for=123456789")
        self.assertEqual(expected, value(ics, "DESCRIPTION"))
        self.assertNotIn("\\", line(ics, "DESCRIPTION"))
        physical = ics.split("\r\n")[:-1]
        for p in physical:
            self.assertLessEqual(len(p.encode("utf-8")), LINE_LIMIT, p)
        # The description is a line that needed the fold.
        first = next(i for i, p in enumerate(physical) if p.startswith("DESCRIPTION:"))
        self.assertTrue(physical[first + 1].startswith(" "))
        self.assertEqual(ics.count("\n"), ics.count("\r\n"))

    def test_the_location_is_the_first_line_of_the_agency_address_escaped(self):
        profile = AgencyProfile.load()
        profile.office_address = "\n  \nRm 4; 2/F, City Hall \\ Annex\nSan Fernando, La Union"
        profile.save()
        self.assertEqual("Rm 4\\; 2/F\\, City Hall \\\\ Annex", value(self.ics(), "LOCATION"))

    def test_with_no_address_the_location_is_the_office(self):
        self.assertEqual("", AgencyProfile.load().office_address)
        self.assertEqual(FALLBACK_LOCATION, value(self.ics(), "LOCATION"))
        self.assertEqual("RACCO I office", FALLBACK_LOCATION)

    def test_the_organizer_is_the_sender_and_the_attendee_the_psychologist(self):
        ics = self.ics()
        self.assertEqual('ORGANIZER;CN="NACC RACCO1":mailto:from@racco1.gov.ph',
                         line(ics, "ORGANIZER"))
        self.assertEqual('ATTENDEE;CN="Marivic Bulan";ROLE=REQ-PARTICIPANT;'
                         'PARTSTAT=NEEDS-ACTION;RSVP=FALSE:mailto:psy@t.ph',
                         line(ics, "ATTENDEE"))

    def test_an_attendee_with_no_name_is_not_named_by_their_address(self):
        """username is the email address on every way an account is made."""
        self.psy.first_name = self.psy.last_name = ""
        self.psy.username = "psy@t.ph"
        self.assertEqual('ATTENDEE;CN="Attendee";ROLE=REQ-PARTICIPANT;'
                         'PARTSTAT=NEEDS-ACTION;RSVP=FALSE:mailto:psy@t.ph',
                         line(self.ics(), "ATTENDEE"))

    def test_a_name_with_punctuation_stays_inside_its_quotes(self):
        self.psy.first_name, self.psy.last_name = 'Reyes, Maria;', 'Jr.: the "Second"'
        self.assertIn('ATTENDEE;CN="Reyes, Maria; Jr.: the Second";ROLE=REQ-PARTICIPANT;',
                      line(self.ics(), "ATTENDEE"))

    def test_no_child_is_needed_to_build_one(self):
        """The appointment above has a child_id and no child. Anything that
        read the child's name would have had to fetch it."""
        self.assertFalse(Appointment._meta.get_field("child").is_cached(self.appt))
        self.ics()
        self.assertFalse(Appointment._meta.get_field("child").is_cached(self.appt))

    # -- the 75-octet limit ---------------------------------------------------

    def test_no_line_is_longer_than_75_octets_even_with_a_long_name_full_of_accents(self):
        # As long as a name is printed (accounts.display.NAME_LIMIT is 60): a
        # longer one is cut before it gets here. These 45 characters are 71
        # octets, with four-octet emoji, so the line still has to be folded.
        self.psy.first_name = "María Concepción Peñaflorida 日本語の名前 😀😀😀"
        self.psy.last_name = "Ñandú"
        AgencyProfile.objects.create(
            office_address="2nd Floor, Provincial Capitol Annex, Quezon Avenue, "
                           "Barangay Catbangen, City of San Fernando, La Union ñññññ")
        ics = self.ics()
        for physical in ics.split("\r\n")[:-1]:
            self.assertLessEqual(len(physical.encode("utf-8")), LINE_LIMIT, physical)
        folded = [p for p in ics.split("\r\n") if p.startswith(" ")]
        self.assertTrue(folded, "the long name should have needed a fold")
        # Undone, the name is whole.
        self.assertIn(f'CN="{" ".join(self.psy.fullname.split())}"', line(ics, "ATTENDEE"))
        self.assertIn("Ñandú", line(ics, "ATTENDEE"))
        self.assertIn("日本語の名前 😀😀😀", line(ics, "ATTENDEE"))

    def test_a_name_longer_than_a_name_is_cut_in_the_attendee_line(self):
        self.psy.first_name, self.psy.last_name = "Maria " * 50, "Santos"
        attendee = line(self.ics(), "ATTENDEE")
        name = attendee.split('CN="', 1)[1].split('"', 1)[0]
        self.assertEqual(NAME_LIMIT, len(name))
        self.assertTrue(name.endswith("\u2026"))

    def test_the_middle_dot_is_two_octets_and_counts_as_two(self):
        ics = self.ics()
        summary = [p for p in ics.split("\r\n") if p.startswith("SUMMARY")][0]
        self.assertEqual("SUMMARY:NACC session · C-0042", summary)
        self.assertEqual(len(summary) + 1, len(summary.encode("utf-8")))


class FoldTest(SimpleTestCase):
    def test_a_short_line_is_left_alone(self):
        self.assertEqual(["SUMMARY:short"], fold("SUMMARY:short"))
        exactly = "X" * LINE_LIMIT
        self.assertEqual([exactly], fold(exactly))

    def test_a_long_ascii_line_breaks_at_75_then_74_with_a_leading_space(self):
        parts = fold("X" * 200)
        # 75, then " " + 74, then " " + the remaining 51.
        self.assertEqual([75, 75, 52], [len(p) for p in parts])
        self.assertTrue(all(p.startswith(" ") for p in parts[1:]))
        self.assertFalse(parts[0].startswith(" "))
        self.assertEqual("X" * 200, parts[0] + "".join(p[1:] for p in parts[1:]))

    def test_a_cut_never_lands_inside_a_multibyte_character(self):
        for char in ("·", "ñ", "日", "😀"):
            text = "NAME:" + char * 90
            parts = fold(text)
            for part in parts:
                self.assertLessEqual(len(part.encode("utf-8")), LINE_LIMIT, (char, part))
            self.assertEqual(text, parts[0] + "".join(p[1:] for p in parts[1:]))

    def test_the_boundary_character_moves_to_the_next_line_whole(self):
        # 73 ASCII octets then a 4-octet emoji would be 77: it goes down whole.
        parts = fold("A" * 73 + "😀" + "B")
        self.assertEqual("A" * 73, parts[0])
        self.assertEqual(" 😀B", parts[1])


class TextEscapingTest(SimpleTestCase):
    def test_backslash_semicolon_comma_and_newline(self):
        self.assertEqual("a\\\\b\\;c\\,d\\ne", _text("a\\b;c,d\ne"))
        self.assertEqual("one\\ntwo\\nthree", _text("one\r\ntwo\rthree"))

    def test_the_backslash_is_escaped_first_so_later_escapes_are_not_doubled(self):
        self.assertEqual("\\\\\\;", _text("\\;"))

    def test_other_control_characters_are_dropped_and_tab_is_kept(self):
        self.assertEqual("ab\tc", _text("a\x00b\x07\tc\x7f"))

    def test_invisible_formatting_characters_are_dropped_from_a_name(self):
        """A right-to-left override can make a name display reversed; a
        zero-width space makes two different names look the same."""
        self.assertEqual('"BidiRTL ZeroWidth"', _cn("Bidi\u202eRTL Zero\u200bWidth"))
        self.assertEqual("ab", _text("a\u200fb\ufeff"))
        # Letters with accents and the emoji of the long-name test are not
        # formatting characters and stay.
        self.assertEqual('"Peña 😀"', _cn("Peña 😀"))

    def test_a_cn_is_quoted_with_its_double_quotes_removed(self):
        self.assertEqual('"Ana Cruz"', _cn("Ana  Cruz"))
        self.assertEqual('"Ana Cruz"', _cn('Ana "Cruz"'))
        self.assertEqual('"Ana Cruz"', _cn("Ana\r\nCruz"))
        self.assertEqual('"Cruz, Ana; Jr."', _cn("Cruz, Ana; Jr."))
