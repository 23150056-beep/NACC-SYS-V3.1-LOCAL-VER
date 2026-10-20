"""Login brute-force protection (cache-based, no DB models).

Failed-login counters and locks live in Django's default cache
(LocMemCache — see CACHES in settings, which is unset so Django falls
back to it). That means counters reset on process restart and are NOT
shared across processes/workers. Acceptable for this system's
single-process office deployment; if this is ever run behind multiple
app-server workers, point CACHES at Redis or Memcached instead so every
worker sees the same counters.
"""

import re
import time

from django.conf import settings
from django.core.cache import cache

_PREFIX = "login-lock:"

# What the audit trail and the ISA's console say in place of a sign-in name
# that is not an email address.
NOT_AN_EMAIL = "an unrecognised sign-in name"
# The letters an address is made of: word characters (so Ñ and the rest count),
# and the few marks that turn up in a local part. A domain is dot-separated
# labels of letters, digits and hyphens. Anything wider (a colon, a bracket, a
# semicolon, an exclamation mark) is a sentence or a password, not an address.
_EMAIL_SHAPE = re.compile(r"[\w.%+'-]+@[\w-]+(?:\.[\w-]+)+")

# The audit line written when ONE ADDRESS is locked out, as opposed to one
# sign-in name: after LOGIN_IP_MAX_ATTEMPTS failures from it, whoever signs in
# from there is turned away, the ISA included. It names no address and no
# person, so it can be a fixed sentence the console picks out whole.
ADDRESS_LOCK_LABEL = "Sign-ins from one address locked after repeated failures"


def readable_sign_in_name(typed):
    """What may be written down about a name typed into the sign-in box.

    The box is open to anyone, and people type the wrong thing into it: a
    password, a sentence, a blank. Whatever was typed goes into the audit line
    of a lockout and from there onto the ISA's screen, so only something that
    looks like an email address (letters, digits and the few marks an address
    uses, an at sign and a dotted host after it, short enough for the audit
    column) is kept as typed. Everything else is `NOT_AN_EMAIL`.

    A password that happens to look like an address ("Summer2024@home.net")
    cannot be told from one, and is kept: that much of the risk stays.
    """
    text = typed.strip() if isinstance(typed, str) else ""
    if len(text) <= 120 and text.isprintable() and _EMAIL_SHAPE.fullmatch(text):
        return text
    return NOT_AN_EMAIL


def _norm_email(email):
    # The sign-in box is open to anyone and a JSON body can put a list, a
    # number or an object where the address goes. Those have no address to
    # key a lock on, so they share the key a blank one has.
    return email.strip().lower() if isinstance(email, str) else ""


def client_ip(request):
    """Client IP used for lockout keying.

    The office deployment has no reverse proxy in front of Django, so
    REMOTE_ADDR is the real client address. X-Forwarded-For is
    deliberately NOT trusted — a client can set that header to whatever
    it likes, which would let an attacker pick their own lockout bucket.
    """
    return request.META.get("REMOTE_ADDR", "")


def _combo_counter_key(email, ip):
    return f"{_PREFIX}count:{_norm_email(email)}:{ip}"


def _combo_lock_key(email, ip):
    return f"{_PREFIX}lock:{_norm_email(email)}:{ip}"


def _ip_counter_key(ip):
    return f"{_PREFIX}ipcount:{ip}"


def _ip_lock_key(ip):
    return f"{_PREFIX}iplock:{ip}"


def _window_seconds():
    return settings.LOGIN_LOCKOUT_MINUTES * 60


def _remaining(lock_key):
    """Seconds left on a lock key, or 0 if it's unset/already expired."""
    unlock_at = cache.get(lock_key)
    if not unlock_at:
        return 0
    remaining = int(round(unlock_at - time.time()))
    return remaining if remaining > 0 else 0


def is_locked(email, ip):
    """Whether a login attempt for this email+IP should be blocked right now.

    Checks the email+IP lock (this specific combo failed too often) and
    the IP-wide lock (this IP failed too often across many different
    emails — a spray attack). Returns (locked, retry_after_seconds); when
    both locks are active, retry_after is the longer of the two.
    """
    retry_after = max(_remaining(_combo_lock_key(email, ip)), _remaining(_ip_lock_key(ip)))
    return retry_after > 0, retry_after


def _bump(counter_key):
    """Increment a rolling-window counter, creating it on first use.

    LocMemCache's incr() raises ValueError on a missing key, and two
    near-simultaneous first failures could both race to create it. This
    is a single-process deployment, so a plain get-then-set is enough —
    worst case on a genuine race is losing one increment, which doesn't
    meaningfully weaken the lockout.
    """
    count = (cache.get(counter_key) or 0) + 1
    cache.set(counter_key, count, timeout=_window_seconds())
    return count


def register_failure(email, ip):
    """Record a failed login attempt against both the combo and IP counters.

    Returns (locked, retry_after_seconds, engaged). `engaged` names the locks
    that were set by THIS attempt: "account" for the email+IP combination,
    "address" for the IP-wide lock, both when one attempt crossed both
    thresholds, and an empty tuple otherwise. It is only non-empty on the
    attempt that just crossed a threshold, so the caller can log each lockout
    exactly once — once a lock exists, is_locked() blocks the request before
    this function is called again for the same combo/IP, so it never fires
    twice for the same lockout.
    """
    combo_count = _bump(_combo_counter_key(email, ip))
    ip_count = _bump(_ip_counter_key(ip))

    engaged = []
    retry_after = 0
    unlock_at = time.time() + _window_seconds()

    if combo_count >= settings.LOGIN_MAX_ATTEMPTS:
        if cache.add(_combo_lock_key(email, ip), unlock_at, timeout=_window_seconds()):
            engaged.append("account")
        retry_after = max(retry_after, _window_seconds())

    if ip_count >= settings.LOGIN_IP_MAX_ATTEMPTS:
        if cache.add(_ip_lock_key(ip), unlock_at, timeout=_window_seconds()):
            engaged.append("address")
        retry_after = max(retry_after, _window_seconds())

    return retry_after > 0, retry_after, tuple(engaged)


def clear_failures(email, ip):
    """Reset the email+IP counter on a successful login.

    The IP-wide counter is deliberately left alone — one successful login
    from an IP shouldn't reset the count for a spray attack still running
    against other accounts from that same IP.
    """
    cache.delete(_combo_counter_key(email, ip))
