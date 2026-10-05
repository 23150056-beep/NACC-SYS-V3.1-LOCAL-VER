"""Abuse control for the two open sign-up paths.

POST /api/auth/google/ and POST /api/auth/signup/ are the only endpoints in
this system that both accept anonymous traffic and write a row. Neither has a
domain allowlist doing any work — RACCO I staff use personal Google accounts —
which means anyone on the internet can create approval requests.

Both paths share these limits, and they must: two doors with one budget
between them, or the cheaper door is simply the one an abuser uses.

The harm is not storage. It is that an administrator wading through a hundred
plausible-looking fake requests eventually approves one by mistake, and that
approval is the only access control standing between a stranger and child case
records. So the limits below exist to keep the queue reviewable by a human, not
to save disk.

Two limits, deliberately different in kind:

* Per-IP, in the cache. Cheap and self-resetting, and it catches a naive flood.
  It inherits LocMemCache's weaknesses (see lockout.py): counters are
  per-worker and wiped on restart, and this deployment runs two Gunicorn
  workers, so the effective allowance is roughly double the number below. A
  speed bump, not a wall.
* A global ceiling on outstanding requests, counted in the database. Durable,
  shared across workers, and survives restarts — because it asks the source of
  truth rather than a cache. This is the one that actually holds, and it is
  why the weaker limit above is acceptable.

Both are deliberately generous. A limit that blocks a genuine new psychologist
on their first day is a worse failure than a queue with some junk in it: the
junk is visible and reversible, the lockout is neither.
"""

from django.conf import settings
from django.core.cache import cache

_PREFIX = "signup:"


def _key(ip):
    return f"{_PREFIX}count:{ip or 'unknown'}"


def _window_seconds():
    return settings.SIGNUP_WINDOW_MINUTES * 60


def attempts_from(ip):
    return cache.get(_key(ip)) or 0


def ip_is_throttled(ip):
    """Whether this address has already opened its allowance of requests."""
    return attempts_from(ip) >= settings.SIGNUP_MAX_PER_IP


def register_attempt(ip):
    """Count one newly created request against this address.

    Only successful creations are counted, not every call to the endpoint: a
    returning applicant checking whether they have been approved yet must not
    burn through the allowance and lock themselves out of their own status.
    """
    count = attempts_from(ip) + 1
    cache.set(_key(ip), count, timeout=_window_seconds())
    return count


def queue_is_full():
    """Whether outstanding requests have reached the global ceiling.

    Imported here rather than at module scope to keep this module importable
    from settings-adjacent code without dragging in the model layer.
    """
    from accounts.models import User

    return (User.objects.filter(status=User.PENDING).count()
            >= settings.SIGNUP_MAX_PENDING)


# --------------------------------------------------------------------------
# Asking for the sign-up email code again
# --------------------------------------------------------------------------
#
# A third open door, and it writes no request, so the two limits above do not
# fit it as they stand. The creation counter is for creations: charging a
# resend to it would let an applicant who asked for a few new codes use up the
# allowance for signing up a colleague from the same office address, and five
# sign-ups from one address would stop every one of them asking for a code.
# The ceiling on outstanding requests counts rows, and a resend adds none.
#
# So this is the same kind of limit - per IP, in the cache, the same window -
# kept apart under its own key. The per-request limits (once a minute, five an
# hour, in the database) are what protect any one address; this is the speed
# bump for one source working through many.

def _resend_key(ip):
    return f"{_PREFIX}resend:{ip or 'unknown'}"


def resend_is_throttled(ip):
    """Whether this address has already used its allowance of code requests."""
    return (cache.get(_resend_key(ip)) or 0) >= settings.SIGNUP_RESEND_MAX_PER_IP


def register_resend(ip):
    """Count one request for a new code against this address.

    Counted whatever came of it - an unknown address, a confirmed one - so
    that what a request costs does not depend on whether the address applied.
    """
    count = (cache.get(_resend_key(ip)) or 0) + 1
    cache.set(_resend_key(ip), count, timeout=_window_seconds())
    return count


# --------------------------------------------------------------------------
# Guessing at the sign-up email code
# --------------------------------------------------------------------------
#
# Each request's code has its own five guesses, and that is exactly what a
# stranger can spend: five wrong guesses at somebody else's address burn their
# code. So wrong guesses are counted per source too, in the cache, under their
# own key. Over the allowance the view answers the usual refusal without
# looking at the code at all. Counted on failures only, so an applicant who
# types the right code is never charged.

def _confirm_key(ip):
    return f"{_PREFIX}confirm:{ip or 'unknown'}"


def confirm_is_throttled(ip):
    """Whether this address has already used its allowance of wrong codes."""
    return (cache.get(_confirm_key(ip)) or 0) >= settings.SIGNUP_CONFIRM_MAX_PER_IP


def register_confirm_failure(ip):
    """Count one wrong code (or one refusal) against this address."""
    count = (cache.get(_confirm_key(ip)) or 0) + 1
    cache.set(_confirm_key(ip), count, timeout=_window_seconds())
    return count
