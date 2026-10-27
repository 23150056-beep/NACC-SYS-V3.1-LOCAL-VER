"""Sending a text message, without the rest of the system knowing who sends it.

Everything above this module calls `send_sms(number, text, kind)` and gets
back an SmsResult. Which gateway carries it is a setting, and changing
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
import hashlib
import hmac
import http.client
import json
import logging
import math
import re
import socket
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request

from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from accounts import sms_catalogue
from accounts.phone import normalise_ph_mobile, InvalidPhilippineMobile

logger = logging.getLogger(__name__)

_TIMEOUT = 20

# How much of a gateway's reply is read, and how much of it is shown. The whole
# reply is read and parsed: Semaphore echoes the message, the account email and
# the sender name back, so a real reply to a one-line text is 400-460
# characters, and cutting it at 400 turned a paid, delivered text into
# "unreadable, so failed" (27 Oct 2026: nine of eleven). Only a LOG LINE or a
# sentence on screen is shortened.
MAX_REPLY_BYTES = 65536
SHOWN_REPLY_CHARS = 400

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
OTP_PLACEHOLDER = sms_catalogue.OTP_PLACEHOLDER


SENT = "sent"
REFUSED = "refused"
UNKNOWN = "unknown"
LIMITED = "limited"


class SmsResult:
    """Whether it went, and what to say if it did not.

    A bare False was not enough: the settings-page test button has to be able
    to print the gateway's own words. A silent failure is exactly how the mail
    integration cost a day.

    `outcome` is the part that matters to a caller deciding what to do next,
    because "it did not go" hides two very different things:

    - "sent": the gateway queued it. `ok` is True for this and only this.
    - "refused": definitely nothing went out - no key, a bad number, the
      gateway said no, the connection never opened. Safe to hand a limit back,
      delete a reminder's claim, or try again.
    - "limited": this system's own daily limits said no before the network was
      touched. Nothing went out either.
    - "unknown": the request may have reached the gateway and the answer was
      lost or unreadable - a timeout, a dropped connection, a reply that is not
      JSON. The text MAY have been delivered and billed, so it is kept and
      never automatically sent again.

    `code` is set only for a message sent with a one-time code, and is the
    code the recipient was actually sent - see _read_semaphore_reply.
    `message_id` is the gateway's own reference, where it gave one.
    """

    def __init__(self, ok, detail="", code=None, outcome=None, message_id=None):
        if outcome is None:
            outcome = SENT if ok else REFUSED
        self.outcome = outcome
        self.ok = outcome == SENT
        self.detail = detail
        self.code = code
        self.message_id = message_id

    @property
    def refused(self):
        """Definitely nothing went out: safe to give limits back or retry."""
        return self.outcome in (REFUSED, LIMITED)

    def __bool__(self):
        return self.ok

    def __repr__(self):
        return (f"SmsResult(ok={self.ok}, outcome={self.outcome!r}, "
                f"detail={self.detail!r})")


def _short(text, limit=SHOWN_REPLY_CHARS):
    """A reply shortened for a log line or a sentence on screen."""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "..."


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

def _send_console(number, text, kind):
    """The default, and what runs locally and in tests.

    Writes the message to the log instead of a gateway. Not a stub to be
    replaced later — it is what should happen when no gateway is configured,
    because the alternative is either a crash or a silent no-op, and both of
    those are worse than a line in the log saying exactly what would have been
    sent.
    """
    logger.info("SMS (console) to %s — %s: %s", number, kind, text)
    return SmsResult(True, f"Written to the log. No gateway is configured, so "
                           f"nothing was sent to {number}.")


# Semaphore discards any message whose text begins with the word TEST: it is
# accepted, never sent, never charged, and nothing in the reply says so. No
# message here starts that way today; this is so a reworded one cannot
# vanish quietly.
_SEMAPHORE_DISCARDS = re.compile(r"^\s*test\b", re.IGNORECASE)


def _send_semaphore(number, text, kind, otp_code=None):
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
        logger.error("SMS %s not sent: it begins with TEST", kind)
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
    body, failure = _post(request, kind)
    if failure is not None:
        return failure
    return _read_semaphore_reply(body, number, kind, otp_code)


def _read_semaphore_reply(body, number, kind, otp_code=None):
    """Semaphore's answer to a send, read strictly.

    This used to call every JSON reply without a "Failed" status a success, so
    a refusal in a shape it did not recognise - a validation error as
    {"field": ["reason"]}, an empty list - was reported as sent; and a list of
    sentences raised AttributeError out of a function that promises never to
    raise. A message_id is now the only thing that counts as sent.

    A reply that is not JSON is "unknown", not "refused": it arrived with a
    2xx, so the request was taken, and a maintenance page or a proxy's error
    in that shape says nothing about whether the text went.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        logger.error("SMS %s: gateway replied with non-JSON: %s", kind, _short(body))
        return SmsResult(False, "The gateway's reply could not be read, so the "
                                "message may have been sent and cannot be "
                                f"confirmed: {_short(body)}", outcome=UNKNOWN)

    records = parsed if isinstance(parsed, list) else [parsed]
    queued = [r for r in records
              if isinstance(r, dict) and r.get("message_id") is not None]
    if not queued:
        words = _gateway_words(parsed) or "it returned no message record"
        logger.error("SMS %s refused by the gateway: %s", kind, _short(body))
        return SmsResult(False, _with_sender_hint(
            f"The gateway did not queue the message: {_short(words)}"),
            outcome=REFUSED)

    for record in queued:
        if str(record.get("status", "")).lower() in ("failed", "refunded"):
            logger.error("SMS %s refused by the gateway: %s", kind, _short(body))
            return SmsResult(False, "The gateway refused the message (status "
                                    f"{record.get('status')}).", outcome=REFUSED)

    first = queued[0]
    code = otp_code
    if otp_code is not None:
        # Semaphore is documented to use the code it is given. Should it ever
        # generate its own instead, the code on the handset is the one that
        # has to verify, so it is the one handed back.
        returned = first.get("code")
        if returned not in (None, "") and str(returned).lstrip("0") != otp_code.lstrip("0"):
            logger.warning("SMS %s: the gateway sent its own code rather than "
                           "the one supplied", kind)
            code = str(returned)

    status = first.get("status") or "queued"
    logger.info("SMS %s accepted by the gateway as message %s (%s)",
                kind, first["message_id"], status)
    return SmsResult(True, f"Accepted by the gateway for delivery to {number} "
                           f"(message {first['message_id']}, {status}).",
                     code=code, message_id=str(first["message_id"]))


