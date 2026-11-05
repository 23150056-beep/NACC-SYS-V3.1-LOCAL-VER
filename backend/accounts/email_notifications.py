"""The emails this system sends to psychologists and social workers about
their work (owner's request, 21 Oct 2026). Design:
docs/superpowers/specs/2026-10-21-email-notifications-design.md.

Everything goes through the sender that already mails passwords and sign-up
codes (children/notifications.py: Brevo, `BREVO_API_KEY`). No new provider. The
one setting they add is `SITE_URL`, the address every email's button and plain
address point at (23 Oct 2026; children/notifications.py `sign_in_url`). The
text messages are a separate channel and are untouched
(accounts/sms_notifications.py).

Eight rules every email here keeps:

1. NO CHILD'S NAME, EVER. Not in the subject, the page, the plain-text part or
   a calendar invite - and not a birth date, case type, address, custodian,
   decline reason or anything clinical either. Brevo is a processor outside
   the agency's agreements. What an email MAY carry is a case reference
   ("C-0042"), session dates and times, staff names and counts. The same rule
   as the texts, applied to a channel that keeps a copy.
2. A NUDGE TO SIGN IN, never the thing itself.
3. NEVER FOR YOUR OWN ACTION. If you did it, you are not emailed about it.
4. ONLY TO SOMEONE WHO CAN ACT ON IT: an active account with an address, in
   the email's role, who has not switched it off (`wants`).
5. AFTER THE COMMIT, OFF THE REQUEST THREAD (`_queue`). A rolled-back save
   mails nothing, and a slow Brevo never holds a save open.
6. NEVER RAISES. A failure is logged and the save stands.
7. WITH NO `BREVO_API_KEY` (the local copy) nothing is sent: one line is
   logged, the way the sign-up code and the SMS console provider do it.
8. BOUNDED PER ACTOR (25 Oct 2026). One account's actions can cause only so
   many of these emails (`EMAIL_LIMIT_*` in settings). Past a limit the email
   is held, the colleague is told that more updates are waiting (once an hour,
   and at most `OVERFLOW_PER_DAY` times a day), and the ISA is told once an
   hour that the account reached it (section 4, `notify`).

The catalogue (section 1) is its own list, fired from the domain functions
(section 5) and never from `log_activity`: the bell addresses most lines to the
psychologist even when they did it themselves, and every line names the child.
"""
import base64
import functools
import html
import logging
import re
import threading
import time
from collections import namedtuple
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from accounts.display import display_name, entered_name, one_line
from accounts.models import EmailOptOut, EmailSendRecord, Role
from activity.models import ActivityLog
from activity.services import log_activity
from children import notifications
from config.clock import clock

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 1. The catalogue
# --------------------------------------------------------------------------

Kind = namedtuple("Kind", "key role label description")

# Ordered as My Profile shows them. The key is stored in EmailOptOut rows, so
# **a key is never renamed or reused**: a renamed one would leave every opt-out
# pointing at nothing, and the people who switched it off would be emailed
# again. test_email_notifications.py pins the list.
KINDS = (
    Kind("case_request", Role.PSYCHOLOGIST, "Asked to take a case",
         "When a social worker or the ISA asks you to take a case, so you can "
         "accept or decline it."),
    Kind("request_withdrawn", Role.PSYCHOLOGIST, "A request is withdrawn",
         "When a case you were asked to take is withdrawn before you answered, "
         "or goes with a duplicate record."),
    Kind("my_calendar", Role.PSYCHOLOGIST, "Sessions on my calendar",
         "When someone else books, moves or cancels a session on your "
         "calendar. Each email carries a calendar invite, and turning it off "
         "also stops updates to sessions already added."),
    Kind("case_accepted", Role.STAFF, "A psychologist accepts",
         "When a psychologist accepts a case you asked them to take. If they book "
         "its first session as they accept it, the same email gives the time."),
    Kind("case_declined", Role.STAFF, "A psychologist declines",
         "When a psychologist declines a case you asked them to take. Their "
         "reason is never in the email; you read it on the record."),
    Kind("records_transferred", Role.STAFF, "Records moved to me",
         "When the ISA uses Transfer records to move records to you from "
         "another social worker."),
    Kind("case_taken_over", Role.STAFF, "My closed case is taken over",
         "When another social worker reopens one of your closed cases at "
         "intake and it moves to their records."),
    Kind("child_sessions", Role.STAFF, "Sessions for my children",
         "When someone else books, moves or cancels a session for a child in "
         "your records. A psychologist's first session, booked as they accept "
         "the case, comes in the acceptance email instead."),
)
KIND = {k.key: k for k in KINDS}


def kinds_for(user):
    """The catalogue entries this person's role is sent, in the order the
    profile card shows them. An administrator has none: the ISA's emails were
    left out on purpose (design note, "Not done, suggested")."""
    role = getattr(getattr(user, "role", None), "role_name", None)
    return [k for k in KINDS if k.role == role]


def wants(user, kind_key):
    """Whether `user` should be emailed `kind_key` at all. The one place rule
    4 is decided, so a trigger cannot send to somebody the rule excludes.

    An unknown key raises KeyError: that is a typo in a caller, and `notify`
    logs it rather than letting it reach a save."""
    kind = KIND[kind_key]
    if user is None or not user.is_active or not getattr(user, "email", ""):
        return False
    if getattr(getattr(user, "role", None), "role_name", None) != kind.role:
        return False
    return not EmailOptOut.objects.filter(user=user, kind=kind_key).exists()


def sending():
    """Whether a mail service is set up. The local copy has none, and every
    email is then logged instead of sent."""
    return bool(settings.BREVO_API_KEY)


# --------------------------------------------------------------------------
# 2. Wording helpers
# --------------------------------------------------------------------------

FOOTER = ("You get this email because of your notification settings in NACC SYS "
          "(My Profile, Email notifications). No case details are included. "
          "Automated message; replies are not monitored.")

