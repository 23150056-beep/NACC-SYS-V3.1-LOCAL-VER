"""The child's custodian: who they live with now, and whether the system may
text them (owner's decision, 29 Sep 2026; changed 2 Nov 2026).

A custodian is not a system user. They never agreed to anything by signing
up, so the system texts them only when BOTH are recorded on the child:

- **a contact number**, and
- **consent** - the custodian agreed in person to receive appointment texts at
  that number (the date, time and place only). Recorded with who ticked it and
  when. It belongs to the person and the number, so a different custodian or a
  different number clears it: nobody inherits somebody else's yes.

Since 2 Nov 2026 the number is no longer confirmed with a one-time code. The
custodian agrees in person, the social worker ticks the box, and saving a
fresh agreement texts that number once to say so
(accounts/sms_notifications.notify_custodian_consent), which is how a typo is
noticed: the wrong handset gets a text that says "Not you?".

`texts_allowed()` is the one test every custodian text goes through
(accounts/sms_notifications.py). The ISA and social workers record all of this;
a psychologist cannot change it.
"""
from django.utils import timezone

from accounts.models import Role
from accounts.phone import InvalidPhilippineMobile, normalise_ph_mobile
from children import intake

FIELDS = ("custodian_name", "custodian_contact", "custodian_sms_consent")


def texts_allowed(child):
    """May the system text this child's custodian? A number, the custodian's
    consent, and a case still open."""
    return bool(child.custodian_contact and child.custodian_sms_consent
                and child.status == child.ACTIVE)


# What status_of() says when texts are on, and the ending of every sentence for
# when they are off. Named so a screen that needs to tell the two apart (the
# case brief's next steps) compares against these and not against typed copies.
TEXTS_ON = "Texts on"
TEXTS_OFF_ENDING = " - texts off"


def status_of(child):
    """A short line for screens: why texts are on or off."""
    if not child.custodian_contact:
        return "No contact number" + TEXTS_OFF_ENDING
    if not child.custodian_sms_consent:
        return "No consent recorded" + TEXTS_OFF_ENDING
    return TEXTS_ON


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
      again in the same save.
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
                         custodian_sms_consent_at=None, custodian_sms_consent_by=None)
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

    return {}


def _leaves_custodian_behind(attrs, instance):
    """This save moves the case to a type that asks for no custodian."""
    if instance is None or "case_type" not in attrs or attrs["case_type"] == instance.case_type:
        return False
    return "custodian_name" not in intake.CASE_TYPE_FIELDS.get(attrs["case_type"], [])


# --- A fresh agreement ------------------------------------------------------

def agreement_snapshot(child):
    """What a save is compared against to tell whether it recorded a NEW yes:
    (name, number, consent) as the record held them. A record that does not
    exist yet holds nothing."""
    if child is None:
        return ("", "", False)
    return ((child.custodian_name or "").strip(), child.custodian_contact or "",
            bool(child.custodian_sms_consent))


def is_fresh_agreement(before, child):
    """Did the save that just happened record a custodian's yes for THIS person
    at THIS number? True when consent is on now and it was off before, or the
    number changed, or the custodian changed (consent given again in the same
    save, or `apply` would have cleared it). An unrelated edit of an already
    consented record, a withdrawal and a closed case are not."""
    name, number, consent = before
    return bool(texts_allowed(child) and (
        not consent
        or (child.custodian_contact or "") != number
        or (child.custodian_name or "").strip() != name))