def _gateway_words(parsed):
    """A refusal as a sentence. Semaphore reports validation failures as
    {"field": ["reason", ...]}, some refusals as a bare list of strings, and
    some as a list of those objects - [{"senderName": "No active sender name
    found..."}], seen on 1 Oct 2026 under HTTP 500. Printed raw, every one of
    them reads as noise on the settings page."""
    if isinstance(parsed, dict):
        parts = []
        for field, reasons in parsed.items():
            if isinstance(reasons, list):
                reasons = " ".join(str(r) for r in reasons)
            parts.append(f"{field}: {reasons}")
        return "; ".join(parts)
    if isinstance(parsed, list):
        return " ".join(_gateway_words(p) if isinstance(p, (dict, list)) else str(p)
                        for p in parsed).strip()
    return str(parsed)


def _sender_hint():
    """What to do about a refused sender name, given what was sent.

    Semaphore's "No active sender name found" reads as though the account
    has no approved name, and an owner whose name IS approved is then told
    something they know to be false. What it usually means is that the name
    sent is not one of the account's ACTIVE names - none was sent, it is
    spelt differently, it is still pending, or the key belongs to another
    account - so the hint says which name went, and where the list is.
    """
    name = settings.SMS_SENDER_NAME
    # Only Semaphore's check reads the account's sender names.
    where = ("Settings > Text messages > Check the key lists the sender names "
             "on the account and their status."
             if settings.SMS_PROVIDER == "semaphore" else "")
    if not name:
        return (" SMS_SENDER_NAME is not set, so no sender name was sent. Set "
                "it to your approved sender name, spelt exactly as the "
                "gateway shows it, and restart the API. " + where)
    return (f" The name sent was '{name}' (SMS_SENDER_NAME). It must match an "
            f"Active sender name on the same account the API key belongs "
            f"to, spelt exactly. " + where)