# Under every button. It tells the reader what an email from NACC SYS never
# does, and what to do when they are not sure this one is ours.
#
# It does NOT ask them to compare the address printed above it with the one
# they know. That was the first wording, and it gave false comfort: the printed
# address is only text, it is independent of where the button goes, and a
# look-alike email can print the genuine address, carry this very sentence and
# point its button somewhere else (23 Oct 2026, security review). The check
# that cannot be copied is the one that does not use the email at all: open
# NACC SYS from the bookmark or the address already known. The address printed
# under the button is there for the person who has not got one yet.
#
# Nor does it say what the button opens. It opens the screen in a tab of this
# browser that is already signed in as the person the email is for, and the
# sign-in page otherwise (the link names whose session to look for: `for` in
# `render`), and the label above it ("Open the schedule") says where it is
# going. "Nothing in this email signs you in" stays true all the same: `for`
# signs nobody in, it only says whose open session to borrow.
SAFETY = ("NACC SYS never asks for your password or a code by email, and nothing "
          "in this email signs you in. Not sure it is from us? Do not use the "
          "button: open NACC SYS from your bookmark or the address you already "
          "know, and tell the ISA.")

# The "Action needed" chip. Dark amber on its pale tint: 6.37:1, where WCAG AA
# asks 4.5:1 for text this size (the brand amber, #b45309, is 4.51 and has no
# room for a mail client that renders colours slightly differently).
_AMBER_TEXT = "#92400e"
_AMBER_TINT = "#fef3c7"

# How the preview line is hidden. display:none does it in every client that
# matters; the rest is for one that drops it (a forwarding gateway, a reader
# that shows the raw HTML): collapse the box to a point and set the text in the
# page's own background colour, so the sentence is not seen there either.
_PREHEADER_STYLE = ("display:none;font-size:1px;line-height:1px;max-height:0;"
                    "max-width:0;opacity:0;overflow:hidden;mso-hide:all;")

# Wrapping for an address, which is one unbreakable word. `anywhere` breaks it
# only where it would otherwise run past the edge (`break-all` cut a short one
# in the middle on a phone), and the older `word-wrap` is for a client that
# does not know `anywhere`. Needed on every line that prints the site.
_WRAP = "word-wrap:break-word;overflow-wrap:anywhere;"

# The standard padding after hidden preview text: invisible characters that
# fill the rest of the line Gmail shows, so it does not go on into the body
# ("Open NACC SYS ... Hello ...") and make the preview read as a jumble.
_PREHEADER_PAD = "&zwnj;&nbsp;" * 90


def when(dt):
    """"Wed 21 Oct 2026, 9:30 AM", in local time. Built by hand: %-d and %-I
    are glibc extensions and raise on Windows, where the local copy runs."""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    local = timezone.localtime(dt)
    return f"{local:%a} {local.day} {local:%b} {local.year}, {clock(local)}"


def span(start, duration_minutes):
    """"Wed 21 Oct 2026, 9:30 AM to 10:30 AM", local time. A session that runs
    past midnight - possible only on a psychologist's own calendar - names the
    day it ends as well. Shared by the session emails (scheduling/session_mail.py)
    and the acceptance email, which both print a session's time."""
    from scheduling.booking import ends_at
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    end = timezone.localtime(ends_at(start, duration_minutes))
    local = timezone.localtime(start)
    tail = clock(end) if end.date() == local.date() else when(end)
    return f"{when(start)} to {tail}"


# A time and its AM/PM: "10:00 AM". Joined with a no-break space in the page so
# a narrow screen wraps before the time and never leaves "AM" alone on a line.
# Only the page: the text part and the invite keep the ordinary space.
_TIME_AND_MERIDIEM = re.compile(r"(\d:\d\d) (AM|PM)\b")


def _whole_times(escaped):
    return _TIME_AND_MERIDIEM.sub(r"\1&nbsp;\2", escaped)


# What scheduling.visibility.case_ref writes: "C-0042".
_CASE_REF = re.compile(r"C-\d{4,}")


def _ref(child_or_ref):
    """The case reference of a Child, or a reference already written out: a
    removed duplicate has no row left to ask.

    A string is accepted only if it IS a reference. This is the one place a
    caller's text goes straight into a subject and a body, so a caller that
    handed it a name would put the name in the email; refusing here means the
    mistake mails nothing (`_never_raises` logs it) rather than leaking."""
    if isinstance(child_or_ref, str):
        if not _CASE_REF.fullmatch(child_or_ref):
            raise ValueError("A case reference looks like C-0042.")
        return child_or_ref
    from scheduling.visibility import case_ref
    return case_ref(child_or_ref)


def _plural(n, noun):
    return f"{n} {noun}{'' if n == 1 else 's'}"


# --------------------------------------------------------------------------
# 3. The message
# --------------------------------------------------------------------------

