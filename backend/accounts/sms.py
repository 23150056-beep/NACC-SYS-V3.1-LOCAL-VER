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
        _endpoint_for("semaphore"),
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


def _send_philsms(number, text, description):
    """PhilSMS - a Philippine aggregator on the same domestic interconnects.

    Chosen as the second gateway because opening a Semaphore account turned
    out not to be a given, which is the situation this module was shaped for.

    Its API disagrees with Semaphore in three ways that all fail quietly:
    the key is a bearer token rather than a body field, the payload is JSON
    rather than form-encoded, and the number is documented WITHOUT the leading
    plus. Like Semaphore it answers HTTP 200 and puts a refusal in the body,
    so the status code alone still does not mean the message went.
    """
    api_key = settings.SMS_API_KEY
    if not api_key:
        return SmsResult(False, "SMS_API_KEY is not set, so there is nothing "
                                "to authenticate with.")

    payload = {
        # Stored as +639XXXXXXXXX; PhilSMS documents 639XXXXXXXXX.
        "recipient": number.lstrip("+"),
        "message": text,
        "type": "plain",
    }
    if settings.SMS_SENDER_NAME:
        payload["sender_id"] = settings.SMS_SENDER_NAME

    request = urllib.request.Request(
        _endpoint_for("philsms"),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    body, failure = _post(request, description)
    if failure is not None:
        return failure

    try:
        parsed = json.loads(body)
    except ValueError:
        logger.warning("SMS %s: gateway replied with non-JSON: %s", description, body)
        return SmsResult(True, f"Gateway accepted it, and replied: {body}")

    if str(parsed.get("status", "")).lower() == "error":
        message = parsed.get("message") or body
        logger.error("SMS %s refused by the gateway: %s", description, body)
        return SmsResult(False, f"The gateway refused the message: {message}")
    logger.info("SMS %s accepted by the gateway", description)
    return SmsResult(True, f"Accepted by the gateway for delivery to {number}.")


def _send_textbee(number, text, description):
    """textbee - the message leaves from a phone you own, on your own SIM.

    Not an aggregator. An Android app holds the SIM and the API is a relay
    that tells it what to send, so there is no business account to open, no
    sender name to register and no minimum top-up - which is the whole reason
    it is here. What arrives shows the handset's own number rather than a
    short name, and that is the trade.

    Its wire format agrees with neither aggregator: the key is an x-api-key
    header, and `recipients` is an ARRAY. A bare string there is valid JSON,
    is accepted, and reaches nobody.
    """
    api_key = settings.SMS_API_KEY
    if not api_key:
        return SmsResult(False, "SMS_API_KEY is not set, so there is nothing "
                                "to authenticate with.")

    payload = {"recipients": [number], "message": text}
    if settings.SMS_DEVICE_ID:
        payload["deviceId"] = settings.SMS_DEVICE_ID

    request = urllib.request.Request(
        _endpoint_for("textbee"),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "x-api-key": api_key,
        },
        method="POST",
    )
    body, failure = _post(request, description)
    if failure is not None:
        return failure

    try:
        parsed = json.loads(body)
    except ValueError:
        return SmsResult(True, f"Gateway accepted it, and replied: {body}")

    # It answers 2xx for an accepted relay. A body that explicitly says
    # otherwise is still treated as a refusal, because the alternative is
    # reporting an unsent message as sent - which is the failure this whole
    # module is arranged around.
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if isinstance(data, dict) and data.get("success") is False:
        return SmsResult(False, f"The gateway refused the message: {body}")
    if isinstance(parsed, dict) and parsed.get("success") is False:
        return SmsResult(False, f"The gateway refused the message: {body}")
    logger.info("SMS %s handed to the phone", description)
    return SmsResult(True, f"Handed to your linked phone for delivery to {number}.")


