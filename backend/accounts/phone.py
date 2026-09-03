"""Turning what somebody typed into a number a gateway will accept.

One implementation, because the alternative is the shape this codebase has
already been cleaned of: the same rule written slightly differently in the
serializer, the SMS sender and whatever comes next, drifting until two of them
disagree about the same person's number.

Philippine mobile numbers only. A landline is a legitimate contact detail and
an illegitimate SMS destination, so it is refused here rather than accepted and
silently dropped by the gateway — a message that never arrives is the failure
mode this whole feature has to avoid.

Accepted, because these are what people actually type:

    0917 123 4567      09171234567      +63 917 123 4567
    639171234567       9171234567       (0917) 123-4567

All of them store as +639171234567.
"""
import re

# Mobile prefixes are 9XX across every Philippine network — Globe, Smart,
# DITO, and the sub-brands (TM, TNT, Sun, GOMO) all sit inside 9. Checking the
# leading 9 rather than a list of three-digit prefixes is deliberate: that list
# changes when a carrier is allocated a new block, and a stale copy of it here
# would refuse a real number.
_MOBILE = re.compile(r"^9\d{9}$")

E164_LENGTH = 13          # +63 plus ten digits
NATIONAL_LENGTH = 10      # 9XXXXXXXXX


class InvalidPhilippineMobile(ValueError):
    """Raised with a message written for the person who typed it."""


def normalise_ph_mobile(raw):
    """Return +639XXXXXXXXX, or raise InvalidPhilippineMobile.

    An empty value is not an error — the field is optional — and comes back as
    an empty string so callers can store it without a special case.
    """
    text = (raw or "").strip()
    if not text:
        return ""

    # Everything people use to make a number readable.
    digits = re.sub(r"[\s\-().]", "", text)

    if digits.startswith("+"):
        digits = digits[1:]
        if not digits.startswith("63"):
            raise InvalidPhilippineMobile(
                "Only Philippine mobile numbers can receive messages from "
                "this system. Enter a number starting 09 or +63.")
        national = digits[2:]
    elif digits.startswith("63"):
        national = digits[2:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits

    if not national.isdigit():
        raise InvalidPhilippineMobile(
            "That contains characters a phone number cannot have. Enter "
            "digits only, for example 0917 123 4567.")

    if not _MOBILE.match(national):
        # The two ways this goes wrong are worth telling apart: a landline is
        # a plausible thing to type here and a different mistake from a
        # mistyped mobile.
        if national.startswith(("2", "3", "4", "5", "6", "7", "8")):
            raise InvalidPhilippineMobile(
                "That looks like a landline. Text messages need a mobile "
                "number, which starts 09.")
        raise InvalidPhilippineMobile(
            "That is not a complete Philippine mobile number. It should be "
            "eleven digits starting 09, for example 0917 123 4567.")

    return "+63" + national


def as_typed(e164):
    """Render a stored number the way a Filipino reader expects it.

    0917 123 4567 rather than +639171234567 — the stored form is for the
    gateway, this is for the screen.
    """
    if not e164 or not e164.startswith("+63") or len(e164) != E164_LENGTH:
        return e164 or ""
    n = e164[3:]
    return f"0{n[0:3]} {n[3:6]} {n[6:]}"