def render(heading, paragraphs, facts=(), button=None, *, preview=None, chip=None,
           greeting=None, steps=(), subject=None, recipient=None):
    """(html, text) for one email: the temporary password email's branded
    layout (children/notifications.py) - the same palette and header, a table
    layout with inline styles because Gmail strips <style> - and a plain-text
    part, because a message with no text part is likelier to be filtered.

    `facts` is (label, value) rows. `button` is (label, path): it points at
    sign_in_url() plus the path, and is left out when no address is known
    rather than printing a dead link. Every value is escaped.

    `recipient` is the user the email goes to. The button's link then ends in
    `for=<their id>`, which tells the page that opens to borrow the session of
    THAT person from a tab of this browser that is signed in, and nobody else's
    (design note 2026-10-26-shared-tab-session-design.md). Two people signed in
    in one browser each get their own session from their own email, and a
    psychologist clicking theirs on a PC where a social worker is signed in
    meets the sign-in page. The id is not a secret and signs nobody in: it can
    only narrow what a tab borrows. It rides on the button and on the footer's
    address of the email switches, which a mail client also turns into a link;
    the plain address under the button stays the bare site.

    What every email gets, and why it is here:

    * **Preview text**, the hidden first line a mail list shows beside the
      subject (`preview`, else the first paragraph). A text-only reader sees
      the heading first, so the text part has nothing to mirror.
    * **The site's address in words under the button**, as text and not a
      link. A mail service's click tracking rewrites every link into its own
      address, so the button's real target cannot be read; the plain address
      can be, and is the bare site, never the deep path, so a person who has
      no bookmark yet can type it. It is not a check on the email: text proves
      nothing about where the button goes (`SAFETY`).
    * **A safety line** (`SAFETY`) next to it, and **a way to the switches** in
      the footer, as text with the address for the same reason. Both need an
      address, so both go when none is known; the safety line also needs the
      button it talks about.

    The optional parts are for the emails that ask something of the reader
    (`case_request`) and leave every other email as it was: `chip` is a short
    "Action needed" label over the heading, `greeting` a "Hello <name>," line
    before the first paragraph, and `steps` numbered instructions after the
    facts. `subject` becomes the page's <title>, else the heading does."""
    n = notifications
    esc = html.escape
    if button and not button[1].startswith("/"):
        # The path is appended to the site's address. One that does not begin
        # with "/" would be read as part of the host: "@elsewhere.example" gives
        # https://site.example@elsewhere.example. Every caller passes a constant
        # today; this is for the next one, and `notify` logs it and sends nothing.
        raise ValueError("A button's path begins with /.")
    site = n.sign_in_url()
    link = f"{site}{button[1]}" if button and site else ""
    whose = getattr(recipient, "pk", None)
    if link and whose is not None:
        # A path that already has a query takes `for` after an "&", or the
        # second "?" would turn the rest into part of the first value.
        link += f"{'&' if '?' in button[1] else '?'}for={whose}"
    preview = (paragraphs[0] if paragraphs else "") if preview is None else preview

    preheader = (
        f'<div style="{_PREHEADER_STYLE}color:{n._PAPER};">'
        f'{esc(preview)}{_PREHEADER_PAD}</div>'
    ) if preview else ""

    chip_row = (
        f'<div style="padding-bottom:12px;"><span style="display:inline-block;'
        f'background:{_AMBER_TINT};color:{_AMBER_TEXT};font-size:11px;'
        f'font-weight:700;letter-spacing:.09em;text-transform:uppercase;'
        f'padding:4px 10px;border-radius:999px;">{esc(chip)}</span></div>'
    ) if chip else ""

    greeting_row = (
        f'<p style="margin:14px 0 0;font-size:15px;line-height:1.6;'
        f'color:{n._INK};">{esc(greeting)}</p>'
    ) if greeting else ""

    paragraph_rows = "".join(
        f'<p style="margin:{14 if i == 0 and not greeting else 10}px 0 0;'
        f'font-size:15px;line-height:1.6;'
        f'color:{n._INK if i == 0 else n._MUTED};">{esc(p)}</p>'
        for i, p in enumerate(paragraphs))

    fact_rows = "".join(
        f'<tr><td style="padding:3px 14px 3px 0;font-size:11px;'
        f'letter-spacing:.09em;font-weight:700;color:{n._BRAND_DARK};'
        f'text-transform:uppercase;white-space:nowrap;">{esc(label)}</td>'
        f'<td style="padding:3px 0;font-size:15px;color:{n._INK};">'
        f'{_whole_times(esc(value))}</td></tr>'
        for label, value in facts)
    fact_block = (
        f'<tr><td style="padding:20px 32px 0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'style="width:100%;background:{n._TINT};border:1px solid #d5dcfa;'
        f'border-radius:10px;padding:10px 16px;">{fact_rows}</table>'
        f'</td></tr>'
    ) if fact_rows else ""

    step_items = "".join(f"<li>{esc(step)}</li>" for step in steps)
    step_block = (
        f'<tr><td style="padding:22px 32px 0;">'
        f'<div style="font-size:14px;font-weight:700;color:{n._INK};">'
        f'What to do next</div>'
        f'<ol style="margin:9px 0 0;padding-left:20px;font-size:14px;'
        f'line-height:1.75;color:{n._MUTED};">{step_items}</ol></td></tr>'
    ) if step_items else ""

    button_row = (
        f'<tr><td align="center" style="padding:26px 32px 0;">'
        f'<a href="{esc(link)}" style="display:inline-block;background:{n._BRAND};'
        f'color:#ffffff;text-decoration:none;font-weight:700;font-size:15px;'
        f'padding:13px 30px;border-radius:8px;">{esc(button[0])}</a>'
        f'</td></tr>'
        # Text, not an <a>: see the note above on click tracking.
        f'<tr><td align="center" style="padding:14px 32px 0;font-size:13px;'
        f'line-height:1.6;color:{n._MUTED};">Or open NACC SYS at '
        f'<span style="display:block;margin-top:2px;color:{n._INK};'
        f'font-weight:600;{_WRAP}">{esc(site)}</span></td></tr>'
        f'<tr><td style="padding:20px 32px 0;">'
        f'<div style="background:{n._PAPER};border-left:3px solid {n._BRAND};'
        f'border-radius:0 8px 8px 0;padding:13px 16px;font-size:13px;'
        f'line-height:1.65;color:{n._MUTED};">{esc(SAFETY)}</div></td></tr>'
    ) if link else ""

    # The footer's address names its recipient like the button does. A mail
    # client turns printed text into a link, and opened on a PC where a
    # colleague is signed in, a bare one would borrow the colleague's session
    # (the same rule as the "Without `for`" case in the design note). It has no
    # query of its own, so `for` always follows a "?".
    profile_link = f"{site}/profile" + (f"?for={whose}" if whose is not None else "")
    profile = (f"Choose which emails you get: My Profile, Email notifications - "
               f"{profile_link}") if site else ""
    profile_row = (
        f'<div style="padding-top:6px;{_WRAP}">{esc(profile)}</div>'
    ) if profile else ""

    html_body = (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(subject or heading)}</title></head>'
        f'<body style="margin:0;padding:0;background:{n._PAPER};">'
        f'{preheader}'
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="background:{n._PAPER};padding:24px 12px;">'
        f'<tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="max-width:560px;background:#ffffff;'
        f'border:1px solid {n._BORDER};border-radius:14px;overflow:hidden;'
        f'font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif;">'

        # Header
        f'<tr><td style="background:{n._BRAND};padding:22px 32px;">'
        f'<div style="color:#ffffff;font-size:17px;font-weight:700;'
        f'line-height:1.3;">NACC &ndash; RACCO 1</div>'
        f'<div style="color:#c3cbf5;font-size:12px;padding-top:3px;">'
        f'National Authority for Child Care</div></td></tr>'

        # What happened
        f'<tr><td style="padding:30px 32px 0;">'
        f'{chip_row}'
        f'<div style="font-size:19px;font-weight:700;color:{n._INK};">'
        f'{esc(heading)}</div>'
        f'{greeting_row}'
        f'{paragraph_rows}</td></tr>'

        f'{fact_block}'
        f'{step_block}'
        f'{button_row}'
        f'<tr><td style="padding:26px 32px 0;"></td></tr>'

        # Footer
        f'<tr><td style="background:{n._PAPER};border-top:1px solid {n._BORDER};'
        f'padding:15px 32px;font-size:11.5px;line-height:1.6;color:{n._MUTED};">'
        f'{esc(FOOTER)}{profile_row}</td></tr>'

        f'</table></td></tr></table></body></html>'
    )

    text_parts = [f"{chip}\n{heading}" if chip else heading]
    if greeting:
        text_parts.append(greeting)
    text_parts.extend(paragraphs)
    if facts:
        text_parts.append("\n".join(f"{label}: {value}" for label, value in facts))
    if steps:
        text_parts.append("What to do next:\n" + "\n".join(
            f"{i}. {step}" for i, step in enumerate(steps, 1)))
    if link:
        text_parts.append(f"{button[0]}: {link}")
        text_parts.append(f"Or open NACC SYS at {site}")
        text_parts.append(SAFETY)
    text_parts.append(f"{FOOTER}\n{profile}" if profile else FOOTER)
    return html_body, "\n\n".join(text_parts)


