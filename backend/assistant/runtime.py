"""Where the model runtime may be, and a record of anyone moving it.

The drafting features send case notes, whole reports and a child's own
answers to the runtime named in `AssistantSetting.ollama_url`, and that field
is editable by the ISA. Nothing about being the ISA makes sending children's
words to another computer a decision they can take alone, so the address must
be this machine unless the deployment itself says otherwise
(`ASSISTANT_ALLOW_REMOTE_RUNTIME`, an environment variable, which only whoever
runs the server can set).

The hosted model has its own gate and ignores this field entirely
(services.get_ai_client).
"""
from urllib.parse import urlsplit

from django.conf import settings

from activity.models import ActivityLog
from activity.services import log_activity

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

REMOTE_RUNTIME_REFUSED = (
    "The model runtime must be on this machine (localhost, 127.0.0.1 or ::1): "
    "another address would send case notes and children's own answers to a "
    "different computer.")


def runtime_host(url):
    """The host of `url`, lower-cased, or '' when it has none or cannot be read.
    Never the port, the path or any credentials."""
    try:
        return (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def runtime_url_refusal(url):
    """The sentence that refuses this runtime address, or None when it may be used.

    Strict on purpose. Anything that makes the host ambiguous is refused rather
    than interpreted: user information ("http://localhost@elsewhere/"), a
    backslash (a browser reads it as a slash, a URL library does not), or any
    space or control character. The host must then be exactly one of the three
    loopback names - not "localhost.example.com", not "127.0.0.1.example.com".
    """
    if getattr(settings, "ASSISTANT_ALLOW_REMOTE_RUNTIME", False):
        return None
    text = str(url or "").strip()
    if not text or "\\" in text or any(c.isspace() or ord(c) < 32 for c in text):
        return REMOTE_RUNTIME_REFUSED
    try:
        parts = urlsplit(text)
        parts.port           # raises ValueError for a port that is not a number
    except ValueError:
        return REMOTE_RUNTIME_REFUSED
    if parts.scheme not in ("http", "https") or "@" in parts.netloc:
        return REMOTE_RUNTIME_REFUSED
    if (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        return REMOTE_RUNTIME_REFUSED
    return None


def record_runtime_change(actor, old_url, old_model, new_url, new_model):
    """One security entry for each of the address and the model that changed,
    with the ISA as actor. The address is logged as its host alone, old and new:
    the entry is read by whoever reads the audit trail, and a URL can carry a
    path or a credential."""
    if old_url != new_url:
        log_activity(
            actor, ActivityLog.UPDATED, ActivityLog.SECURITY, entity_type="Assistant",
            entity_label=f"runtime address: {runtime_host(old_url) or '(none)'} -> "
                         f"{runtime_host(new_url) or '(none)'}"[:255],
            entity_id=1)
    if old_model != new_model:
        log_activity(
            actor, ActivityLog.UPDATED, ActivityLog.SECURITY, entity_type="Assistant",
            entity_label=f"model: {old_model or '(none)'} -> {new_model or '(none)'}"[:255],
            entity_id=1)
