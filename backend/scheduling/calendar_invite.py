"""The calendar invite attached to a psychologist's session emails (RFC 5545 and
the iTIP methods of RFC 5546; design: docs/superpowers/specs/
2026-10-21-email-notifications-design.md, "Sessions and the calendar invite").

Pure text in, text out: nothing here touches the database except the one read
of the agency's address, so the format can be tested line by line and the
bookkeeping (CalendarInvite's SEQUENCE) stays in session_mail.py, where it is
counted only for an invite that is really queued.

What an invite may carry is what an email may carry (accounts/
email_notifications.py, rule 1): the case reference, the times, the
psychologist's name and the agency's own address. Never the child's name, the
session's purpose or its notes - Google keeps a copy of an invite for good.

Three details are where hand-made calendar files usually break, and each has a
test:

* CRLF ends every line, including the last. A bare LF is read by some clients
  and refused by others.
* TEXT values escape backslash, semicolon, comma and newline. A comma in the
  office address would otherwise end the LOCATION early.
* No physical line is longer than 75 OCTETS. Octets, not characters: the "·"
  in the summary is two bytes, and a name written in Filipino or Spanish
  spelling is more. A line is folded between characters, never inside one.
"""
import hashlib
import unicodedata
from datetime import timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from accounts.display import entered_name
from accounts.models import AgencyProfile
from children.notifications import origin_from_cors, sign_in_url
from scheduling.booking import ends_at
from scheduling.visibility import case_ref

PRODID = "-//NACC SYS//Appointments//EN"
METHODS = {"REQUEST": "CONFIRMED", "CANCEL": "CANCELLED"}
FALLBACK_LOCATION = "RACCO I office"

# RFC 5545 3.1: lines SHOULD NOT be longer than 75 octets, CRLF excluded.
LINE_LIMIT = 75


def legacy_uid(appointment):
    """The identity the first invites were sent under, before the UID was
    stored on `CalendarInvite`.

    Stable for one appointment, so a move or a cancellation finds the event it
    is about; the tag is the first ten hex characters of the SHA-256 of the
    deployment's address (or "local"), so the demo, the live system and a
    developer's copy - which all number their appointments from 1 - never
    shared an id for one person's calendar.

    It follows the site's address, which is why it is no longer used for a new
    invite: changing the address would give every booked session a second
    event. It is kept for one thing, session_mail.py: an invite sent before the
    column existed is in somebody's calendar under THIS value, so the next send
    writes it into the row and goes on using it.

    The address is the one those invites were built from, `origin_from_cors`,
    and not `sign_in_url`: a deployment that sets `SITE_URL` in the same
    release would otherwise work out a different tag than the one its calendars
    hold, and the move it is meant to protect would add an event beside the
    old one."""
    tag = hashlib.sha256((origin_from_cors() or "local").encode("utf-8")).hexdigest()[:10]
    return f"appointment-{appointment.pk}-{tag}@nacc-sys"


def _plain(value):
    """`value` without control characters other than newline and tab, which a
    name typed into a form could otherwise use to start a new line of the
    file, and without the invisible formatting ones (category Cf:
    right-to-left overrides, zero-width characters), which let a name display
    other than it is written."""
    return "".join(ch for ch in str(value)
                   if ch in "\r\n\t"
                   or unicodedata.category(ch) not in ("Cc", "Cf"))


def _text(value):
    """A TEXT value (RFC 5545 3.3.11): backslash first, or the escapes added
    after it would be doubled."""
    value = _plain(value).replace("\r\n", "\n").replace("\r", "\n")
    return (value.replace("\\", "\\\\").replace(";", "\\;")
                 .replace(",", "\\,").replace("\n", "\\n"))


def _cn(name):
    """A CN parameter value: always quoted, so a name with a comma, a colon or
    a semicolon in it cannot end the parameter early. A quoted value cannot
    hold a double quote, so one is dropped."""
    name = " ".join(_plain(name).replace('"', "").split())
    return f'"{name}"'


def _mailto(address):
    return "mailto:" + "".join(_plain(address).split())


def _utc(dt):
    """"20261021T013000Z". A naive value is read as local time, the way the
    rest of the system reads one."""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _location():
    """The first line of the agency's address - an invite's LOCATION is a
    place on a map, not a letterhead."""
    for line in AgencyProfile.load().office_address.splitlines():
        if line.strip():
            return line.strip()
    return FALLBACK_LOCATION


def fold(line):
    """`line` as one or more physical lines of at most 75 octets (RFC 5545
    3.1): a break, then a single space, then the rest. The space counts toward
    the continuation's 75. Cut between characters, because the cut must not
    land inside a multibyte one."""
    if len(line.encode("utf-8")) <= LINE_LIMIT:
        return [line]
    physical, current, size = [], "", 0
    for ch in line:
        width = len(ch.encode("utf-8"))
        if size + width > LINE_LIMIT:
            physical.append(current)
            current, size = " ", 1
        current += ch
        size += width
    physical.append(current)
    return physical


def build_ics(appointment, attendee, *, method, sequence, uid):
    """The .ics file for `attendee`'s copy of `appointment`.

    `method` is "REQUEST" (a booking, or a move: the same UID with a higher
    `sequence` replaces the event) or "CANCEL" (removes it). `sequence` and
    `uid` are the caller's, session_mail.py, which is the one place that knows
    how many invites this person has really been sent and which identity the
    appointment was given. Nothing here works the UID out, so nothing about
    the deployment can change it.

    RSVP is FALSE because a reply would land in the agency's own inbox, which
    nobody reads."""
    if method not in METHODS:
        raise ValueError(f"A calendar invite is a REQUEST or a CANCEL, not {method!r}.")
    ref = case_ref(appointment.child_id)
    url = sign_in_url()
    details = f"Case {ref}. Sign in to NACC SYS for the details" + (f": {url}" if url else ".")
    organizer = _cn(settings.BREVO_SENDER_NAME)

    lines = [
        "BEGIN:VCALENDAR",
        f"PRODID:{PRODID}",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{''.join(_plain(uid).split())}",
        f"DTSTAMP:{_utc(timezone.now())}",
        f"DTSTART:{_utc(appointment.start)}",
        f"DTEND:{_utc(ends_at(appointment.start, appointment.duration_minutes))}",
        f"SEQUENCE:{int(sequence)}",
        f"STATUS:{METHODS[method]}",
        f"SUMMARY:{_text(f'NACC session · {ref}')}",
        f"DESCRIPTION:{_text(details)}",
        f"LOCATION:{_text(_location())}",
        f"ORGANIZER;CN={organizer}:{_mailto(settings.BREVO_SENDER_EMAIL)}",
        f"ATTENDEE;CN={_cn(entered_name(attendee, 'Attendee'))};ROLE=REQ-PARTICIPANT;"
        f"PARTSTAT=NEEDS-ACTION;RSVP=FALSE:{_mailto(attendee.email)}",
        "TRANSP:OPAQUE",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "".join(f"{physical}\r\n" for line in lines for physical in fold(line))