def _with_sender_hint(sentence):
    if "sender" in sentence.lower():
        return sentence + _sender_hint()
    return sentence


def _read_semaphore_account(parsed):
    """What Semaphore's account endpoint says, as a verdict.

    A working key is not the same as a working account. An inactive account
    or a zero balance both authenticate perfectly and send nothing, so both
    fail the check rather than printing a balance for someone to misread.
    """
    # It has been seen as a one-item list as well as a bare object.
    if isinstance(parsed, list) and len(parsed) == 1:
        parsed = parsed[0]
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


def _send_philsms(number, text, kind):
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
    body, failure = _post(request, kind)
    if failure is not None:
        return failure

    try:
        parsed = json.loads(body)
    except ValueError:
        logger.warning("SMS %s: gateway replied with non-JSON: %s", kind, _short(body))
        return SmsResult(True, f"Gateway accepted it, and replied: {_short(body)}")

    if str(parsed.get("status", "")).lower() == "error":
        message = parsed.get("message") or body
        logger.error("SMS %s refused by the gateway: %s", kind, _short(body))
        return SmsResult(False, f"The gateway refused the message: {_short(message)}",
                         outcome=REFUSED)
    logger.info("SMS %s accepted by the gateway", kind)
    return SmsResult(True, f"Accepted by the gateway for delivery to {number}.")


def _send_textbee(number, text, kind):
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
    body, failure = _post(request, kind)
    if failure is not None:
        return failure

    try:
        parsed = json.loads(body)
    except ValueError:
        return SmsResult(True, f"Gateway accepted it, and replied: {_short(body)}")

    # It answers 2xx for an accepted relay. A body that explicitly says
    # otherwise is still treated as a refusal, because the alternative is
    # reporting an unsent message as sent - which is the failure this whole
    # module is arranged around.
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if isinstance(data, dict) and data.get("success") is False:
        return SmsResult(False, f"The gateway refused the message: {_short(body)}",
                         outcome=REFUSED)
    if isinstance(parsed, dict) and parsed.get("success") is False:
        return SmsResult(False, f"The gateway refused the message: {_short(body)}",
                         outcome=REFUSED)
    logger.info("SMS %s handed to the phone", kind)
    return SmsResult(True, f"Handed to your linked phone for delivery to {number}.")


def _read_body(response):
    return response.read(MAX_REPLY_BYTES).decode("utf-8", "replace")


def _refusal_in(detail):
    """Whether an error body is a gateway's readable "I queued nothing".

    Semaphore answers a refused sender name with HTTP 500 and a JSON body that
    says so; that is a refusal, however the status was spelt. A 5xx with
    anything else - an HTML error page, an empty body - tells us nothing about
    whether the request got as far as being queued.
    """
    try:
        parsed = json.loads(detail)
    except ValueError:
        return False
    records = parsed if isinstance(parsed, list) else [parsed]
    return isinstance(parsed, (dict, list)) and not any(
        isinstance(r, dict) and r.get("message_id") is not None for r in records)


def _failure_before_sending(reason):
    """True for a connection failure that happened before any request was
    written: refused, or no such host. A timeout or a reset is the opposite:
    the request may have gone."""
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror,
                               ssl.SSLCertVerificationError))