# --------------------------------------------------------------------------
# 4. Sending
# --------------------------------------------------------------------------

# What `_queue` calls its threads, so `wait_for_queued` can find them.
_THREAD_NAME = "nacc-email"


def _queue(payload, description):
    """Hand one message to Brevo after the surrounding transaction commits, on
    a daemon thread. on_commit so a rolled-back save cannot mail about
    something that never happened; the thread so a slow Brevo cannot hold the
    response open. The one seam tests patch to read what would have gone."""
    transaction.on_commit(
        lambda: threading.Thread(
            target=notifications._post, args=(payload, description),
            name=_THREAD_NAME, daemon=True).start(),
        # Inside an explicit atomic block this runs at the COMMIT, outside
        # notify's try/except: a thread that cannot start would otherwise
        # surface as a 500 after the save was already made (and skip the other
        # callbacks queued with it). Robust callbacks are logged and dropped.
        robust=True)


def wait_for_queued(timeout=20):
    """Wait, up to `timeout` seconds, for the emails `_queue` has started.

    For a management command only. A daemon thread dies with its process, and a
    command exits the moment it has queued, so the message it queued would be
    lost with it - the same reason session reminders are sent before their
    command returns (see "Text messages" in CLAUDE.md). A long-lived server
    never needs this."""
    deadline = time.monotonic() + timeout
    for thread in threading.enumerate():
        if thread.name == _THREAD_NAME and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))


# --------------------------------------------------------------------------
# The limit on what one account can cause (design note, "Round four")
# --------------------------------------------------------------------------
#
# Every email here is caused by somebody's action, and picking one psychologist
# and then another on a record, back and forth, mailed "asked" and "withdrawn"
# to both on every save. The budget belongs to the ACTOR (the `by` of the
# domain function), not to the recipient: a colleague who is emailed a lot has
# done nothing wrong, and the one who caused it is who should be slowed.

# The notice that more updates are waiting. Its `EmailSendRecord.kind`; it is not
# in the catalogue, has no switch of its own (it goes to someone who has the
# held email's kind on) and is not counted against the limits, so it cannot
# hold itself back.
OVERFLOW = "overflow"

_HOUR = timedelta(hours=1)
_DAY = timedelta(hours=24)
# The most notices one actor can cause one colleague in 24 hours. The notice is
# not counted by the three limits, and an account held by the DAILY limit stays
# held for most of a day, so with only "once an hour" a stolen account that
# reached a dozen colleagues cost 150 emails plus a notice to each of them every
# hour: 428 in a day, measured. A few reminders are worth sending, because a
# colleague emailed again after the first hold has a held email behind it that
# the notice is for; one an hour is just the account's volume again. So what an
# actor can cause in 24 hours is the daily limit plus this many a colleague.
OVERFLOW_PER_DAY = 3
# How long a send record is kept: the widest window is 24 hours, and the extra
# hour is slack for a clock that ran a little behind. See EmailSendRecord.
SEND_RECORD_KEEP = timedelta(hours=25)


def _limit(setting):
    """The number in `setting`, or None when that kind of limit is off: 0 and
    None both mean no limit."""
    value = getattr(settings, setting, None)
    return value if value and value > 0 else None


def _over_limit(by, user, now):
    """Whether `by` has used up any of its budgets for an email to `user`: the
    last hour to this one colleague, the last hour to everyone, the last 24
    hours to everyone. A count AT a limit is over it: the limit is how many may
    be sent, and this email would be one more. Rolling windows, so a window
    ends an hour after the emails in it, not at the top of the hour.

    The notice (`OVERFLOW`) is not counted. One query for all three counts."""
    per_colleague = _limit("EMAIL_LIMIT_PER_RECIPIENT_HOUR")
    per_hour = _limit("EMAIL_LIMIT_PER_HOUR")
    per_day = _limit("EMAIL_LIMIT_PER_DAY")
    if not (per_colleague or per_hour or per_day):
        return False
    last_hour = Q(created_at__gt=now - _HOUR)
    sent = (EmailSendRecord.objects
            .filter(actor=by, created_at__gt=now - _DAY).exclude(kind=OVERFLOW)
            .aggregate(day=Count("pk"),
                       hour=Count("pk", filter=last_hour),
                       colleague=Count("pk", filter=last_hour & Q(recipient=user))))
    return any(limit and sent[window] >= limit
               for window, limit in (("colleague", per_colleague),
                                     ("hour", per_hour), ("day", per_day)))


def _record(by, user, kind_key, now):
    """Count one email against `by`. Written just before the email is queued (or
    logged, on a copy with no mail service, so the limit can be watched working
    there). No actor, no row: such an email is not limited. `created_at` is
    given and not left to its default, which is the real clock whatever a test
    has pinned."""
    if by is not None:
        EmailSendRecord.objects.create(
            actor=by, recipient=user, kind=kind_key, created_at=now)


