"""The emails this system sends to psychologists and social workers about
their work (owner's request, 21 Oct 2026). Design:
docs/superpowers/specs/2026-10-21-email-notifications-design.md.

Everything goes through the sender that already mails passwords and sign-up
codes (children/notifications.py: Brevo, `BREVO_API_KEY`). No new provider. The
one setting they add is `SITE_URL`, the address every email's button and plain
address point at (23 Oct 2026; children/notifications.py `sign_in_url`). The
text messages are a separate channel and are untouched
(accounts/sms_notifications.py).

Seven rules every email here keeps:

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
from collections import namedtuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.display import entered_name
from accounts.models import EmailOptOut, Role
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
         "When a psychologist accepts a case you asked them to take."),
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
         "your records."),
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
# Nor does it say what the button opens. It goes to the sign-in page in a tab
# that is signed out and straight to the screen in one that is not, and the
# label above it ("Open the schedule") says where it is going.
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
           greeting=None, steps=(), subject=None):
    """(html, text) for one email: the temporary password email's branded
    layout (children/notifications.py) - the same palette and header, a table
    layout with inline styles because Gmail strips <style> - and a plain-text
    part, because a message with no text part is likelier to be filtered.

    `facts` is (label, value) rows. `button` is (label, path): it points at
    sign_in_url() plus the path, and is left out when no address is known
    rather than printing a dead link. Every value is escaped.

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

    profile = (f"Choose which emails you get: My Profile, Email notifications - "
               f"{site}/profile") if site else ""
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

def _queue(payload, description):
    """Hand one message to Brevo after the surrounding transaction commits, on
    a daemon thread. on_commit so a rolled-back save cannot mail about
    something that never happened; the thread so a slow Brevo cannot hold the
    response open. The one seam tests patch to read what would have gone."""
    transaction.on_commit(
        lambda: threading.Thread(
            target=notifications._post, args=(payload, description),
            daemon=True).start(),
        # Inside an explicit atomic block this runs at the COMMIT, outside
        # notify's try/except: a thread that cannot start would otherwise
        # surface as a 500 after the save was already made (and skip the other
        # callbacks queued with it). Robust callbacks are logged and dropped.
        robust=True)


def notify(kind_key, user, *, subject, heading, paragraphs, facts=(), button=None,
           attachment=None, preview=None, chip=None, greeting=None, steps=()):
    """Email `user` one message of `kind_key`. True when it was queued, False
    when there was nothing to send. Never raises.

    `preview`, `chip`, `greeting` and `steps` are passed to `render`, which
    says what each is for; an email that gives none looks as it always did,
    apart from what every email gets.

    `attachment` is None or a callable taking no arguments and returning
    (filename, text). It is called only once the email is certain to be
    queued, so a caller can count the attachment as sent inside it (the
    calendar invite's SEQUENCE) without counting one that was never mailed:
    not for a person who switched the kind off, and not with no mail service.
    """
    try:
        if not wants(user, kind_key):
            return False
        if not sending():
            logger.warning("EMAIL (console) %s to %s: %s", kind_key, user.email, subject)
            return False
        html_body, text_body = render(
            heading, paragraphs, facts, button, preview=preview, chip=chip,
            greeting=greeting, steps=steps, subject=subject)
        name = entered_name(user)
        payload = {
            "sender": {
                "name": settings.BREVO_SENDER_NAME,
                "email": settings.BREVO_SENDER_EMAIL,
            },
            "to": [{"email": user.email, **({"name": name} if name else {})}],
            "subject": subject,
            "htmlContent": html_body,
            "textContent": text_body,
        }
        if attachment is not None:
            filename, content = attachment()
            payload["attachment"] = [{
                "name": filename,
                "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            }]
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
           button=("Review the request", "/"))


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
           paragraphs=[said], button=OPEN_NACC)


@_never_raises
def case_answered(req, *, by):
    """A psychologist accepted or declined (children/assignment.py), told to
    the social worker whose record it is, or whoever asked where it has none.
    Only a social worker is told: the ISA asking on a record nobody holds gets
    nothing, because the ISA reads the answer on the record.

    The decline reason is NEVER included. It is the psychologist's words about
    a child, and the social worker reads it on the record, behind the sign-in."""
    from children.models import AssignmentRequest
    if req.status == AssignmentRequest.ACCEPTED:
        kind_key, verb = "case_accepted", "accepted"
        more = "It is now in their records, and a session can be booked."
    elif req.status == AssignmentRequest.DECLINED:
        kind_key, verb = "case_declined", "declined"
        more = "Sign in to read their reason and ask another psychologist."
    else:
        return
    recipient = req.child.social_worker or req.requested_by
    if recipient is None or recipient.pk == by.pk:
        return
    ref, psych = _ref(req.child), entered_name(req.psychologist, "A psychologist")
    subject = f"{psych} {verb} case {ref}"
    notify(kind_key, recipient, subject=subject, heading=subject,
           paragraphs=[f"{psych} {verb} case {ref}. {more}"],
           button=("Open the record", f"/report/child/{req.child_id}"))


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
           button=("Open Records", "/children"))


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
           button=OPEN_NACC)