def _post(request, description):
    """Send it, and turn a transport failure into an SmsResult.

    Returns (body, None) or (None, SmsResult). Shared, because two gateways
    fail over the network in exactly the same ways and only disagree about
    what a successful body looks like.
    """
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return response.read().decode("utf-8", "replace")[:400], None
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:                                        # noqa: BLE001
            detail = "(no response body)"
        logger.error("SMS %s failed: HTTP %s - %s", description, exc.code, detail)
        return None, SmsResult(False, _explain(exc.code, detail))
    except urllib.error.URLError as exc:
        logger.exception("SMS %s failed: could not reach the gateway", description)
        return None, SmsResult(False, f"Could not reach the SMS gateway at "
                                      f"{request.full_url} ({exc.reason}).")
    except Exception:                                            # noqa: BLE001
        logger.exception("Unexpected error sending SMS %s", description)
        return None, SmsResult(False, "Unexpected error sending the message. "
                                      "The server log has the traceback.")


# Each gateway's own URL. SMS_ENDPOINT overrides, but it must not have to be
# set: it used to default to Semaphore's URL for everybody, so choosing
# PhilSMS and leaving it alone would have posted JSON at Semaphore and read
# the 400 as a PhilSMS problem.
DEFAULT_ENDPOINTS = {
    "semaphore": "https://api.semaphore.co/api/v4/messages",
    "philsms": "https://app.philsms.com/api/v3/sms/send",
    "textbee": "https://api.textbee.dev/api/v1/gateway/send-sms",
}


def _endpoint_for(provider):
    return settings.SMS_ENDPOINT or DEFAULT_ENDPOINTS.get(provider, "")


# Where each gateway will confirm the key and the balance for free. Not
# derived from SMS_ENDPOINT: that setting exists to point the SENDER somewhere
# else, and quietly rewriting a URL to guess a second one is how you end up
# checking a different account from the one you send with.
CHECK_ENDPOINTS = {
    "philsms": "https://app.philsms.com/api/v3/balance",
    "semaphore": "https://api.semaphore.co/api/v4/account",
    # Not a balance. What runs out on this one is a phone.
    "textbee": "https://api.textbee.dev/api/v1/gateway/devices",
}


def check_gateway():
    """Ask the gateway who we are and what is left, without sending anything.

    PhilSMS gives five free credits and has no sandbox. Five is exactly enough
    for one pass of each message this system sends, so they are the wrong
    thing to spend discovering that a key was pasted with a trailing space.
    Every gateway answers this question for nothing.

    It authenticates exactly the way the sender does on purpose. A check that
    passes with a key the sender would be refused with is worse than no check.
    """
    provider = settings.SMS_PROVIDER
    url = CHECK_ENDPOINTS.get(provider)
    if not url:
        return SmsResult(False, "There is no gateway to check: SMS_PROVIDER is "
                                f"{provider or 'unset'}, so messages are written "
                                "to the log and nothing is sent.")
    api_key = settings.SMS_API_KEY
    if not api_key:
        return SmsResult(False, "SMS_API_KEY is not set, so there is nothing "
                                "to authenticate with.")

    headers = {"accept": "application/json"}
    if provider == "philsms":
        headers["authorization"] = f"Bearer {api_key}"
    elif provider == "textbee":
        headers["x-api-key"] = api_key
    else:
        url = f"{url}?{urllib.parse.urlencode({'apikey': api_key})}"

    body, failure = _post(urllib.request.Request(url, headers=headers, method="GET"),
                          "gateway check")
    if failure is not None:
        return failure

    try:
        parsed = json.loads(body)
    except ValueError:
        return SmsResult(True, f"The gateway answered: {body}")

    if isinstance(parsed, dict) and str(parsed.get("status", "")).lower() == "error":
        return SmsResult(False, "The gateway refused the key: "
                                f"{parsed.get('message') or body}")

    data = parsed.get("data") if isinstance(parsed, dict) else parsed
    if provider == "textbee":
        phones = data if isinstance(data, list) else []
        if not phones:
            return SmsResult(False, "The key works, but no phone is linked to "
                                    "it. Open the textbee app on the handset "
                                    "and pair it, or nothing can be sent.")
        names = ", ".join(str(p.get("model") or p.get("_id") or "device")
                          for p in phones if isinstance(p, dict))
        return SmsResult(True, f"The key works. {len(phones)} phone(s) linked"
                               f"{': ' + names if names else ''}.")
    if isinstance(data, dict) and data:
        summary = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in data.items())
    else:
        summary = body
    return SmsResult(True, f"The key works. {summary}")


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
    "philsms": _send_philsms,
    "textbee": _send_textbee,
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