def _post(request, kind):
    """Send it, and turn a transport failure into an SmsResult.

    Returns (body, None) or (None, SmsResult). Shared, because the gateways
    fail over the network in exactly the same ways and only disagree about
    what a successful body looks like. Semaphore had its own copy, whose
    unreachable-gateway message printed SMS_ENDPOINT - blank unless it is
    overridden, so it read "Could not reach the SMS gateway at  (...)".

    Each failure says whether the text can have gone. A refusal (4xx, a
    gateway that answered "nothing queued", a connection that never opened) is
    safe to retry; a timeout, a dropped connection or a 5xx with no readable
    refusal is "unknown" - the gateway may have queued it and lost the answer.
    """
    # Without the query string: Semaphore's account check carries the API key
    # there, and these sentences are shown on the settings page.
    where = request.full_url.split("?", 1)[0]
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return _read_body(response), None
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(MAX_REPLY_BYTES).decode("utf-8", "replace")
        except Exception:                                        # noqa: BLE001
            detail = "(no response body)"
        logger.error("SMS %s failed: HTTP %s - %s", kind, exc.code, _short(detail))
        refused = exc.code < 500 or _refusal_in(detail)
        sentence = _explain(exc.code, detail)
        if not refused:
            sentence += " The message may have been sent."
        return None, SmsResult(False, sentence,
                               outcome=REFUSED if refused else UNKNOWN)
    except urllib.error.URLError as exc:
        reason = exc.reason
        if _failure_before_sending(reason):
            logger.error("SMS %s failed: could not reach the gateway (%s)", kind, reason)
            return None, SmsResult(False, f"Could not reach the SMS gateway at "
                                          f"{where} ({reason}).", outcome=REFUSED)
        logger.error("SMS %s: no usable answer from the gateway (%s)", kind, reason)
        return None, SmsResult(False, f"No answer from the SMS gateway at {where} "
                                      f"({reason}). The message may have been "
                                      f"sent.", outcome=UNKNOWN)
    except (TimeoutError, socket.timeout, ConnectionError,
            http.client.HTTPException) as exc:
        # Raised bare from reading the reply: the request had been sent.
        logger.error("SMS %s: the connection failed after the request (%s)",
                     kind, exc.__class__.__name__)
        return None, SmsResult(False, "The gateway did not finish answering "
                                      f"({exc.__class__.__name__}). The message "
                                      "may have been sent.", outcome=UNKNOWN)
    except Exception:                                            # noqa: BLE001
        logger.exception("Unexpected error sending SMS %s", kind)
        return None, SmsResult(False, "Unexpected error sending the message. "
                                      "The server log has the traceback. It "
                                      "may have been sent.", outcome=UNKNOWN)


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


# Semaphore's list of the account's sender names and their status. Asked by the
# key check, never by the sender: it is rate-limited to a call or two a minute.
SEMAPHORE_SENDERNAMES_ENDPOINT = "https://api.semaphore.co/api/v4/account/sendernames"


