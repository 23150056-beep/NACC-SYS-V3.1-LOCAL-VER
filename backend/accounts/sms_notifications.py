"""The three messages this system sends by text.

Each one is a nudge to open the system, never the thing itself. That is the
whole design, and it comes from what a text message is: unencrypted, carried
by a telco, sitting on a lock screen that anyone beside the person can read.

So none of these carries a child's name, a case detail, or a password. The
email already refuses to carry a name for the same reason (see
children/notifications.py); a text is the weaker channel, so the rule is
applied harder rather than relaxed.

An unverified number gets nothing. A number somebody typed is a number that
might be a typo, and a typo is a stranger's handset.
"""
import logging
import secrets

from django.conf import settings
from django.core.cache import cache

from accounts.sms import queue_sms, send_sms

logger = logging.getLogger(__name__)

# How long a verification code is good for, and how many tries it gets. Short
# enough that a code read off a screen months later is useless; long enough
# that a text arriving slowly is not.
CODE_TTL_SECONDS = 10 * 60
CODE_MAX_ATTEMPTS = 5


def _deliverable(user):
    """The number to text, or "" — a number nobody has proved they can receive
    at is not a destination."""
    number = getattr(user, "phone", "") or ""
    if not number:
        return ""
    if not getattr(user, "phone_verified", False):
        logger.info("Skipped SMS to %s: number on file is not verified", user.pk)
        return ""
    return number


# --------------------------------------------------------------------------
# 1. An administrator issued a temporary password
# --------------------------------------------------------------------------

def notify_temporary_password(user):
    """Tell them a password is waiting, not what it is.

    The password itself goes by email, where it is behind a login. Texting it
    would put a working credential on a lock screen, which is a downgrade from
    what this system already does, not a convenience.
    """
    number = _deliverable(user)
    if not number:
        return False
    return queue_sms(
        number,
        "NACC SYS: an administrator has issued you a temporary password. "
        "Check your email to sign in. Do not share it.",
        "temporary password notice",
    )


# --------------------------------------------------------------------------
# 2. A child was assigned to a psychologist
# --------------------------------------------------------------------------

def notify_new_assignment(child):
    """Tell the psychologist they have a new case. Not which child.

    The case number is enough to find it after signing in, and it means the
    child's name never reaches a telco.
    """
    psychologist = getattr(child, "assigned_psychologist", None)
    if psychologist is None:
        return False
    number = _deliverable(psychologist)
    if not number:
        return False
    return queue_sms(
        number,
        f"NACC SYS: a new case has been assigned to you (case {child.id}). "
        f"Sign in to review it.",
        "assignment notice",
    )


# --------------------------------------------------------------------------
# 3. Tomorrow's sessions
# --------------------------------------------------------------------------

def notify_session_reminder(psychologist, count, when="tomorrow"):
    """One message for the whole day, not one per appointment.

    Deliberate on two counts: five separate texts about five sessions is the
    kind of thing that gets a sender muted, and a per-appointment message
    would have to say which child it was about to be worth reading.
    """
    number = _deliverable(psychologist)
    if not number or count < 1:
        return False
    sessions = "session" if count == 1 else "sessions"
    return queue_sms(
        number,
        f"NACC SYS: you have {count} {sessions} scheduled {when}. "
        f"Sign in to see the schedule.",
        "session reminder",
    )


# --------------------------------------------------------------------------
# Proving a number belongs to the person who typed it
# --------------------------------------------------------------------------

def _code_key(user_id):
    return f"phone-verify:{user_id}"


def start_phone_verification(user, number):
    """Text a code to `number` and remember it for a few minutes.

    Sent synchronously, unlike the notifications: the person is sitting in
    front of the screen waiting for it, and if the gateway refuses they need
    to be told now rather than left watching a handset.
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    cache.set(_code_key(user.pk), {"code": code, "number": number, "tries": 0},
              CODE_TTL_SECONDS)
    result = send_sms(
        number,
        f"NACC SYS: your verification code is {code}. "
        f"It expires in 10 minutes.",
        "phone verification",
    )
    if not result.ok:
        cache.delete(_code_key(user.pk))
    return result


def confirm_phone_verification(user, submitted):
    """Check a code. Returns (ok, message).

    Counts attempts, because six digits is guessable given enough tries and
    this is the step that decides whether an account can be texted at all.
    """
    key = _code_key(user.pk)
    entry = cache.get(key)
    if not entry:
        return False, ("That code has expired. Ask for a new one.")

    entry["tries"] += 1
    if entry["tries"] > CODE_MAX_ATTEMPTS:
        cache.delete(key)
        return False, ("Too many wrong codes. Ask for a new one.")
    cache.set(key, entry, CODE_TTL_SECONDS)

    if (submitted or "").strip() != entry["code"]:
        left = CODE_MAX_ATTEMPTS - entry["tries"]
        return False, (f"That code is not right. {left} attempt"
                       f"{'' if left == 1 else 's'} left.")

    cache.delete(key)
    user.phone = entry["number"]
    user.phone_verified = True
    user.save(update_fields=["phone", "phone_verified", "updated_at"])
    return True, "Number verified. You will now get text notifications."
