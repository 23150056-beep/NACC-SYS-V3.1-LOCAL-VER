"""Proving a typed email address exists.

The sign-up form verifies nothing, which matters at exactly one moment:
approval emails a temporary password. Approve a request whose address was
mistyped and the credential goes to whoever owns the typo, or nowhere at all
while the applicant waits for a mail that cannot arrive.

Deliberately the same shape as accounts/sms_notifications' phone verification
- a six-digit code in the cache, a short life, a limited number of guesses -
because two ways of doing the same thing is two things to get wrong. The code
is keyed by address rather than by user id: the person confirming is not
signed in, and cannot be, since a PENDING account is not allowed to
authenticate.

A Google request never comes through here. Google verified the address, and
asking the applicant to prove it a second time would be ceremony.
"""
import logging
import secrets

from django.core.cache import cache

logger = logging.getLogger(__name__)

TTL_SECONDS = 15 * 60
MAX_ATTEMPTS = 5


def code_key(email):
    return f"email-verify:{(email or '').strip().lower()}"


def send_verification_email(email, code):
    """Mail the code. Returns whether the gateway accepted it.

    Imported lazily so this module can be used - and tested - without pulling
    in the whole notification stack.
    """
    from children.notifications import send_verification_code

    return send_verification_code(email, code)


def start(email):
    """Issue a code for `email` and remember it. Returns whether it went."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    cache.set(code_key(email), {"code": code, "tries": 0}, TTL_SECONDS)
    ok = send_verification_email(email, code)
    if not ok:
        cache.delete(code_key(email))
        logger.warning("Could not send an email verification code to %s", email)
    return ok


def confirm(email, submitted):
    """Check a code. Returns (ok, message).

    Every failure reads the same from outside - expired, wrong, or no such
    request - for the reason the sign-up form gives one refusal for every
    address already spoken for: a different answer here would turn this into
    a way to ask whether somebody works at the agency.
    """
    key = code_key(email)
    entry = cache.get(key)
    refusal = "That code is not right, or it has expired. Ask for a new one."
    if not entry:
        return False, refusal

    entry["tries"] += 1
    if entry["tries"] > MAX_ATTEMPTS:
        cache.delete(key)
        return False, refusal
    cache.set(key, entry, TTL_SECONDS)

    if (submitted or "").strip() != entry["code"]:
        return False, refusal

    cache.delete(key)
    return True, "Address confirmed. An administrator will review your request."
