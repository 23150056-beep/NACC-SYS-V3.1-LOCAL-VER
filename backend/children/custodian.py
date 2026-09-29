"""The child's custodian: who they live with now, and whether the system may
text them (owner's decision, 29 Sep 2026).

A custodian is not a system user. They never agreed to anything by signing
up, so the system texts them only when BOTH are recorded on the child:

- **consent** - the social worker asked, in person or by phone, and the
  custodian said yes. Recorded with who ticked it and when. It belongs to the
  person, not the child: a different custodian, or a different number, clears
  it, so nobody inherits somebody else's yes.
- **a confirmed number** - a one-time code texted to the number and read back
  by the custodian while the social worker is with them. A typed number may be
  a typo, and a typo is a stranger's handset.

`texts_allowed()` is the one test every custodian text goes through
(accounts/sms_notifications.py). The ISA and social workers record all of this;
a psychologist cannot change it.
"""
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from accounts.phone import InvalidPhilippineMobile, normalise_ph_mobile
from children import intake

# Same limits as a user confirming their own number (accounts/sms_notifications):
# every code is a paid text to a number somebody typed.
CODE_TTL_SECONDS = 10 * 60
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_SECONDS = 60
CODE_MAX_PER_HOUR = 5
# How long a confirmed number may be put on a record by whoever confirmed it -
# the rest of the intake, a sibling's record with the same custodian.
CONFIRMED_FOR = timedelta(hours=1)

CODE_TEXT = ("NACC RACCO I: your code is {otp}. Give it to the social worker "
             "to confirm this number for appointment reminders.")

FIELDS = ("custodian_name", "custodian_contact", "custodian_sms_consent")


class TooManyCodes(Exception):
    """Raised with a sentence for the social worker waiting on a code."""


def texts_allowed(child):
    """May the system text this child's custodian? Consent, a confirmed
    number, and a case still open."""
    return bool(child.custodian_contact and child.custodian_sms_consent
                and child.custodian_contact_verified_at
                and child.status == child.ACTIVE)


def status_of(child):
    """A short line for screens: why texts are on or off."""
    if not child.custodian_contact:
        return "No contact number - texts off"
    if not child.custodian_sms_consent:
        return "No consent recorded - texts off"
    if not child.custodian_contact_verified_at:
        return "Number not confirmed - texts off"
    return "Texts on"


# --- Recording it on the child ---------------------------------------------

def normalise(raw):
    """The number as stored, or a ValueError with a sentence for the screen."""
    try:
        return normalise_ph_mobile(raw)
    except InvalidPhilippineMobile as exc:
        raise ValueError(str(exc)) from exc


def apply(attrs, instance, user, role):
    """Work out the custodian fields a save will write. Returns {field: error}
    for anything refused, and otherwise updates `attrs` in place.

    - A psychologist cannot change any of it - except that moving the case to
      a type that asks for no custodian takes the custodian with it, whoever
      moves it. Refusing that left a psychologist's case-type change unsavable,
      with the refusal on a field the new case type does not show.
    - A new custodian or a new number clears consent unless consent is given
      again in the same save, and clears the confirmation unless the new
      number was confirmed by this person within CONFIRMED_FOR.
    - Consent needs a number to be about.
    """
    before = {f: getattr(instance, f, None) if instance is not None else None
              for f in FIELDS}
    before["custodian_name"] = before["custodian_name"] or ""
    before["custodian_contact"] = before["custodian_contact"] or ""
    before["custodian_sms_consent"] = bool(before["custodian_sms_consent"])

    if role not in (Role.ADMINISTRATOR, Role.STAFF):
        changed = [f for f in FIELDS if f in attrs and attrs[f] != before[f]]
        if changed and _leaves_custodian_behind(attrs, instance) and not any(
                attrs.get(f) for f in FIELDS):
            attrs.update(custodian_name="", custodian_contact="", custodian_sms_consent=False,
                         custodian_sms_consent_at=None, custodian_sms_consent_by=None,
                         custodian_contact_verified_at=None)
            return {}
        if changed:
            return {changed[0]: "Only the social worker or the ISA records the custodian."}
        for f in FIELDS:
            attrs.pop(f, None)
        return {}

    after = {f: attrs.get(f, before[f]) for f in FIELDS}
    who_changed = (after["custodian_name"].strip() != before["custodian_name"].strip()
                   or after["custodian_contact"] != before["custodian_contact"])
    now = timezone.now()

    consent = attrs.get("custodian_sms_consent")
    if consent is None:                      # not in this save
        consent = False if who_changed else before["custodian_sms_consent"]
    if consent and not after["custodian_contact"]:
        return {"custodian_sms_consent": "Enter the custodian's contact number first."}
    attrs["custodian_sms_consent"] = bool(consent)
    if not consent:
        attrs["custodian_sms_consent_at"] = None
        attrs["custodian_sms_consent_by"] = None
    elif who_changed or not before["custodian_sms_consent"]:
        attrs["custodian_sms_consent_at"] = now
        attrs["custodian_sms_consent_by"] = user

    number = after["custodian_contact"]
    if not number:
        attrs["custodian_contact_verified_at"] = None
    elif number != before["custodian_contact"] or (
            instance is not None and not instance.custodian_contact_verified_at):
        attrs["custodian_contact_verified_at"] = confirmed_at(user, number)
    return {}


