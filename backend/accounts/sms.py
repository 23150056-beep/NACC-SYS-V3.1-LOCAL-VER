"""Sending a text message, without the rest of the system knowing who sends it.

Everything above this module calls `send_sms(number, text, description)` and
gets back True or False. Which gateway carries it is a setting, and changing
it is a line of configuration rather than an edit to every caller — the same
shape `children/notifications.py` gives Brevo, for the same reason.

That indirection earns its place immediately here. The provider question is
genuinely open: a global CPaaS charges roughly ₱10 a message into the
Philippines because international A2P termination is expensive, while a local
aggregator on domestic interconnects is well under ₱1 for the same delivery.
Whoever is chosen first is unlikely to be chosen forever.

Two rules the callers do not get to break:

**No case data, ever.** SMS is unencrypted, passes through a telco, and lands
on a lock screen anybody standing nearby can read. The rule the email already
follows — a case number, never a child's name — is stricter here, not looser.

**Never on the request thread.** Same reasoning as the mail: a slow gateway
must not be able to hold a save open. `queue_sms` hands the send to a daemon
thread after the transaction commits.
"""
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.db import transaction

from accounts.phone import normalise_ph_mobile, InvalidPhilippineMobile

logger = logging.getLogger(__name__)

_TIMEOUT = 20

# Longer than this and the gateway bills two messages, or splits it and lands
# out of order. Every message this system sends is one line; the cap is here so
# that stays true when somebody edits the wording later.
SINGLE_SEGMENT = 160


class SmsResult:
    """Whether it went, and what to say if it did not.

    A bare False was not enough: the settings-page test button has to be able
    to print the gateway's own words. A silent failure is exactly how the mail
    integration cost a day.
    """

    def __init__(self, ok, detail=""):
        self.ok = ok
        self.detail = detail

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return f"SmsResult(ok={self.ok}, detail={self.detail!r})"


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

def _send_console(number, text, description):
    """The default, and what runs locally and in tests.

    Writes the message to the log instead of a gateway. Not a stub to be
    replaced later — it is what should happen when no gateway is configured,
    because the alternative is either a crash or a silent no-op, and both of
    those are worse than a line in the log saying exactly what would have been
    sent.
    """
    logger.info("SMS (console) to %s — %s: %s", number, description, text)
    return SmsResult(True, f"Written to the log. No gateway is configured, so "
                           f"nothing was sent to {number}.")


def _send_semaphore(number, text, description):
    """Semaphore — a Philippine aggregator, reaching every local network over
    domestic interconnects.

    Its API is form-encoded rather than JSON, and it answers 200 with a body
    describing a per-message failure, so the status code alone does not mean
    the message went.
    """
    api_key = settings.SMS_API_KEY
    if not api_key:
        return SmsResult(False, "SMS_API_KEY is not set, so there is nothing "
                                "to authenticate with.")
    payload = {
        "apikey": api_key,
        # Semaphore accepts 09XXXXXXXXX or the E.164 form; the stored form is
        # sent as-is so what is logged matches what is in the database.
        "number": number,
        "message": text,
    }
    if settings.SMS_SENDER_NAME:
        payload["sendername"] = settings.SMS_SENDER_NAME

    request = urllib.request.Request(
        settings.SMS_ENDPOINT,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            body = response.read().decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:                                        # noqa: BLE001
            detail = "(no response body)"
        logger.error("SMS %s failed: HTTP %s — %s", description, exc.code, detail)
        return SmsResult(False, _explain(exc.code, detail))
    except urllib.error.URLError as exc:
        logger.exception("SMS %s failed: could not reach the gateway", description)
        return SmsResult(False, f"Could not reach the SMS gateway at "
                                f"{settings.SMS_ENDPOINT} ({exc.reason}).")
    except Exception:                                            # noqa: BLE001
        logger.exception("Unexpected error sending SMS %s", description)
        return SmsResult(False, "Unexpected error sending the message. The "
                                "server log has the traceback.")

    # A 200 with an error in the body is the case worth handling: the gateway
    # accepted the request and refused the message.
    try:
        parsed = json.loads(body)
    except ValueError:
        logger.warning("SMS %s: gateway replied with non-JSON: %s", description, body)
        return SmsResult(True, f"Gateway accepted it, and replied: {body}")

    entries = parsed if isinstance(parsed, list) else [parsed]
    for entry in entries:
        status = str(entry.get("status", "")).lower()
        if status in ("failed", "refunded"):
            logger.error("SMS %s refused by the gateway: %s", description, body)
            return SmsResult(False, f"The gateway refused the message: {body}")
    logger.info("SMS %s accepted by the gateway", description)
    return SmsResult(True, f"Accepted by the gateway for delivery to {number}.")


def _explain(code, detail):
    """The gateway's own words, plus what they usually mean."""
    if code in (401, 403):
        return (f"The gateway rejected the credentials (HTTP {code}). Check "
                f"SMS_API_KEY. It said: {detail}")
    if code == 400 and "sender" in detail.lower():
        return (f"The gateway rejected the sender name "
                f"'{settings.SMS_SENDER_NAME}'. It has to be registered with "
                f"them before it can be used. It said: {detail}")
    if code == 402:
        return f"The account is out of credit. It said: {detail}"
    return f"The gateway answered HTTP {code}: {detail}"


PROVIDERS = {
    "console": _send_console,
    "semaphore": _send_semaphore,
}


# --------------------------------------------------------------------------
# The interface everything else uses
# --------------------------------------------------------------------------

def send_sms(number, text, description="message"):
    """Send one message now, and say what happened. Never raises.

    `description` names which message this is, for the log. More than one kind
    goes through here, and a failure that does not say which leaves whoever is
    reading the log looking at the wrong feature.
    """
    try:
        number = normalise_ph_mobile(number)
    except InvalidPhilippineMobile as exc:
        logger.warning("SMS %s not sent: %s", description, exc)
        return SmsResult(False, str(exc))
    if not number:
        return SmsResult(False, "No mobile number on file for this account.")

    text = (text or "").strip()
    if not text:
        return SmsResult(False, "Refusing to send an empty message.")
    if len(text) > SINGLE_SEGMENT:
        # Truncating beats splitting: two texts arriving out of order say
        # something the sender did not write.
        logger.warning("SMS %s was %d characters and has been trimmed to %d",
                       description, len(text), SINGLE_SEGMENT)
        text = text[:SINGLE_SEGMENT - 1].rstrip() + "…"

    provider = PROVIDERS.get(settings.SMS_PROVIDER, _send_console)
    return provider(number, text, description)


def queue_sms(number, text, description="message"):
    """Send after the current transaction commits, on a daemon thread.

    Returns whether it was queued, not whether it arrived — the caller is
    inside a request and the gateway is not its problem. Mirrors how the mail
    is queued, deliberately: two notification paths that behave differently
    under load is one more thing to hold in your head.
    """
    if not number:
        return False

    def _fire():
        threading.Thread(
            target=send_sms, args=(number, text, description), daemon=True
        ).start()

    transaction.on_commit(_fire)
    return True