def _payload(user, subject, html_body, text_body):
    name = entered_name(user)
    return {
        "sender": {
            "name": settings.BREVO_SENDER_NAME,
            "email": settings.BREVO_SENDER_EMAIL,
        },
        "to": [{"email": user.email, **({"name": name} if name else {})}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
    }


def _send_overflow_notice(by, user, now):
    """Tell `user`, at most once an hour and `OVERFLOW_PER_DAY` times in 24 hours
    per actor, that `by` has caused more emails than are sent and the rest are
    held. A held email can be the one that would have corrected an earlier one
    (a request "asked" after "withdrawn"), and without this the earlier email
    would stand as the truth; the notice sends the reader to the Dashboard and
    the bell, which hold everything.

    It names the actor and nothing else: no case reference and no kind of
    change, so it is as safe to mail as any email here. It does not promise when
    the emails start again: the two hourly limits lift within the hour and the
    daily one can hold for most of a day, and which of them is holding is not
    something the colleague needs to know.

    The hour and the day are counted from the notices already recorded, so two
    held emails caused in the same instant can both pass (the limits themselves
    are soft in the same way, and the design note accepts it).

    Not checked against `wants` here: a held email is one the recipient has
    switched on, and `notify` checked that before it got this far."""
    earlier = list(EmailSendRecord.objects.filter(
        actor=by, recipient=user, kind=OVERFLOW, created_at__gt=now - _DAY
    ).values_list("created_at", flat=True))
    if len(earlier) >= OVERFLOW_PER_DAY or any(at > now - _HOUR for at in earlier):
        return
    subject = "More updates are waiting for you in NACC SYS"
    who = entered_name(by, "A colleague")
    if not sending():
        _record(by, user, OVERFLOW, now)
        logger.warning("EMAIL (console) %s to %s: %s", OVERFLOW, user.email, subject)
        return
    html_body, text_body = render(
        subject,
        [f"{who} has made more changes that affect you lately than NACC SYS "
         "sends emails for. Sign in to see them: your Dashboard and the "
         "notifications list show everything. Emails about their changes start "
         "again once they have slowed down: within the hour, or within a day at "
         "the longest."],
        button=OPEN_NACC, subject=subject, recipient=user)
    _record(by, user, OVERFLOW, now)
    _queue(_payload(user, subject, html_body, text_body), f"{OVERFLOW} email")


def _tell_the_isa(by, now):
    """One SECURITY line in the activity log, the first time `by` reaches a limit
    in a rolling hour: the ISA's console shows it in the security log. An
    account that does this may be careless or may be somebody else's. The line
    names the account and nothing else - no colleague, no case - and says what
    it is by `ActivityLog.kind`, which the console reads instead of the words."""
    if ActivityLog.objects.filter(
            kind=ActivityLog.KIND_EMAIL_LIMIT, actor=by, created_at__gt=now - _HOUR).exists():
        return
    log_activity(
        by, ActivityLog.UPDATED, ActivityLog.SECURITY,
        entity_type="User", entity_id=by.pk, kind=ActivityLog.KIND_EMAIL_LIMIT,
        entity_label=f"{one_line(display_name(by, 'An account'), 100)} "
                     "reached the email limit")


def _hold(kind_key, user, subject, by, now):
    """What happens to an email the limit has stopped: nothing is sent and
    nothing is counted, then the colleague and the ISA are told (each at most
    once an hour; the colleague at most `OVERFLOW_PER_DAY` times a day). The two
    are tried one after the other, so one that fails does not stop the other.

    A held email is not tried again later, whatever it was: the notice is what
    tells the colleague to look. That includes an acceptance, which the caller
    has already claimed (`claim_answer_mail`), and a calendar invite."""
    if not sending():
        logger.warning("EMAIL (held: limit) %s to %s: %s", kind_key, user.email, subject)
    for tell in (lambda: _send_overflow_notice(by, user, now), lambda: _tell_the_isa(by, now)):
        try:
            tell()
        except Exception:                                        # noqa: BLE001
            logger.exception("Could not report that an account reached the email limit")


def prune_send_records(now=None):
    """Delete the send records older than `SEND_RECORD_KEEP`. Called by the
    once-a-minute sweep and by the reminder run, so the table never holds more
    than a day and an hour of mail. Returns how many went. Never raises."""
    try:
        now = now or timezone.now()
        return EmailSendRecord.objects.filter(
            created_at__lt=now - SEND_RECORD_KEEP).delete()[0]
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not prune the email send records")
        return 0


def notify(kind_key, user, *, subject, heading, paragraphs, facts=(), button=None,
           attachment=None, preview=None, chip=None, greeting=None, steps=(), by=None):
    """Email `user` one message of `kind_key`. True when it was queued, False
    when there was nothing to send. Never raises.

    `preview`, `chip`, `greeting` and `steps` are passed to `render`, which
    says what each is for; an email that gives none looks as it always did,
    apart from what every email gets.

    `by` is the person whose action caused the email, and the one the limit
    counts it against. Every caller passes it; without one the email is not
    counted and not limited. An email over the actor's limit is HELD (False
    comes back, nothing is built, `attachment` is not called) and the colleague
    and the ISA are told (`_hold`).

    `attachment` is None or a callable taking no arguments and returning
    (filename, text). It is called only once the email is certain to be
    queued, so a caller can count the attachment as sent inside it (the
    calendar invite's SEQUENCE) without counting one that was never mailed:
    not for a person who switched the kind off, not when the actor's limit
    holds it, and not with no mail service.
    """
    try:
        if not wants(user, kind_key):
            return False
        if getattr(by, "pk", None) is None:
            by = None
        now = timezone.now()
        if by is not None and _over_limit(by, user, now):
            _hold(kind_key, user, subject, by, now)
            return False
        if not sending():
            _record(by, user, kind_key, now)
            logger.warning("EMAIL (console) %s to %s: %s", kind_key, user.email, subject)
            return False
        html_body, text_body = render(
            heading, paragraphs, facts, button, preview=preview, chip=chip,
            greeting=greeting, steps=steps, subject=subject, recipient=user)
        payload = _payload(user, subject, html_body, text_body)
        if attachment is not None:
            filename, content = attachment()
            payload["attachment"] = [{
                "name": filename,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            }]
        _record(by, user, kind_key, now)
        _queue(payload, f"{kind_key} email")
        return True
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not queue the %s email", kind_key)
        return False


# --------------------------------------------------------------------------
# 5. The emails
# --------------------------------------------------------------------------
#
# Every function takes `by`, the person who did it, and sends nothing to that
# person (rule 3). They return nothing: a caller is in the middle of a save and
# has no use for whether a message went. `notify` never raises, but the work
# done before it (finding the recipient, naming the case) is not inside it, so
# each function is wrapped as well: rule 6 covers the whole of it.

OPEN_NACC = ("Open NACC SYS", "/")
SCHEDULE = ("Open the schedule", "/schedule")


def _never_raises(send):
    @functools.wraps(send)
    def quietly(*args, **kwargs):
        try:
            send(*args, **kwargs)
        except Exception:                                        # noqa: BLE001
            logger.exception("Could not prepare the %s email", send.__name__)
    return quietly


# How a lead sentence names the person who asked, by the role they hold. A
# social worker or the ISA is who asks (children/assignment.py); a role that is
# not here is simply not named.
_ASKER_ROLE = {Role.STAFF: "a social worker", Role.ADMINISTRATOR: "the ISA"}


@_never_raises
def case_request(child, psychologist, *, by):
    """A psychologist is asked to take a case (children/assignment.py
    request_assignment). The same subject and question the assignment email
    always had, with the reference every screen uses and the fuller layout of
    23 Oct 2026: an "Action needed" chip, a greeting, who asked and when, the
    three things to do, and the button.

    It is the one email that asks something of its reader, and so the one a
    look-alike would copy. It says who asked and in what role, and where to
    find the question once signed in ("Waiting for your answer", the panel at
    the top of the Dashboard - components/AssignmentRequests.jsx), so the
    button never has to be the only way to find it. Still nothing about the
    child: no name, case type, age, purpose or reason."""
    if psychologist is None or psychologist.pk == by.pk:
        return
    ref, who = _ref(child), entered_name(by, "A colleague")
    role = _ASKER_ROLE.get(getattr(getattr(by, "role", None), "role_name", None))
    asker = f"{who}, {role}," if role else who
    name = entered_name(psychologist)
    subject = "A case is waiting for your answer"
    notify("case_request", psychologist, subject=subject, heading=subject,
           preview=f"Case {ref} is waiting for you to accept or decline.",
           chip="Action needed",
           greeting=f"Hello {name}," if name else None,
           paragraphs=[f"{asker} asked you to take case {ref}. It joins your "
                       "records only once you accept it."],
           facts=[("Case", ref), ("Asked by", who), ("Asked on", when(timezone.now()))],
           steps=["Open NACC SYS and sign in.",
                  f'Find {ref} under "Waiting for your answer" at the top of your '
                  "Dashboard.",
                  "Accept it, or decline it with a reason for the social worker."],
           button=("Review the request", "/"), by=by)


@_never_raises
def request_withdrawn(psychologist, child_or_ref, *, by):
    """The question put to a psychologist has gone: withdrawn, or taken out with
    a duplicate record. `child_or_ref` is a Child or a case reference already
    written out, because a removed duplicate has no row to ask.

    Closing a case withdraws its open request too (children/views.py
    terminate), and whoever closed it withdrew nothing: it reads as a closed
    case and names nobody."""
    from children.models import Child
    if psychologist is None or psychologist.pk == by.pk:
        return
    ref = _ref(child_or_ref)
    subject = f"Case {ref} is no longer waiting for you"
    if isinstance(child_or_ref, Child) and child_or_ref.status == Child.INACTIVE:
        said = (f"Case {ref} was closed, so the request for you to take it is no "
                "longer open. There is nothing for you to do.")
    else:
        said = (f"{entered_name(by, 'A colleague')} withdrew the request for you to "
                f"take case {ref}. There is nothing for you to do.")
    notify("request_withdrawn", psychologist, subject=subject, heading=subject,
           paragraphs=[said], button=OPEN_NACC, by=by)


# How long an acceptance may wait for its first session before the sweep mails
# it anyway (round three, 24 Oct 2026). Long enough for the "When will you see
# them first?" dialog to be answered, short enough that nobody wonders.
ANSWER_MAIL_GRACE = timedelta(minutes=10)

# The most acceptances one sweep mails, so a long outage cannot turn the next
# activity-feed read into hundreds of emails. The rest go on the next sweep.
SWEEP_BATCH = 50

# The most the activity feed's own sweep mails in the request it rides on. That
# request is the one every screen waits for, and each acceptance costs a handful
# of queries and a rendered page (about 250 queries for a batch of 50), which
# on a database across a network is seconds added to a screen. A backlog clears
# ten a minute instead; the daily reminder run, which nobody waits for, takes
# the whole `SWEEP_BATCH`.
FEED_SWEEP_BATCH = 10

# The sweep runs at most once a minute per process (`maybe_sweep`). The cache is
# per process, so several workers each run it; the claim below is what keeps
# that from mailing anyone twice.
_SWEEP_KEY = "email-answer-sweep"
_SWEEP_EVERY = 60


def claim_answer_mail(req):
    """Make the email for this ACCEPTED request ours to send, once. True only
    for the caller whose UPDATE set `answer_mailed_at`; everyone else - a
    booking, the dialog's call, the sweep, a second tab - gets False and sends
    nothing, so however they race the social worker has one email.

    The claim stands even when the email is then not sent (the social worker
    switched it off, no mail service, nobody to tell, or the psychologist is at
    the limit on what their actions can cause: the social worker is then told
    that more updates are waiting, and the answer is on the record): the answer
    has been dealt with, and a row left unstamped would be tried again by the
    sweep every minute - held again each time, and crowding the newer
    acceptances out of the ten it takes."""
    from children.models import AssignmentRequest
    return bool(AssignmentRequest.objects
                .filter(pk=req.pk, status=AssignmentRequest.ACCEPTED,
                        answer_mailed_at__isnull=True)
                .update(answer_mailed_at=timezone.now()))


def _next_session(req):
    """The earliest session still to come that this psychologist has with this
    child, or None. Anyone may have booked it: by the time the acceptance is
    mailed without the psychologist's own booking, the social worker or the ISA
    may have."""
    from scheduling.models import Appointment
    return (Appointment.objects
            .filter(child_id=req.child_id, psychologist_id=req.psychologist_id,
                    status=Appointment.SCHEDULED, start__gt=timezone.now())
            .order_by("start", "pk").first())


def _superseded(req):
    """True when this acceptance is no longer what the child's record says: the
    child is not with that psychologist any more (another accepted, or the
    social worker took the psychologist off), or a later request for the child
    has been accepted since. Mailing it then would tell the social worker
    something that has stopped being so - "accepted ... in their records" of a
    child who is with someone else - and the acceptance that replaced it has its
    own email. Whoever reaches a superseded acceptance first (the dialog's call
    or the sweep) claims it and sends nothing, so it is not come back to.

    Requests are answered in the order they are made (a child has one open at a
    time), so a higher id is a later one."""
    from children.models import AssignmentRequest
    if req.child.assigned_psychologist_id != req.psychologist_id:
        return True
    return AssignmentRequest.objects.filter(
        child_id=req.child_id, status=AssignmentRequest.ACCEPTED, pk__gt=req.pk).exists()


def text_answer(req, *, first_session=None):
    """Text the social worker the psychologist's ACCEPTANCE (owner, 28 Oct
    2026), by whoever just won `claim_answer_mail` - so it goes once per answer,
    exactly when the email is decided, and not for a superseded acceptance
    (`_superseded`, which the email drops too). It does not look at the email
    opt-out or the email limit: texts have their own limits (sms_catalogue).
    `first_session` is the session just booked, for the combined text. Never
    raises."""
    from accounts import sms_notifications
    from children.models import AssignmentRequest
    try:
        if req.status != AssignmentRequest.ACCEPTED or _superseded(req):
            return False
        if first_session is not None:
            return sms_notifications.notify_case_answer(
                req, "case_accepted_booked", when=first_session.start)
        return sms_notifications.notify_case_answer(req, "case_accepted")
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not text the acceptance")
        return False


@_never_raises
def case_answered(req, *, by, first_session=None):
    """A psychologist accepted or declined (children/assignment.py), told to
    the social worker whose record it is, or whoever asked where it has none.
    Only a social worker is told: the ISA asking on a record nobody holds gets
    nothing, because the ISA reads the answer on the record.

    A decline is mailed at once. An acceptance is mailed by whoever claims it
    first (`claim_answer_mail`) and this function does not claim: the caller
    has, or it is a decline. It reads differently by what is known:

    * `first_session`, the session the psychologist has just booked: ONE email
      for the acceptance and the booking, so the social worker is not mailed
      twice seconds apart (round three, 24 Oct 2026).
    * otherwise the child's next session with this psychologist, if there is
      one: the acceptance, naming it.
    * otherwise the acceptance alone, saying no session is booked yet, and
      promising an email when one is only to a social worker who gets those -
      or, for a case closed since, saying so and promising nothing.

    An acceptance that has been superseded is not mailed (`_superseded`).

    The decline reason is NEVER included. It is the psychologist's words about
    a child, and the social worker reads it on the record, behind the sign-in."""
    _send_answer(req, by=by, first_session=first_session)


def _send_answer(req, *, by, first_session=None):
    """The work of `case_answered`, which swallows whatever this raises. The
    booking path calls it directly (`accepted_with_first_session`): it has
    already claimed the acceptance, so it must know whether the email failed to
    be prepared, to give the claim back and let the booking's own email go."""
    from children.models import AssignmentRequest, Child
    if req.status not in (AssignmentRequest.ACCEPTED, AssignmentRequest.DECLINED):
        return
    if req.status == AssignmentRequest.ACCEPTED and _superseded(req):
        return
    recipient = req.child.social_worker or req.requested_by
    if recipient is None or recipient.pk == by.pk:
        return
    ref, psych = _ref(req.child), entered_name(req.psychologist, "A psychologist")

    if req.status == AssignmentRequest.DECLINED:
        kind_key = "case_declined"
        subject = f"{psych} declined case {ref}"
        lead = (f"{psych} declined case {ref}. Sign in to read their reason and ask "
                "another psychologist.")
        facts, button = (), ("Open the record", f"/report/child/{req.child_id}")
    else:
        kind_key = "case_accepted"
        in_records = f"{psych} accepted case {ref}. It is now in their records"
        session = first_session or _next_session(req)
        if first_session is not None:
            subject = f"{psych} accepted case {ref} and booked the first session"
            lead = f"{in_records}, and the first session is booked."
            facts = [("Case", ref), ("First session", span(
                session.start, session.duration_minutes))]
            button = SCHEDULE
        elif session is not None:
            subject = f"{psych} accepted case {ref}"
            lead = f"{in_records}, and a session is booked."
            facts = [("Case", ref), ("Next session", span(
                session.start, session.duration_minutes))]
            button = SCHEDULE
        else:
            subject = f"{psych} accepted case {ref}"
            if req.child.status == Child.INACTIVE:
                # Closed since it was accepted (the email waits for the dialog
                # or the sweep): no session will follow, so no promise of one.
                lead = f"{psych} accepted case {ref}, which has since been closed."
            else:
                promise = ("; you will get an email when one is."
                           if wants(recipient, "child_sessions") else ".")
                lead = f"{in_records}. No session is booked yet{promise}"
            facts, button = (), ("Open the record", f"/report/child/{req.child_id}")
    notify(kind_key, recipient, subject=subject, heading=subject,
           paragraphs=[lead], facts=facts, button=button, by=by)


def accepted_with_first_session(appointment, *, by):
    """The psychologist who accepted a case has just booked a session for it
    (scheduling/session_mail.py). True when the acceptance has been dealt with
    as ONE message with that booking, in which case the caller sends the social
    worker nothing more for this booking. "Dealt with" is mailed, logged on a
    copy with no mail service, or held because the psychologist is at the limit
    on the emails their actions cause: the booking's own email would be held by
    the same limit, and the social worker has been told that more updates are
    waiting.

    Only the psychologist who accepted can make it combine. A booking by the
    social worker or the ISA is the ordinary "booked a session" email, and the
    acceptance still goes by the dialog's call or the sweep, naming that
    session. A session that is not one to come (already past, or not
    "scheduled") does not combine either, and does not use up the acceptance:
    "the first session is booked" would be untrue of it. Nor does a session
    that is not the FIRST one: if this psychologist already has another with
    the child that has not been called off (the ISA booked 9:30, the child came
    back to the same psychologist), the acceptance stays owed and goes naming
    the earliest session, as "Next session".

    False, and the ordinary email goes, when the acceptance was already mailed
    (it is only the first booking that combines), when there is no such
    acceptance, when it has been superseded, when the email could not be
    prepared (the claim is given back, so the dialog's call or the sweep sends
    the acceptance later), and when the social worker has switched "A
    psychologist accepts" off - then the claim is taken all the same, and they
    hear of the booking as "Sessions for my children". Never raises."""
    claimed = None
    try:
        from children.models import AssignmentRequest
        from scheduling.models import Appointment
        if (by is None or by.pk != appointment.psychologist_id
                or appointment.status != Appointment.SCHEDULED
                or appointment.start <= timezone.now()):
            return False
        req = (AssignmentRequest.objects
               .select_related("child", "child__social_worker", "psychologist",
                               "requested_by")
               .filter(child_id=appointment.child_id, psychologist_id=by.pk,
                       status=AssignmentRequest.ACCEPTED, answer_mailed_at__isnull=True)
               .order_by("-decided_at", "-pk").first())
        if req is None or _superseded(req):
            return False
        earlier = (Appointment.objects
                   .filter(child_id=appointment.child_id, psychologist_id=by.pk)
                   .exclude(pk=appointment.pk)
                   .exclude(status=Appointment.CANCELLED))
        if earlier.exists() or not claim_answer_mail(req):
            return False
        claimed = req
        # This booking is the acceptance's own: whichever way the email goes,
        # the acceptance text (combined, or later by the dialog) covers it, so
        # scheduling/session_mail.py must not text the booking as well.
        appointment.acceptance_claimed = True
        if not wants(req.child.social_worker or req.requested_by, "case_accepted"):
            # The email is off; the text is its own switch-less nudge.
            text_answer(req, first_session=appointment)
            return False
        _send_answer(req, by=by, first_session=appointment)
        # After the email was prepared: had it raised, the claim goes back and
        # the dialog or the sweep texts instead, so it is never texted twice.
        text_answer(req, first_session=appointment)
        return True
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not prepare the combined acceptance email")
        if claimed is not None:
            _release_answer_mail(claimed)
        return False


def _release_answer_mail(req):
    """Give back a claim whose email could not be prepared, so the dialog's call
    or the sweep sends the acceptance later and the booking's own email goes now.
    Safe because the claim is exclusive: nobody else has touched the row since
    it was taken. Never raises."""
    from children.models import AssignmentRequest
    try:
        AssignmentRequest.objects.filter(
            pk=req.pk, status=AssignmentRequest.ACCEPTED,
        ).update(answer_mailed_at=None)
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not give back the claim on an acceptance email")


def mail_overdue_answers(now=None, *, dry_run=False, limit=None):
    """Mail every acceptance that has waited `ANSWER_MAIL_GRACE` with no
    booking and no call from the dialog - the browser crashed, the network
    dropped, the tab was closed before the call went. Oldest first, at most
    `limit` at a time (`SWEEP_BATCH` unless the caller says fewer). Returns how
    many were claimed, which counts one dealt with and not mailed (switched off,
    superseded) as well; a dry run: how many would be, without claiming any.
    Never raises.

    The email names the child's next session if somebody has booked one since
    (`case_answered`), and says none is booked yet otherwise."""
    from children.models import AssignmentRequest
    try:
        now = now or timezone.now()
        due = (AssignmentRequest.objects
               .select_related("child", "child__social_worker", "psychologist",
                               "requested_by", "decided_by")
               .filter(status=AssignmentRequest.ACCEPTED, answer_mailed_at__isnull=True,
                       decided_at__lte=now - ANSWER_MAIL_GRACE)
               .order_by("decided_at", "pk")[:limit or SWEEP_BATCH])
        if dry_run:
            return len(due)
        claimed = 0
        for req in due:
            try:
                if not claim_answer_mail(req):
                    continue
                claimed += 1
                text_answer(req)
                # decided_by is cleared if that account is ever deleted; the
                # psychologist asked is the one who accepted.
                case_answered(req, by=req.decided_by or req.psychologist)
            except Exception:                                    # noqa: BLE001
                logger.exception("Could not mail an overdue acceptance")
        return claimed
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not look for overdue acceptances")
        return 0


def maybe_sweep():
    """`mail_overdue_answers`, at most once a minute per process and at most
    `FEED_SWEEP_BATCH` at a time, and `prune_send_records` with it. It is called
    from a request that every screen makes (the activity feed), so otherwise it
    costs one cache call. Never raises."""
    try:
        if cache.add(_SWEEP_KEY, True, _SWEEP_EVERY):
            mail_overdue_answers(limit=FEED_SWEEP_BATCH)
            prune_send_records()
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not sweep for overdue acceptances")


@_never_raises
def records_transferred(target, source, count, *, by):
    """The ISA moved `count` records to `target` from `source`
    (children/transfer.py). Told to the receiving worker; the one who gave them
    up already has the bell's sentence and was not asked for this."""
    if target is None or target.pk == by.pk:
        return
    n, who = _plural(count, "record"), entered_name(by, "The ISA")
    subject = f"{n} moved to you"
    notify("records_transferred", target, subject=subject, heading=subject,
           paragraphs=[f"{who} moved {n} to you from {entered_name(source, 'a colleague')}."],
           button=("Open Records", "/children"), by=by)


@_never_raises
def case_taken_over(child, previous, *, by):
    """Another social worker reopened `child`, a closed case in `previous`'s
    records, at intake (children/views.py reopen), and it is now theirs."""
    if previous is None or previous.pk == by.pk:
        return
    ref, who = _ref(child), entered_name(by, "A colleague")
    subject = f"Case {ref} was reopened by {who}"
    notify("case_taken_over", previous, subject=subject, heading=subject,
           paragraphs=[f"{who} reopened case {ref}, a closed case in your records, "
                       "at intake. It is now in their records."],
           button=OPEN_NACC, by=by)
