"""Proving a typed email address exists.

The sign-up form verifies nothing, which matters at exactly one moment:
approval emails a temporary password. Approve a request whose address was
mistyped and the credential goes to whoever owns the typo, or nowhere at all
while the applicant waits for a mail that cannot arrive.

Deliberately the same shape as accounts/sms_notifications' phone verification
- a six-digit code, a short life, a limited number of guesses, held in a row
(EmailVerification) rather than the cache - because two ways of doing the same
thing is two things to get wrong. The cache lives in one process's memory:
under gunicorn a code stored by the worker that mailed it read as expired to
the worker that took the reply, and each worker kept its own count of guesses.

The code is looked up by address, not by a signed-in user: the person
confirming is not signed in, and cannot be, since a PENDING account is not
allowed to authenticate. The row itself belongs to the request's User row, so
it goes when the account does, and it records the address the code was mailed
to. It counts only while that is still the request's address and the request
is still pending: a code mailed to a typo an administrator has since corrected
cannot vouch for the corrected address, and a declined request has nothing
left to confirm.

Two differences from the phone code, both on purpose. Every refusal reads the
same (see confirm()). And the person asking for a new code is not signed in
either, so resend() is open and says nothing about whether it did anything.

A Google request never comes through here. Google verified the address, and
asking the applicant to prove it a second time would be ceremony.
"""
import logging
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import EmailVerification, User

logger = logging.getLogger(__name__)

TTL_SECONDS = 15 * 60
MAX_ATTEMPTS = 5
# The phone code's limits (sms_notifications), per request.
RESEND_SECONDS = 60
MAX_PER_HOUR = 5

REFUSAL = "That code is not right, or it has expired. Ask for a new one."
CONFIRMED = "Address confirmed. An administrator will review your request."
# What the resend door says, to everybody, whatever happened. It must read the
# same for an address with no request, one already confirmed, a declined one, a
# Google one and a limit being hit - anything else is a way to ask whether
# somebody has applied.
RESEND_REPLY = ("If that address has a request waiting to be confirmed, a "
                "new code has been sent to it. A new code can be asked for "
                "once a minute, five times an hour; asking sooner changes "
                "nothing.")


def _text(value):
    # This is fed straight from an open endpoint's JSON body, so it may be
    # anything; only a string can be an address or a code.
    return value.strip() if isinstance(value, str) else ""


def normalise(email):
    return _text(email).lower()


def send_verification_email(email, code):
    """Mail the code. Returns whether the gateway accepted it.

    Imported lazily so this module can be used - and tested - without pulling
    in the whole notification stack.
    """
    from children.notifications import send_verification_code

    return send_verification_code(email, code)


def start(user):
    """Issue a code for this request's address and remember it. Returns
    whether it went. Sign-up calls it for a new request and resend() for an
    existing one, so both count against the same limits.

    A code issued too soon after the last one, or beyond five an hour, is
    not issued and nothing is mailed. While one is outstanding it is mailed
    again, not replaced; a new one is issued only when none is. A refused send leaves no code
    outstanding and does not count against either limit.
    """
    now = timezone.now()
    address = normalise(user.email)
    with transaction.atomic():
        EmailVerification.objects.get_or_create(
            user=user, defaults={"email": address})
        row = EmailVerification.objects.select_for_update().get(user=user)
        before = {"last_sent_at": row.last_sent_at,
                  "window_started_at": row.window_started_at,
                  "sent_in_window": row.sent_in_window}
        if (row.window_started_at is None
                or now - row.window_started_at >= timedelta(hours=1)):
            row.window_started_at, row.sent_in_window = now, 0
        if (row.sent_in_window >= MAX_PER_HOUR
                or (row.last_sent_at is not None
                    and now - row.last_sent_at < timedelta(seconds=RESEND_SECONDS))):
            logger.info("Email verification code for %s not sent: asked for "
                        "too often", user.email)
            return False
        # A code that is still outstanding - mailed to this address, not burned,
        # not expired - is mailed again rather than replaced. A stranger who
        # knows an address can otherwise keep issuing new codes for it, and
        # each one throws away the one the applicant is holding. Its guesses
        # and its expiry stay as they were: asking again must not give the
        # code a longer life or a fresh set of guesses.
        reused = bool(row.code and row.email == address
                      and row.expires_at is not None and row.expires_at > now
                      and row.tries <= MAX_ATTEMPTS)
        if reused:
            code = row.code
        else:
            code = f"{secrets.randbelow(1_000_000):06d}"
            row.email, row.code, row.tries = address, code, 0
            row.expires_at = now + timedelta(seconds=TTL_SECONDS)
        row.last_sent_at = now
        row.sent_in_window += 1
        row.save()

    # Mailed after the claim commits, not inside it: SQLite locks the whole
    # database for a write and a gateway can take seconds.
    ok = send_verification_email(user.email, code)
    if not ok:
        # A refused send leaves no NEW code outstanding. A code that was
        # already there is the applicant's, and stays.
        undo = dict(before) if reused else {"code": "", **before}
        EmailVerification.objects.filter(pk=row.pk, code=code).update(**undo)
        logger.warning("Could not send an email verification code to %s", user.email)
    return ok


def resend(email):
    """Send a new code to a typed request that is still waiting to confirm.

    Returns whether one went, for the log and the tests; the view says the
    same thing either way (RESEND_REPLY). Only a PENDING request that signed
    up with a typed address and has not confirmed it: a Google request was
    verified by Google, a confirmed one has nothing left to prove, and a
    declined one is not coming back.
    """
    user = User.objects.filter(
        email__iexact=normalise(email), status=User.PENDING,
        google_sub__isnull=True, email_verified=False).first()
    if user is None:
        return False
    return start(user)


def confirm(email, submitted):
    """Check a code. Returns (ok, message).

    Every failure reads the same from outside - expired, wrong, or no such
    request - for the reason the sign-up form gives one refusal for every
    address already spoken for: a different answer here would turn this into
    a way to ask whether somebody works at the agency.

    It marks the request's address verified itself, in the same transaction
    that burns the code. A code counts only for a request still awaiting
    approval, and only while the address it was mailed to is still that
    request's address.
    """
    address = normalise(email)
    with transaction.atomic():
        row = (EmailVerification.objects.select_for_update()
               .select_related("user")
               .filter(email=address, user__email__iexact=address,
                       user__status=User.PENDING)
               .first())
        if (row is None or not row.code or row.expires_at is None
                or row.expires_at <= timezone.now()):
            return False, REFUSAL
        row.tries += 1
        if row.tries > MAX_ATTEMPTS:
            row.code = ""
            row.save(update_fields=["code", "tries"])
            return False, REFUSAL
        if _text(submitted) != row.code:
            row.save(update_fields=["tries"])
            return False, REFUSAL
        row.code = ""
        row.save(update_fields=["code", "tries"])
        user = row.user
        user.email_verified = True
        user.save(update_fields=["email_verified", "updated_at"])
    return True, CONFIRMED
