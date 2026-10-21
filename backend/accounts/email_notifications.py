"""The emails this system sends to psychologists and social workers about
their work (owner's request, 21 Oct 2026). Design:
docs/superpowers/specs/2026-10-21-email-notifications-design.md.

Everything goes through the sender that already mails passwords and sign-up
codes (children/notifications.py: Brevo, `BREVO_API_KEY`). No new provider and
no new setting. The text messages are a separate channel and are untouched
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


def when(dt):
    """"Wed 21 Oct 2026, 9:30 AM", in local time. Built by hand: %-d and %-I
    are glibc extensions and raise on Windows, where the local copy runs."""
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    local = timezone.localtime(dt)
    return f"{local:%a} {local.day} {local:%b} {local.year}, {clock(local)}"


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

def render(heading, paragraphs, facts=(), button=None):
    """(html, text) for one email: the temporary password email's branded
    layout (children/notifications.py) - the same palette and header, a table
    layout with inline styles because Gmail strips <style> - and a plain-text
    part, because a message with no text part is likelier to be filtered.

    `facts` is (label, value) rows. `button` is (label, path): it points at
    sign_in_url() plus the path, and is left out when no address is known
    rather than printing a dead link. Every value is escaped."""
    n = notifications
    esc = html.escape
    url = n.sign_in_url()
    link = f"{url}{button[1]}" if button and url else ""

    paragraph_rows = "".join(
        f'<p style="margin:{14 if i == 0 else 10}px 0 0;font-size:15px;'
        f'line-height:1.6;color:{n._INK if i == 0 else n._MUTED};">{esc(p)}</p>'
        for i, p in enumerate(paragraphs))

    fact_rows = "".join(
        f'<tr><td style="padding:3px 14px 3px 0;font-size:11px;'
        f'letter-spacing:.09em;font-weight:700;color:{n._BRAND_DARK};'
        f'text-transform:uppercase;white-space:nowrap;">{esc(label)}</td>'
        f'<td style="padding:3px 0;font-size:15px;color:{n._INK};">{esc(value)}</td></tr>'
        for label, value in facts)
    fact_block = (
        f'<tr><td style="padding:20px 32px 0;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" '
        f'style="width:100%;background:{n._TINT};border:1px solid #d5dcfa;'
        f'border-radius:10px;padding:10px 16px;">{fact_rows}</table>'
        f'</td></tr>'
    ) if fact_rows else ""

    button_row = (
        f'<tr><td align="center" style="padding:26px 32px 0;">'
        f'<a href="{esc(link)}" style="display:inline-block;background:{n._BRAND};'
        f'color:#ffffff;text-decoration:none;font-weight:700;font-size:15px;'
        f'padding:13px 30px;border-radius:8px;">{esc(button[0])}</a>'
        f'</td></tr>'
    ) if link else ""

    html_body = (
        f'<!DOCTYPE html><html><body style="margin:0;padding:0;'
        f'background:{n._PAPER};">'
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
        f'<div style="font-size:19px;font-weight:700;color:{n._INK};">'
        f'{esc(heading)}</div>'
        f'{paragraph_rows}</td></tr>'

        f'{fact_block}'
        f'{button_row}'
        f'<tr><td style="padding:26px 32px 0;"></td></tr>'

        # Footer
        f'<tr><td style="background:{n._PAPER};border-top:1px solid {n._BORDER};'
        f'padding:15px 32px;font-size:11.5px;line-height:1.6;color:{n._MUTED};">'
        f'{esc(FOOTER)}</td></tr>'

        f'</table></td></tr></table></body></html>'
    )

    text_parts = [heading, *paragraphs]
    if facts:
        text_parts.append("\n".join(f"{label}: {value}" for label, value in facts))
    if link:
        text_parts.append(f"{button[0]}: {link}")
    text_parts.append(FOOTER)
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
           attachment=None):
    """Email `user` one message of `kind_key`. True when it was queued, False
    when there was nothing to send. Never raises.

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
        html_body, text_body = render(heading, paragraphs, facts, button)
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


@_never_raises
def case_request(child, psychologist, *, by):
    """A psychologist is asked to take a case (children/assignment.py
    request_assignment). The same subject and question the assignment email
    always had, now with the reference every screen uses."""
    if psychologist is None or psychologist.pk == by.pk:
        return
    ref, who = _ref(child), entered_name(by, "A colleague")
    subject = "A case is waiting for your answer"
    notify("case_request", psychologist, subject=subject, heading=subject,
           paragraphs=[f"{who} asked you to take case {ref}. It joins your records "
                       "once you accept it.",
                       "Sign in to accept or decline it."],
           button=("Answer the request", "/"))


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
