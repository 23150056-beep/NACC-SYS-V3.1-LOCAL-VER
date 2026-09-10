"""Transactional mail for case assignment.

Restored from V2, with two deliberate changes.

**No name, no case details.** V2's email carried the child's full name and case
number in the body. Brevo is a processor outside the agency's data-processing
agreements (see the data-residency section of docs/CLOUD-DEPLOYMENT.md), and a
child's name in a third party's mail logs is exactly the disclosure that section
exists to prevent. The email now carries the case number and nothing else — the
psychologist signs in to see who it is.

**Off the request thread.** V2 called Brevo synchronously with a 20-second
timeout, inside the request that saved the child record. A slow or unreachable
Brevo would have held the save open and, on a small instance, tied up the
worker. The send now happens after the transaction commits, on a daemon thread:
the record is the source of truth and the mail must never be able to break it.
"""
import html
import json
import logging
import threading
import urllib.error
import urllib.request

from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"
_TIMEOUT = 20


def _post(payload, description):
    """POST one message to Brevo. `description` names which mail this is, and
    exists only for the log: more than one kind of message goes through here,
    and a failure that does not say which one leaves whoever is reading the
    logs looking at the wrong feature."""
    request = urllib.request.Request(
        BREVO_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": settings.BREVO_API_KEY,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.status in (200, 201, 202)
    except urllib.error.HTTPError as exc:
        # Brevo says WHY in the response body, and a traceback does not carry
        # it: a rejected sender and a bad key both surface as "HTTP Error 400"
        # with nothing to tell them apart. Read the body and log it, or the
        # only honest answer to "why did no email arrive" is a guess.
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:                                        # noqa: BLE001
            detail = "(no response body)"
        logger.error("Brevo %s failed: HTTP %s — %s",
                     description, exc.code, detail)
        return False
    except urllib.error.URLError:
        logger.exception("Brevo %s failed: could not reach Brevo", description)
        return False
    except Exception:
        logger.exception("Unexpected error sending Brevo %s", description)
        return False


def build_payload(psychologist, case_number):
    """The message itself. Split out so a test can assert what leaves the
    building without standing up an HTTP server."""
    recipient_name = html.escape(
        getattr(psychologist, "fullname", "")
        or getattr(psychologist, "username", "")
        or "Psychologist"
    )
    return {
        "sender": {
            "name": settings.BREVO_SENDER_NAME,
            "email": settings.BREVO_SENDER_EMAIL,
        },
        "to": [{"email": psychologist.email, "name": recipient_name}],
        "subject": "New case assignment",
        "htmlContent": (
            "<h2>New case assignment</h2>"
            f"<p>Dear {recipient_name},</p>"
            "<p>A new case has been assigned to you.</p>"
            f"<p><strong>Case number:</strong> {case_number}</p>"
            "<p>Sign in to the RACCO I Child Care Management System to review it. "
            "No case details are included in this email.</p>"
            "<br><small>This is an automated notification.</small>"
        ),
    }


def send_assignment_notification(child):
    """Queue one email to the child's assigned psychologist.

    Returns True when a send was queued, False when there was nothing to send
    (no assignee, no address, no API key). Never raises — the caller is in the
    middle of saving a case record.
    """
    psychologist = getattr(child, "assigned_psychologist", None)
    if psychologist is None or not getattr(psychologist, "email", ""):
        return False
    if not settings.BREVO_API_KEY:
        logger.warning(
            "BREVO_API_KEY is not set; skipped assignment email for case %s", child.id)
        return False

    payload = build_payload(psychologist, child.id)
    # on_commit so a rolled-back save cannot send mail about a case that does
    # not exist; the thread so a slow Brevo cannot hold the response open.
    transaction.on_commit(
        lambda: threading.Thread(
            target=_post, args=(payload, "assignment email"), daemon=True).start())
    return True


def sign_in_url():
    """Where to send someone to use the password we just gave them.

    Taken from CORS_ALLOWED_ORIGINS, which already holds the frontend's own
    origin on every deployment — a separate setting would be a second place to
    get wrong, and one that only ever shows up as a dead link in an email
    nobody reports. A localhost entry is skipped when a deployed one exists,
    since the mail is read on someone else's machine.
    """
    origins = [o.strip().rstrip("/") for o in settings.CORS_ALLOWED_ORIGINS
               if o and o.strip()]
    deployed = [o for o in origins
                if "localhost" not in o and "127.0.0.1" not in o]
    return (deployed or origins or [""])[0]


# Palette from the app's own stylesheet, so the mail looks like the system it
# comes from. Inline on every element on purpose: Gmail strips <style> blocks,
# and a table layout is the only thing every client agrees on.
_BRAND = "#2236c4"
_BRAND_DARK = "#1b2aa3"
_TINT = "#eef1fd"
_PAPER = "#f4f6fa"
_INK = "#101828"
_MUTED = "#667085"
_BORDER = "#e4e7ec"


def send_test_email(recipient):
    """Send a test message and RETURN what Brevo said. (ok, detail).

    Synchronous and result-returning, unlike every other send in this module.
    The others are fire-and-forget because a slow Brevo must never hold a case
    record open — but that also means a rejected send is invisible unless
    somebody reads the server log, and reading the log is not something an
    administrator can do on Render's free plan.

    So this one is for a person pressing a button and waiting for an answer,
    and it reports the answer verbatim: which sender it used, and exactly what
    Brevo replied.
    """
    sender = settings.BREVO_SENDER_EMAIL
    if not settings.BREVO_API_KEY:
        return False, ("No API key is set on the server (BREVO_API_KEY). "
                       "Emails are switched off; passwords must be handed "
                       "over in person.")
    if not recipient:
        return False, "That account has no email address."

    payload = {
        "sender": {"name": settings.BREVO_SENDER_NAME, "email": sender},
        "to": [{"email": recipient}],
        "subject": "NACC SYS email test",
        "htmlContent": ("<p>This is a test from NACC SYS. If you are reading "
                        "it, temporary password emails will arrive.</p>"),
        "textContent": ("This is a test from NACC SYS. If you are reading it, "
                        "temporary password emails will arrive."),
    }
    request = urllib.request.Request(
        BREVO_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "api-key": settings.BREVO_API_KEY,
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            if response.status in (200, 201, 202):
                return True, (f"Brevo accepted a test message from {sender} "
                              f"to {recipient}. Check that inbox, including "
                              f"spam.")
            return False, f"Brevo replied HTTP {response.status}."
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:                                        # noqa: BLE001
            detail = "(no response body)"
        # The two that actually happen, named in words rather than codes.
        if exc.code == 401:
            hint = ("The API key on the server is not one Brevo recognises. "
                    "Check BREVO_API_KEY.")
        elif exc.code == 400 and "sender" in detail.lower():
            hint = (f"Brevo will not send from {sender}. Verify that exact "
                    f"address in Brevo, or change BREVO_SENDER_EMAIL to one "
                    f"that is verified.")
        else:
            hint = "See the message from Brevo below."
        logger.error("Brevo test email failed: HTTP %s — %s", exc.code, detail)
        return False, f"{hint}  (Brevo said: HTTP {exc.code} — {detail})"
    except urllib.error.URLError as exc:
        logger.exception("Brevo test email could not reach Brevo")
        return False, f"Could not reach Brevo: {exc}"


def build_temporary_password_payload(user, temporary_password):
    """The message itself, split out so a test can read what leaves the
    building without standing up an HTTP server.

    Carries the password and nothing else about the person — no case data, no
    caseload, nothing about a child. Brevo is a processor outside the agency's
    agreements, and the rule that keeps a child's name out of the assignment
    email applies here too.
    """
    name = html.escape(
        getattr(user, "fullname", "")
        or getattr(user, "username", "")
        or "there"
    )
    # The password is generated from a fixed alphabet, so escaping changes
    # nothing today. It is escaped anyway: the day that alphabet grows, this
    # should not become the thing that breaks the mail.
    password = html.escape(str(temporary_password))
    url = sign_in_url()

    button = (
        f'<tr><td align="center" style="padding:4px 32px 28px;">'
        f'<a href="{html.escape(url)}" '
        f'style="display:inline-block;background:{_BRAND};color:#ffffff;'
        f'text-decoration:none;font-weight:700;font-size:15px;'
        f'padding:13px 30px;border-radius:8px;">Go to NACC SYS</a>'
        f"</td></tr>"
    ) if url else ""

    html_body = (
        f'<!DOCTYPE html><html><body style="margin:0;padding:0;'
        f'background:{_PAPER};">'
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="background:{_PAPER};padding:24px 12px;">'
        f'<tr><td align="center">'
        f'<table role="presentation" width="100%" cellpadding="0" '
        f'cellspacing="0" style="max-width:560px;background:#ffffff;'
        f'border:1px solid {_BORDER};border-radius:14px;overflow:hidden;'
        f'font-family:Segoe UI,Roboto,Helvetica,Arial,sans-serif;">'

        # Header
        f'<tr><td style="background:{_BRAND};padding:22px 32px;">'
        f'<div style="color:#ffffff;font-size:17px;font-weight:700;'
        f'line-height:1.3;">NACC &ndash; RACCO 1</div>'
        f'<div style="color:#c3cbf5;font-size:12px;padding-top:3px;">'
        f'National Authority for Child Care</div></td></tr>'

        # Greeting
        f'<tr><td style="padding:30px 32px 0;">'
        f'<div style="font-size:19px;font-weight:700;color:{_INK};">'
        f'Your temporary password</div>'
        f'<p style="margin:14px 0 0;font-size:15px;line-height:1.6;'
        f'color:{_INK};">Hello {name},</p>'
        f'<p style="margin:10px 0 0;font-size:15px;line-height:1.6;'
        f'color:{_MUTED};">An administrator has set up access for you. Use the '
        f'password below to sign in for the first time.</p></td></tr>'

        # The password
        f'<tr><td style="padding:22px 32px 0;">'
        f'<div style="background:{_TINT};border:1px solid #d5dcfa;'
        f'border-radius:10px;padding:18px 20px;text-align:center;">'
        f'<div style="font-size:11px;letter-spacing:.09em;font-weight:700;'
        f'color:{_BRAND_DARK};text-transform:uppercase;">Temporary password'
        f'</div>'
        f'<div style="font-family:Consolas,Menlo,Courier New,monospace;'
        f'font-size:25px;font-weight:700;letter-spacing:.06em;color:{_INK};'
        f'padding-top:9px;word-break:break-all;">{password}</div>'
        f'</div></td></tr>'

        # Steps
        f'<tr><td style="padding:22px 32px 0;">'
        f'<div style="font-size:14px;font-weight:700;color:{_INK};">'
        f'What to do next</div>'
        f'<ol style="margin:9px 0 0;padding-left:20px;font-size:14px;'
        f'line-height:1.75;color:{_MUTED};">'
        f'<li>Open NACC SYS and sign in with this email address.</li>'
        f'<li>Enter the temporary password above.</li>'
        f'<li>Choose your own password when prompted.</li>'
        f'</ol></td></tr>'

        f'<tr><td style="padding:24px 32px 0;"></td></tr>'
        f"{button}"

        # Security note
        f'<tr><td style="padding:0 32px 26px;">'
        f'<div style="background:{_PAPER};border-left:3px solid {_BRAND};'
        f'border-radius:0 8px 8px 0;padding:13px 16px;font-size:13px;'
        f'line-height:1.65;color:{_MUTED};">'
        f'This password works once, until you set your own. Do not share it '
        f'with anyone &mdash; not even a colleague. If you were not expecting '
        f'this email, tell your administrator, and do not use the password.'
        f'</div></td></tr>'

        # Footer
        f'<tr><td style="background:{_PAPER};border-top:1px solid {_BORDER};'
        f'padding:15px 32px;font-size:11.5px;line-height:1.6;color:{_MUTED};">'
        f'Automated message from NACC SYS. Replies are not monitored.'
        f'</td></tr>'

        f"</table></td></tr></table></body></html>"
    )

    # Plain text alongside the HTML: some clients show it, and a message with
    # no text part is likelier to be filtered as spam.
    text_body = (
        f"Your temporary password\n\n"
        f"Hello {getattr(user, 'fullname', '') or 'there'},\n\n"
        f"An administrator has set up access for you.\n\n"
        f"Temporary password: {temporary_password}\n\n"
        f"1. Open NACC SYS and sign in with this email address.\n"
        f"2. Enter the temporary password above.\n"
        f"3. Choose your own password when prompted.\n\n"
        + (f"{url}\n\n" if url else "")
        + "This password works once, until you set your own. Do not share it "
          "with anyone. If you were not expecting this email, tell your "
          "administrator and do not use the password.\n\n"
          "Automated message from NACC SYS. Replies are not monitored."
    )

    return {
        "sender": {
            "name": settings.BREVO_SENDER_NAME,
            "email": settings.BREVO_SENDER_EMAIL,
        },
        "to": [{"email": user.email, "name": name}],
        "subject": "Your NACC SYS temporary password",
        "htmlContent": html_body,
        "textContent": text_body,
    }


def send_temporary_password_notification(user, temporary_password):
    """Queue a temporary password email without blocking the account change.

    True means the message was handed to a background thread, NOT that it
    arrived: the Brevo call happens after this returns and its result is not
    read back. A caller that reports this to a person must say "queued", never
    "sent" — an administrator who believes an email went out is an
    administrator who does not hand the password over.
    """
    if not getattr(user, "email", ""):
        return False
    if not settings.BREVO_API_KEY:
        logger.warning(
            "BREVO_API_KEY is not set; skipped temporary password email for user %s",
            user.id)
        return False

    payload = build_temporary_password_payload(user, temporary_password)
    transaction.on_commit(
        lambda: threading.Thread(
            target=_post, args=(payload, "temporary password email"),
            daemon=True).start())
    return True


def send_verification_code(email, code):
    """Mail a sign-up verification code, and say whether it was accepted.

    Sent synchronously, unlike the notifications above: the applicant is
    sitting in front of the form waiting for it, and a failure they are not
    told about is a form that appears to have worked and has not.

    With no BREVO_API_KEY the code goes to the log instead, the same way the
    SMS console provider works — a missing key should not crash a sign-up, and
    it should not silently do nothing either.
    """
    if not email:
        return False
    if not settings.BREVO_API_KEY:
        logger.warning("EMAIL (console) verification code for %s: %s", email, code)
        return True
    payload = {
        "sender": {
            "name": settings.BREVO_SENDER_NAME,
            "email": settings.BREVO_SENDER_EMAIL,
        },
        "to": [{"email": email}],
        "subject": "Your NACC SYS verification code",
        "htmlContent": (
            "<h2>Confirm your email address</h2>"
            f"<p>Your verification code is <strong>{html.escape(code)}</strong>.</p>"
            "<p>It expires in 15 minutes.</p>"
            "<p>If you did not request access to NACC SYS, you can ignore "
            "this message.</p>"
        ),
    }
    return _post(payload, "sign-up verification code")