def _check_semaphore_sender_names(api_key):
    """(verdict, sentence) about the sender names on the key's account.

    The verdict is None when the list could not be had - a rate limit, an
    answer that cannot be read - because failing the whole check on a second
    call would hide a perfectly good key.
    """
    url = (f"{SEMAPHORE_SENDERNAMES_ENDPOINT}?"
           f"{urllib.parse.urlencode({'apikey': api_key})}")
    body, failure = _post(urllib.request.Request(
        url, headers={"accept": "application/json"}, method="GET"),
        "sender name check")
    if failure is not None:
        return None, f"Sender names could not be checked: {failure.detail}"
    try:
        parsed = json.loads(body)
    except ValueError:
        return None, "Sender names could not be checked: the answer could not be read."
    names = parsed.get("data") if isinstance(parsed, dict) else parsed
    if not isinstance(names, list):
        return None, ("Sender names could not be checked: "
                      f"{_short(_gateway_words(parsed) or body)}")

    entries = [(str(n.get("name") or ""), str(n.get("status") or ""))
               for n in names if isinstance(n, dict) and n.get("name")]
    active = [name for name, status in entries if status.lower() == "active"]
    listing = ", ".join(f"{name} ({status or 'no status'})"
                        for name, status in entries) or "none"
    wanted = settings.SMS_SENDER_NAME

    if not active:
        return False, (f"No sender name on this account is active (sender "
                       f"names: {listing}), so every message will be refused "
                       f"until one is approved.")
    if not wanted:
        return False, (f"SMS_SENDER_NAME is not set. Set it to an active "
                       f"sender name - {', '.join(active)} - and restart the "
                       f"API; without one, messages are refused.")
    if wanted in active:
        return True, f"Sender name '{wanted}' is active."
    for name, status in entries:
        if name == wanted:
            return False, (f"Sender name '{wanted}' is on the account but its "
                           f"status is {status or 'unknown'}, not Active.")
    for name in active:
        if name.lower() == wanted.lower():
            return False, (f"SMS_SENDER_NAME is '{wanted}' but the account has "
                           f"'{name}'. It must match exactly, capitals included.")
    return False, (f"SMS_SENDER_NAME is '{wanted}', which is not a sender name "
                   f"on this account. Active: {', '.join(active)}. Is the API "
                   f"key from the account that holds the name?")


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
                                f"the key is not confirmed: {_short(body)}")

    if isinstance(parsed, dict) and str(parsed.get("status", "")).lower() == "error":
        return SmsResult(False, "The gateway refused the key: "
                                f"{_short(parsed.get('message') or body)}")
    if provider == "semaphore":
        account = _read_semaphore_account(parsed)
        if not account.ok:
            return account
        senders_ok, senders = _check_semaphore_sender_names(api_key)
        # None means the list could not be fetched: say so, and let the
        # account's own verdict stand rather than failing on a second call.
        return SmsResult(senders_ok is not False, f"{account.detail} {senders}")

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
        summary = _short(body)
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
    # The whole body was read to find the gateway's words; only a sentence of
    # it is shown.
    detail = _short(detail)

    if code in (401, 403):
        return (f"The gateway rejected the credentials (HTTP {code}). Check "
                f"SMS_API_KEY. It said: {detail}")
    # Any status, not only a validation one: Semaphore answered "No active
    # sender name found" with a 500.
    if "sender" in detail.lower():
        return (f"The gateway refused the sender name (HTTP {code}). It said: "
                f"{detail}." + _sender_hint())
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


def segments(text):
    """How many messages the gateway bills for `text`, counted the way a telco
    counts: 160 septets in one GSM-7 message and 153 in each of several, 70
    UCS-2 units in one and 67 in each of several."""
    septets = 0
    for ch in text:
        if ch in _GSM7:
            septets += 1
        elif ch in _GSM7_ESCAPED:
            septets += 2
        else:
            units = len(text.encode("utf-16-le")) // 2
            return 1 if units <= UCS2_SEGMENT else math.ceil(units / 67)
    return 1 if septets <= SINGLE_SEGMENT else math.ceil(septets / 153)


