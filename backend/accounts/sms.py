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
import re
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
# ...but only while every character is in the GSM-7 alphabet. One character
# outside it - an em dash, a curly quote, the ellipsis this module used to
# append when trimming - turns the WHOLE message into UCS-2, where a segment
# is 70 characters. A 160-character message with one "…" in it is three.
UCS2_SEGMENT = 70
_GSM7 = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
         "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM7_ESCAPED = "^{}\\[~]|€\f"      # two septets each

# Where a one-time code goes in a message that carries one. Semaphore's code
# route fills it in itself; for every other gateway send_sms writes the code in.
OTP_PLACEHOLDER = "{otp}"


class SmsResult:
    """Whether it went, and what to say if it did not.

    A bare False was not enough: the settings-page test button has to be able
    to print the gateway's own words. A silent failure is exactly how the mail
    integration cost a day.

    `code` is set only for a message sent with a one-time code, and is the
    code the recipient was actually sent - see _read_semaphore_reply.
    """

    def __init__(self, ok, detail="", code=None):
        self.ok = ok
        self.detail = detail
        self.code = code

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


# Semaphore discards any message whose text begins with the word TEST: it is
# accepted, never sent, never charged, and nothing in the reply says so. No
# message here starts that way today; this is so a reworded one cannot
# vanish quietly.
_SEMAPHORE_DISCARDS = re.compile(r"^\s*test\b", re.IGNORECASE)


def _send_semaphore(number, text, description, otp_code=None):
    """Semaphore - a Philippine aggregator, reaching every local network over
    domestic interconnects.

    Its API is form-encoded rather than JSON, with the key as a body field.
    A message that went comes back as a list of message records, each with a
    message_id; anything else means nothing was queued, whatever the HTTP
    status said.

    With `otp_code` it goes to Semaphore's code route instead: `text` still
    holds {otp} and Semaphore writes in the code it is given. That route is
    kept apart from bulk traffic, which is what a code with a ten-minute life
    needs, and costs two credits a message rather than one.
    """
    api_key = settings.SMS_API_KEY
    if not api_key:
        return SmsResult(False, "SMS_API_KEY is not set, so there is nothing "
                                "to authenticate with.")
    if _SEMAPHORE_DISCARDS.match(text):
        logger.error("SMS %s not sent: it begins with TEST", description)
        return SmsResult(False, "Semaphore silently discards any message that "
                                "begins with the word TEST, so this one would "
                                "have been accepted and never sent. Reword its "
                                "opening.")

    payload = {
        "apikey": api_key,
        # Stored as +639XXXXXXXXX. Semaphore echoes recipients back as
        # 639XXXXXXXXX, so that is the form sent: nothing for form encoding
        # to turn into something else, and the log matches its dashboard.
        "number": number.lstrip("+"),
        "message": text,
    }
    if settings.SMS_SENDER_NAME:
        payload["sendername"] = settings.SMS_SENDER_NAME
    url = _endpoint_for("semaphore")
    if otp_code is not None:
        payload["code"] = otp_code
        url = SEMAPHORE_OTP_ENDPOINT

    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"content-type": "application/x-www-form-urlencoded",
                 "accept": "application/json"},
        method="POST",
    )
    body, failure = _post(request, description)
    if failure is not None:
        return failure
    return _read_semaphore_reply(body, number, description, otp_code)