def _leaves_custodian_behind(attrs, instance):
    """This save moves the case to a type that asks for no custodian."""
    if instance is None or "case_type" not in attrs or attrs["case_type"] == instance.case_type:
        return False
    return "custodian_name" not in intake.CASE_TYPE_FIELDS.get(attrs["case_type"], [])


def confirmed_at(user, number):
    """When `user` confirmed `number` with a code, if recently enough to put
    it on a record now; else None."""
    from children.models import CustodianContactCheck
    row = (CustodianContactCheck.objects
           .filter(requested_by=user, number=number, verified_at__isnull=False)
           .first())
    if row is None or timezone.now() - row.verified_at > CONFIRMED_FOR:
        return None
    return row.verified_at


# --- The one-time code -------------------------------------------------------

def send_code(user, number):
    """Text a code to `number`. Returns the gateway's SmsResult; raises
    TooManyCodes when this person has asked too recently or too often. A
    refused send does not count against either limit."""
    from accounts.sms import send_sms
    from children.models import CustodianContactCheck

    now = timezone.now()
    with transaction.atomic():
        CustodianContactCheck.objects.get_or_create(requested_by=user)
        row = CustodianContactCheck.objects.select_for_update().get(requested_by=user)
        before = {"last_sent_at": row.last_sent_at,
                  "window_started_at": row.window_started_at,
                  "sent_in_window": row.sent_in_window}
        if (row.window_started_at is None
                or now - row.window_started_at >= timedelta(hours=1)):
            row.window_started_at, row.sent_in_window = now, 0
        if row.sent_in_window >= CODE_MAX_PER_HOUR:
            raise TooManyCodes("Too many codes have been sent from your account in "
                               "the last hour. Try again later.")
        if (row.last_sent_at is not None
                and now - row.last_sent_at < timedelta(seconds=CODE_RESEND_SECONDS)):
            raise TooManyCodes("A code was sent less than a minute ago. Give it a "
                               "moment to arrive, or ask again in a minute.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        row.number, row.code, row.tries, row.verified_at = number, code, 0, None
        row.expires_at = now + timedelta(seconds=CODE_TTL_SECONDS)
        row.last_sent_at = now
        row.sent_in_window += 1
        row.save()

    # After the claim commits: a gateway can take twenty seconds, and SQLite
    # locks the whole database for a write.
    result = send_sms(number, CODE_TEXT, "custodian number check", otp_code=code)
    mine = CustodianContactCheck.objects.filter(pk=row.pk, code=code)
    if not result.ok:
        mine.update(code="", **before)
        return result
    if result.code and result.code != code:
        mine.update(code=result.code)
    return result


def confirm_code(user, number, submitted):
    """Check the code read back for `number`. Returns (ok, message)."""
    from children.models import CustodianContactCheck

    with transaction.atomic():
        row = (CustodianContactCheck.objects.select_for_update()
               .filter(requested_by=user).first())
        if (row is None or not row.code or row.number != number
                or row.expires_at is None or row.expires_at <= timezone.now()):
            return False, "That code has expired or was for another number. Send a new one."
        row.tries += 1
        if row.tries > CODE_MAX_ATTEMPTS:
            row.code = ""
            row.save(update_fields=["code", "tries"])
            return False, "Too many wrong codes. Send a new one."
        if (submitted or "").strip() != row.code:
            row.save(update_fields=["tries"])
            left = CODE_MAX_ATTEMPTS - row.tries
            return False, (f"That code is not right. {left} attempt"
                           f"{'' if left == 1 else 's'} left.")
        row.code = ""
        row.verified_at = timezone.now()
        row.save(update_fields=["code", "tries", "verified_at"])
    return True, "Number confirmed. Save the record to keep it."