def number_fingerprint(number):
    """(hash, last four digits) of a normalised number, for the spend record.

    Keyed with the secret key so the table cannot be turned back into phone
    numbers by trying every Philippine mobile number against a plain hash -
    there are only about a hundred million of them.
    """
    digest = hmac.new(settings.SECRET_KEY.encode("utf-8"),
                      number.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest, number[-4:]


# Hours a limit looks back. The per-number and agency limits are daily; a
# kind's own rules carry their own window.
_DAY = 86400


def _sent_since(seconds):
    """Rows that spent, or may have spent, credit in the last `seconds`. A
    refused or limited text went nowhere, so it never counts against a limit."""
    from accounts.models import SmsSend
    return SmsSend.objects.filter(
        created_at__gte=timezone.now() - timedelta(seconds=seconds),
        outcome__in=(SENT, UNKNOWN))


def _limit_reached(entry, fingerprint, credits, requested_by, subject):
    """A sentence if this text would break a limit, else "".

    Every text passes through here, so the limits hold for a feature added
    after they were written. Counted from the SmsSend table - shared by every
    worker and kept across restarts - rather than from the cache, which is
    per process.
    """
    from django.db.models import Sum

    number_hash = fingerprint[0]
    day = _sent_since(_DAY)

    if day.filter(number_hash=number_hash).count() >= settings.SMS_PER_NUMBER_DAILY:
        return ("This number has already been sent as many texts as are allowed "
                "in a day. Try again tomorrow.")

    spent = day.aggregate(total=Sum("credits"))["total"] or 0
    if spent + credits > settings.SMS_DAILY_CREDIT_LIMIT:
        return ("The agency's daily text message limit has been reached, so no "
                "more texts are sent until it clears. Try again later.")

    for rule in entry.rules:
        pool = _sent_since(rule.seconds).filter(
            kind__in=sms_catalogue.kinds_in_group(rule.group))
        if rule.by == sms_catalogue.BY_REQUESTER:
            if requested_by is None:
                continue
            pool = pool.filter(requested_by=requested_by)
        else:
            pool = pool.filter(number_hash=number_hash)
            if rule.by == sms_catalogue.BY_NUMBER_SUBJECT:
                pool = pool.filter(subject=subject)
        if pool.count() >= rule.count:
            return _rule_sentence(rule)
    return ""


def limit_reason(number, kind, *, requested_by=None, subject=""):
    """The sentence send_sms would refuse this text with, or "" if it would go.

    queue_sms is fire and forget - the answer arrives on a thread after the
    request has been served - so this is the only way a screen can be told
    that a text will not be sent. It runs the SAME check send_sms does
    (_limit_reached, with the same credit estimate), so the two cannot
    disagree. Every catalogue message fits one segment, so a normal-route text
    is estimated at one credit and a code at two; the console bills nothing.
    A number that cannot be read, or an unknown kind, is not a limit: send_sms
    refuses those on its own terms.
    """
    entry = sms_catalogue.get(kind)
    if entry is None:
        return ""
    try:
        number = normalise_ph_mobile(number)
    except InvalidPhilippineMobile:
        return ""
    if not number:
        return ""
    if PROVIDERS.get(settings.SMS_PROVIDER, _send_console) is _send_console:
        credits = 0
    else:
        credits = 2 if entry.route == sms_catalogue.OTP else 1
    requested_by = requested_by if getattr(requested_by, "pk", None) else None
    try:
        return _limit_reached(entry, number_fingerprint(number), credits,
                              requested_by, subject)
    except Exception:                                            # noqa: BLE001
        # As in send_sms: unreadable limits do not stop a text.
        logger.exception("SMS %s: the limits could not be checked", kind)
        return ""


def _rule_sentence(rule):
    window = ("a minute" if rule.seconds == 60 else
              "an hour" if rule.seconds == 3600 else
              "a day" if rule.seconds == _DAY else
              f"{rule.seconds // 60} minutes")
    return (f"This kind of text has already been sent as often as is allowed "
            f"({rule.count} in {window}). Try again later.")


def _record(kind, route, credits, result, fingerprint, requested_by, subject):
    """Write the spend record. Never raises: a text that went must not be
    reported as failed because the book-keeping could not be written."""
    try:
        from accounts.models import SmsSend
        SmsSend.objects.create(
            kind=kind, route=route,
            credits=credits if result.outcome in (SENT, UNKNOWN) else 0,
            outcome=result.outcome, message_id=(result.message_id or "")[:40],
            requested_by=requested_by, subject=subject[:40],
            number_hash=fingerprint[0], number_tail=fingerprint[1])
    except Exception:                                            # noqa: BLE001
        logger.exception("SMS %s: the send could not be recorded", kind)


def send_sms(number, text, kind, otp_code=None, *, requested_by=None,
             subject="", enforce_limits=True):
    """Send one message now, and say what happened. Never raises.

    `kind` is a key of accounts/sms_catalogue, which says which message this is
    and what limits it carries; an unknown one is refused, so a message nobody
    declared cannot be sent. It is also what the log and the spend record call
    the message. `requested_by` (the user who caused it) and `subject` (what it
    is about, as a reference like "child:42", never a name) go into the spend
    record, and the catalogue's per-requester and per-subject limits read them.

    `otp_code` marks a message carrying a one-time code: `text` holds {otp}
    where the code goes. Where the gateway has a route for codes (Semaphore's)
    it goes that way; everywhere else the code is written in here. A code
    message is never trimmed - one cut off through the code is worse than none.
    The returned SmsResult's `code` is the code the recipient was sent.

    Every call that gets past argument checking leaves one SmsSend row, and is
    held to the per-number, agency and per-kind limits BEFORE the network is
    touched. `enforce_limits=False` is for the live-check command alone.
    """
    entry = sms_catalogue.get(kind)
    if entry is None:
        logger.error("SMS not sent: %r is not in accounts/sms_catalogue", kind)
        return SmsResult(False, f"{kind!r} is not a declared kind of text "
                                "message, so nothing was sent.")
    try:
        number = normalise_ph_mobile(number)
    except InvalidPhilippineMobile as exc:
        logger.warning("SMS %s not sent: %s", kind, exc)
        return SmsResult(False, str(exc))
    if not number:
        return SmsResult(False, "No mobile number on file for this account.")

    text = (text or "").strip()
    if not text:
        return SmsResult(False, "Refusing to send an empty message.")

    written = None
    if otp_code is not None:
        if OTP_PLACEHOLDER not in text:
            return SmsResult(False, "A code message has nowhere to put the code.")
        written = text.replace(OTP_PLACEHOLDER, otp_code)
        if not fits_one_segment(written):
            logger.error("SMS %s not sent: longer than one segment", kind)
            return SmsResult(False, "Refusing to trim a message that carries a "
                                    "code.")
    elif not fits_one_segment(text):
        # Truncating beats splitting: two texts arriving out of order say
        # something the sender did not write.
        trimmed = _trim_to_one_segment(text)
        logger.warning("SMS %s was %d characters and has been trimmed to %d",
                       kind, len(text), len(trimmed))
        text = trimmed

    requested_by = requested_by if getattr(requested_by, "pk", None) else None
    provider_name = settings.SMS_PROVIDER
    provider = PROVIDERS.get(provider_name, _send_console)
    code_route = OTP_ROUTES.get(provider_name)
    by_code_route = (otp_code is not None and code_route is not None
                     and not settings.SMS_ENDPOINT)
    route = sms_catalogue.OTP if by_code_route else sms_catalogue.NORMAL
    # An estimate of what the gateway will bill. The console one bills nothing.
    if provider is _send_console:
        credits = 0
    elif by_code_route:
        credits = 2
    else:
        credits = segments(written if written is not None else text)

    fingerprint = number_fingerprint(number)

    if enforce_limits:
        try:
            reason = _limit_reached(entry, fingerprint, credits, requested_by,
                                    subject)
        except Exception:                                        # noqa: BLE001
            # The limits could not be read. Failing closed would stop every
            # text, a password notice included, over a book-keeping fault.
            logger.exception("SMS %s: the limits could not be checked", kind)
            reason = ""
        if reason:
            logger.warning("SMS %s not sent: %s", kind, reason)
            result = SmsResult(False, reason, outcome=LIMITED)
            _record(kind, route, 0, result, fingerprint, requested_by, subject)
            return result

    if by_code_route:
        result = code_route(number, text, kind, otp_code=otp_code)
    elif written is not None:
        result = provider(number, written, kind)
        result.code = otp_code if result.ok else None
    else:
        result = provider(number, text, kind)
    _record(kind, route, credits, result, fingerprint, requested_by, subject)
    return result


def queue_sms(number, text, kind, *, requested_by=None, subject=""):
    """Send after the current transaction commits, on a daemon thread.

    Returns whether it was queued, not whether it arrived — the caller is
    inside a request and the gateway is not its problem. Mirrors how the mail
    is queued, deliberately: two notification paths that behave differently
    under load is one more thing to hold in your head.
    """
    if not number:
        return False

    def _send():
        try:
            send_sms(number, text, kind, requested_by=requested_by,
                     subject=subject)
        finally:
            # A thread has its own database connection (the limits and the
            # spend record use it) and nothing else would ever close it.
            connection.close()

    def _fire():
        threading.Thread(target=_send, daemon=True).start()

    transaction.on_commit(_fire)
    return True
