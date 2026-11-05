"""Configuration mistakes that otherwise look like a healthy deployment.

Pure predicates, so they can be tested without standing up a settings module.
`settings.py` calls them at import time: the first raises, the second warns,
and the third (`checked_site_url`, 23 Oct 2026) hands back a reason that
settings.py warns about and carries on without the value.

The first two come from a real deployment on 27 Aug 2026 that reported healthy
and was broken in three separate ways, none visible from outside. The shape
they share is the dangerous part — the service says "ok" and the application
does not work — so each one is made to say so instead.
"""
import ipaddress
import logging
import re
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def sqlite_fallback_is_a_mistake(*, debug, database_url, db_engine):
    """True when a deployment is about to get an ephemeral SQLite file.

    SQLite is the zero-config fallback so a fresh checkout and the test suite
    never block on a database server. That is right for development and wrong
    for anything deployed: the file lives on the container's disk, which most
    platforms destroy on every deploy and every wake from sleep. Records are
    written, reported saved, and then quietly disappear.

    `/healthz/` cannot catch this. SQLite *is* a working database, so the
    health check passes while the deployment is losing everything.
    """
    if debug:
        return False
    if str(database_url or "").strip():
        return False
    # The on-premises path predating V3 configures Postgres piecemeal rather
    # than through a URL.
    return str(db_engine or "sqlite").strip() != "postgres"


# The defaults in settings.py. A deployment still carrying these has not had
# its origins configured.
_LOCAL_ORIGINS = frozenset({
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:3000", "http://127.0.0.1:3000",
})


def cors_is_unconfigured(*, debug, origins):
    """True when a deployment has no origin a browser will actually come from.

    Not fatal — an API with no browser client is a legitimate configuration —
    but it is worth saying out loud, because the failure is silent and
    confusing: `curl` works perfectly while every request from the frontend is
    blocked before it is sent, and the browser console blames CORS in a message
    that does not name the setting.
    """
    if debug:
        return False
    real = [o for o in (origins or []) if o and o not in _LOCAL_ORIGINS]
    return not real


# What a host name may be made of. Anything else - a space, a backslash, a
# letter outside ASCII - is a typo or an attempt to make the address read as
# something it is not, and it is going into the href of an email.
_HOST_NAME = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?")


def _is_ipv6(host):
    # No "%": an IPv6 address may carry a zone ("fe80::1%eth0") and the parser
    # takes almost any text after it, which no browser can open and the
    # emails would print.
    if "%" in host:
        return False
    try:
        ipaddress.IPv6Address(host)
    except ValueError:
        return False
    return True


def checked_site_url(raw, *, debug):
    """(address, "") when `raw` is a usable SITE_URL, ("", reason) when it is
    not, and ("", "") when nothing was set.

    SITE_URL is the address people open NACC SYS at, and it goes into the
    button of every email. Two things make a bad one worse than none: the
    link is read on somebody else's machine, so a mistake is a dead button in
    mail nobody reports; and an address with a user name or a path in it can
    be made to open somewhere other than the sign-in page. So it must be a
    bare site: http or https, a host name, an optional port, and nothing more.

    * `http` is refused when `debug` is False. A deployed copy is https, and a
      sign-in page over plain http sends the password readable on the network.
    * A user name or password is refused (`https://colleague@elsewhere.example`
      reads as the first host and opens the second).
    * A query, a fragment or a path is refused: this is a site, not a page.
      The emails add their own path.
    * A trailing slash is dropped, so `https://nacc.example/` and
      `https://nacc.example` are one address.

    The reason is a sentence for a log line or a settings page. It never
    repeats any of `raw`: a user name and password typed into it would
    otherwise be written to the API log.

    Not fatal, and so not raised: the caller ignores a refused value and
    falls back to the old rule, because an email link is not worth a failed
    boot."""
    value = str(raw or "").strip()
    if not value:
        return "", ""
    try:
        parts = urlsplit(value)
        host = parts.hostname
        _ = parts.port                                # raises on a bad port
    except ValueError:
        return "", "It is not a web address, or its port is not a number."
    if parts.scheme not in ("http", "https"):
        return "", "It must start with http:// or https://."
    if not host:
        return "", "It needs a host name, such as nacc.example.gov.ph."
    if "@" in parts.netloc:
        return "", "It must not contain a user name or a password."
    if parts.query or parts.fragment or "?" in value or "#" in value:
        return "", "It must not carry a query or a fragment (nothing after ? or #)."
    if parts.path not in ("", "/"):
        return "", ("It must be the site itself, with no path after the host: "
                    "each email adds its own.")
    if not _HOST_NAME.fullmatch(host) and not _is_ipv6(host):
        return "", "Its host name has characters an address cannot contain."
    if not debug and parts.scheme != "https":
        return "", ("DJANGO_DEBUG is False, so it must start with https://: "
                    "a sign-in page over plain http sends passwords readable "
                    "on the network.")
    # Host names do not care about case, and the email asks people to compare
    # this address with the one they know, so it is written the usual way.
    return f"{parts.scheme}://{parts.netloc.lower()}", ""