def _read_semaphore_reply(body, number, description, otp_code=None):
    """Semaphore's answer to a send, read strictly.

    This used to call every JSON reply without a "Failed" status a success, so
    a refusal in a shape it did not recognise - a validation error as
    {"field": ["reason"]}, an empty list - was reported as sent; and a list of
    sentences raised AttributeError out of a function that promises never to
    raise. A message_id is now the only thing that counts as sent.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        logger.error("SMS %s: gateway replied with non-JSON: %s", description, body)
        return SmsResult(False, "The gateway's reply could not be read, so the "
                                f"message cannot be confirmed as sent: {body}")

    records = parsed if isinstance(parsed, list) else [parsed]
    queued = [r for r in records
              if isinstance(r, dict) and r.get("message_id") is not None]
    if not queued:
        words = _gateway_words(parsed) or "it returned no message record"
        logger.error("SMS %s refused by the gateway: %s", description, body)
        return SmsResult(False, _with_sender_hint(
            f"The gateway did not queue the message: {words}"))

    for record in queued:
        if str(record.get("status", "")).lower() in ("failed", "refunded"):
            logger.error("SMS %s refused by the gateway: %s", description, body)
            return SmsResult(False, "The gateway refused the message (status "
                                    f"{record.get('status')}).")

    first = queued[0]
    code = otp_code
    if otp_code is not None:
        # Semaphore is documented to use the code it is given. Should it ever
        # generate its own instead, the code on the handset is the one that
        # has to verify, so it is the one handed back.
        returned = first.get("code")
        if returned not in (None, "") and str(returned).lstrip("0") != otp_code.lstrip("0"):
            logger.warning("SMS %s: the gateway sent its own code rather than "
                           "the one supplied", description)
            code = str(returned)

    status = first.get("status") or "queued"
    logger.info("SMS %s accepted by the gateway as message %s (%s)",
                description, first["message_id"], status)
    return SmsResult(True, f"Accepted by the gateway for delivery to {number} "
                           f"(message {first['message_id']}, {status}).",
                     code=code)


def _gateway_words(parsed):
    """A refusal as a sentence. Semaphore reports validation failures as
    {"field": ["reason", ...]} and some refusals as a bare list of strings;
    printed raw, both read as noise on the settings page."""
    if isinstance(parsed, dict):
        parts = []
        for field, reasons in parsed.items():
            if isinstance(reasons, list):
                reasons = " ".join(str(r) for r in reasons)
            parts.append(f"{field}: {reasons}")
        return "; ".join(parts)
    if isinstance(parsed, list):
        return " ".join(str(p) for p in parsed if not isinstance(p, dict))
    return str(parsed)


def _with_sender_hint(sentence):
    if "sender" in sentence.lower():
        return (f"{sentence} A sender name has to be registered and approved "
                f"by the gateway before it can be used; SMS_SENDER_NAME is "
                f"'{settings.SMS_SENDER_NAME}'.")
    return sentence


def _read_semaphore_account(parsed):
    """What Semaphore's account endpoint says, as a verdict.

    A working key is not the same as a working account. An inactive account
    or a zero balance both authenticate perfectly and send nothing, so both
    fail the check rather than printing a balance for someone to misread.
    """
    if not isinstance(parsed, dict) or (parsed.get("account_id") is None
                                        and parsed.get("credit_balance") is None):
        return SmsResult(False, "The gateway refused the key: "
                                f"{_gateway_words(parsed) or 'no account came back'}")
    status = str(parsed.get("status") or "")
    balance = parsed.get("credit_balance")
    summary = f"Account {parsed.get('account_name') or parsed.get('account_id')}"
    if status:
        summary += f" ({status})"
    if balance is not None:
        summary += f", {balance} credits left"
    summary += "."

    if status and status.lower() != "active":
        return SmsResult(False, f"The key works, but the account is not active. "
                                f"{summary} Nothing sends until Semaphore "
                                f"reactivates it.")
    try:
        empty = balance is not None and float(balance) <= 0
    except (TypeError, ValueError):
        empty = False
    if empty:
        return SmsResult(False, f"The key works, but there is no credit. "
                                f"{summary} Nothing sends until the account is "
                                f"topped up.")
    return SmsResult(True, f"The key works. {summary}")


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

    Returns (body, None) or (None, SmsResult). Shared, because the gateways
    fail over the network in exactly the same ways and only disagree about
    what a successful body looks like. Semaphore had its own copy, whose
    unreachable-gateway message printed SMS_ENDPOINT - blank unless it is
    overridden, so it read "Could not reach the SMS gateway at  (...)".
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
        # Without the query string: Semaphore's account check carries the
        # API key there, and this sentence is shown on the settings page.
        where = request.full_url.split("?", 1)[0]
        return None, SmsResult(False, f"Could not reach the SMS gateway at "
                                      f"{where} ({exc.reason}).")
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


# Semaphore's route for one-time codes. Only used while SMS_ENDPOINT is unset:
# an override points sending somewhere else, and quietly sending codes to the
# real gateway past it is the surprise that setting exists to prevent.
SEMAPHORE_OTP_ENDPOINT = "https://api.semaphore.co/api/v4/otp"


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
        # A maintenance page or a proxy's error arrives as a 200 too. An
        # answer that cannot be read has confirmed nothing about the key.
        return SmsResult(False, "The gateway's answer could not be read, so "
                                f"the key is not confirmed: {body}")

    if isinstance(parsed, dict) and str(parsed.get("status", "")).lower() == "error":
        return SmsResult(False, "The gateway refused the key: "
                                f"{parsed.get('message') or body}")
    if provider == "semaphore":
        return _read_semaphore_account(parsed)

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
    try:
        parsed = json.loads(detail)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
        detail = parsed["message"]
    elif isinstance(parsed, (dict, list)):
        detail = _gateway_words(parsed) or detail

    if code in (401, 403):
        return (f"The gateway rejected the credentials (HTTP {code}). Check "
                f"SMS_API_KEY. It said: {detail}")
    # Semaphore answers a failed validation with 422, PhilSMS with 400.
    if code in (400, 422) and "sender" in detail.lower():
        return (f"The gateway rejected the sender name "
                f"'{settings.SMS_SENDER_NAME}'. It has to be registered with "
                f"them before it can be used. It said: {detail}")
    if code == 402:
        return f"The account is out of credit. It said: {detail}"
    if code == 429:
        # Semaphore's account endpoint - the Check button - allows one or two
        # calls a minute, so pressing it twice is enough to see this.
        return ("The gateway is limiting how often it is asked (HTTP 429). "
                "Wait a minute and try again. It said: " + detail)
    return f"The gateway answered HTTP {code}: {detail}"


PROVIDERS = {
    "console": _send_console,
    "semaphore": _send_semaphore,
    "philsms": _send_philsms,
    "textbee": _send_textbee,
}

# Gateways with a separate route for one-time codes. The rest get the code
# written into an ordinary message.
OTP_ROUTES = {
    "semaphore": _send_semaphore,
}


# --------------------------------------------------------------------------
# The interface everything else uses
# --------------------------------------------------------------------------

def fits_one_segment(text):
    """Whether `text` goes as a single SMS, counted the way a telco counts."""
    septets = 0
    for ch in text:
        if ch in _GSM7:
            septets += 1
        elif ch in _GSM7_ESCAPED:
            septets += 2
        else:
            return len(text.encode("utf-16-le")) // 2 <= UCS2_SEGMENT
    return septets <= SINGLE_SEGMENT


def _trim_to_one_segment(text):
    # "..." rather than "…", which is not GSM-7 and would itself turn the
    # message into three UCS-2 segments.
    while text and not fits_one_segment(text.rstrip() + "..."):
        text = text[:-1]
    return text.rstrip() + "..."


def send_sms(number, text, description="message", otp_code=None):
    """Send one message now, and say what happened. Never raises.

    `description` names which message this is, for the log. More than one kind
    goes through here, and a failure that does not say which leaves whoever is
    reading the log looking at the wrong feature.

    `otp_code` marks a message carrying a one-time code: `text` holds {otp}
    where the code goes. Where the gateway has a route for codes (Semaphore's)
    it goes that way; everywhere else the code is written in here. A code
    message is never trimmed - one cut off through the code is worse than none.
    The returned SmsResult's `code` is the code the recipient was sent.
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

    if otp_code is not None:
        if OTP_PLACEHOLDER not in text:
            return SmsResult(False, "A code message has nowhere to put the code.")
        written = text.replace(OTP_PLACEHOLDER, otp_code)
        if not fits_one_segment(written):
            logger.error("SMS %s not sent: longer than one segment", description)
            return SmsResult(False, "Refusing to trim a message that carries a "
                                    "code.")
        code_route = OTP_ROUTES.get(settings.SMS_PROVIDER)
        if code_route is not None and not settings.SMS_ENDPOINT:
            return code_route(number, text, description, otp_code=otp_code)
        result = PROVIDERS.get(settings.SMS_PROVIDER, _send_console)(
            number, written, description)
        result.code = otp_code if result.ok else None
        return result

    if not fits_one_segment(text):
        # Truncating beats splitting: two texts arriving out of order say
        # something the sender did not write.
        trimmed = _trim_to_one_segment(text)
        logger.warning("SMS %s was %d characters and has been trimmed to %d",
                       description, len(text), len(trimmed))
        text = trimmed

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
