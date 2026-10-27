"""Every text message this system can send, declared in one place.

A new SMS feature is an entry here plus a `send_sms(kind=...)` call, and
nothing else: the sender (accounts/sms.py) reads the reply properly, tells a
refusal from "may have been sent", records the spend and holds the limits for
every kind alike, and `test_sms_catalogue.py` fails on a message nobody has
declared, one that is longer than a segment at its worst, or one with neither
limits nor a reason for having none.

Why a catalogue and not the wording next to each caller: on 27 Oct 2026 the
live check found that nine of eleven delivered texts were recorded as failed,
and the cause lived in the sender, not in any feature. Anything that must hold
for every text belongs where every text passes. What a message must DECLARE up
front - its route, who it goes to, how often it may go - belongs in one table
that a test can read, rather than in eleven call sites that each remember it.

The wording moved here unchanged from accounts/sms_notifications.py and
children/custodian.py. The rules in sms_notifications.py's header still hold:
no child's name, no case detail, no password, one plain GSM-7 segment, and
never an opening "TEST" (Semaphore silently discards it).

Nothing here imports the sender, so the sender can import this.
"""
from dataclasses import dataclass, field, replace

# Where a one-time code goes in a message that carries one. The sender fills
# it in (Semaphore's code route does it itself); `render` leaves it alone.
OTP_PLACEHOLDER = "{otp}"

# Routes. "otp" is Semaphore's code route: kept apart from bulk traffic and
# billed at two credits a message.
NORMAL = "normal"
OTP = "otp"

# Recipients. "staff" is a system user's own verified number, "custodian" the
# number a child's custodian confirmed, "caller" the number of whoever pressed
# the button (the Settings test text).
STAFF = "staff"
CUSTODIAN = "custodian"
CALLER = "caller"

# What a rule counts by.
BY_NUMBER = "number"
BY_NUMBER_SUBJECT = "number+subject"
BY_REQUESTER = "requester"

# Spelt out in one place so the code-sending paths and the test agree.
CUSTODIAN_OFFICE = "the RACCO I office"


@dataclass(frozen=True)
class Rule:
    """At most `count` texts of this kind in any `seconds`, counted by `by`.

    `group` names the pool the count is taken from. It defaults to the kind, so
    a rule on its own counts only its own kind; two kinds that share a group
    share the allowance - the two code kinds, which are the same abuse (a
    loop of paid codes) whichever screen asked.
    """
    count: int
    seconds: int
    by: str = BY_NUMBER
    group: str = ""


@dataclass(frozen=True)
class Entry:
    key: str
    template: str
    route: str
    recipient: str
    # Values that make the rendered text its longest, so the length test
    # measures the worst case rather than a pleasant one.
    samples: dict = field(default_factory=dict)
    rules: tuple = ()
    no_rule_reason: str = ""

    def __post_init__(self):
        # A rule with no group of its own counts this kind alone.
        object.__setattr__(self, "rules", tuple(
            r if r.group else replace(r, group=self.key) for r in self.rules))


_REMINDED_ONCE = "claimed once per recipient and day by scheduling/reminders.py"

# The day-before wording for a custodian with one child coming in, and the two
# for a custodian with several (siblings come on the same day).
_GROUP_LEAD = "NACC RACCO I reminder: the children in your care have "

_CODE_RULES = [
    Rule(2, 3600, BY_NUMBER, "code"),
    Rule(4, 86400, BY_NUMBER, "code"),
    Rule(10, 86400, BY_REQUESTER, "code"),
]
_EVENT_RULES = [Rule(4, 86400, BY_NUMBER_SUBJECT, "custodian_event")]

_WHEN = "Wed 30 Sep, 12:30 PM"

ENTRIES = {e.key: e for e in [
    Entry(
        "temporary_password",
        "NACC SYS: an administrator has issued you a temporary password. "
        "Check your email to sign in. Do not share it.",
        NORMAL, STAFF,
        rules=[Rule(1, 600, BY_NUMBER)]),
    Entry(
        "assignment",
        "NACC SYS: a case is waiting for you to accept or decline (case "
        "{case_ref}). Sign in to answer.",
        NORMAL, STAFF,
        samples={"case_ref": "C-99999"},
        rules=[Rule(1, 3600, BY_NUMBER_SUBJECT)]),
    Entry(
        "session_reminder",
        "NACC SYS: you have {count} {sessions} scheduled {when}. "
        "Sign in to see the schedule.",
        NORMAL, STAFF,
        samples={"count": 99, "sessions": "sessions", "when": "tomorrow"},
        no_rule_reason=_REMINDED_ONCE),
    Entry(
        "custodian_booked",
        "NACC RACCO I: An appointment is set for the child in your care on "
        "{when} at {office}. Please arrive 10 minutes early.",
        NORMAL, CUSTODIAN,
        samples={"when": _WHEN, "office": CUSTODIAN_OFFICE},
        rules=_EVENT_RULES),
    Entry(
        "custodian_moved",
        "NACC RACCO I: The appointment of the child in your care is moved to "
        "{when} at {office}.",
        NORMAL, CUSTODIAN,
        samples={"when": _WHEN, "office": CUSTODIAN_OFFICE},
        rules=_EVENT_RULES),
    Entry(
        "custodian_cancelled",
        "NACC RACCO I: The appointment of the child in your care on {when} "
        "is cancelled. The office will contact you to rebook.",
        NORMAL, CUSTODIAN,
        samples={"when": _WHEN, "office": CUSTODIAN_OFFICE},
        rules=_EVENT_RULES),
    Entry(
        "custodian_missed",
        "NACC RACCO I: We missed you at today's appointment. Please contact "
        "the office to rebook.",
        NORMAL, CUSTODIAN,
        samples={"when": _WHEN, "office": CUSTODIAN_OFFICE},
        rules=_EVENT_RULES),
    Entry(
        "custodian_reminder",
        "NACC RACCO I reminder: the child in your care has an appointment "
        "{day}, {when} at {office}.",
        NORMAL, CUSTODIAN,
        samples={"day": "tomorrow", "when": _WHEN, "office": CUSTODIAN_OFFICE},
        no_rule_reason=_REMINDED_ONCE),
    # One reminder for a custodian whose children come on the same day. The
    # times are listed up to four ("9:00 AM, 10:30 AM, 1:00 PM and 2:30 PM" is
    # the longest that still fits one segment with the wording around it);
    # more than four fall back to the count and the first time, below.
    Entry(
        "custodian_reminder_group",
        _GROUP_LEAD + "appointments {day}, {date}, {times} at {office}.",
        NORMAL, CUSTODIAN,
        samples={"day": "tomorrow", "date": "Wed 30 Sep",
                 "times": "10:30 AM, 12:30 PM, 12:30 PM and 12:30 PM",
                 "office": CUSTODIAN_OFFICE},
        no_rule_reason=_REMINDED_ONCE),
    Entry(
        "custodian_reminder_group_many",
        _GROUP_LEAD + "{count} appointments {day}, {date}, the first at "
        "{time} at {office}.",
        NORMAL, CUSTODIAN,
        samples={"count": 99, "day": "tomorrow", "date": "Wed 30 Sep",
                 "time": "12:30 PM", "office": CUSTODIAN_OFFICE},
        no_rule_reason=_REMINDED_ONCE),
    Entry(
        "phone_code",
        # Measured on the owner's handset on 28 Oct 2026: the old wording
        # ("NACC SYS: ... It expires in 10 minutes.") was accepted and marked
        # Sent by Semaphore three times and never arrived - the network drops
        # that exact combination. "NACC SYS: your verification code is
        # 111111." and this wording both arrived. Do not put "NACC SYS" back
        # next to "expires in".
        "NACC RACCO I: your verification code is {otp}. It expires in 10 minutes.",
        OTP, STAFF,
        samples={"otp": "482913"},
        rules=_CODE_RULES),
    Entry(
        "custodian_code",
        "NACC RACCO I: your code is {otp}. Give it to the social worker "
        "to confirm this number for appointment reminders.",
        OTP, STAFF,
        samples={"otp": "482913"},
        rules=_CODE_RULES),
    Entry(
        "config_test",
        "NACC SYS: test message {reference}. If you can read this, text "
        "notifications are working.",
        NORMAL, CALLER,
        samples={"reference": "667120"},
        rules=[Rule(1, 60, BY_NUMBER), Rule(5, 3600, BY_NUMBER)]),
]}

# The events a custodian is told about when an appointment changes. The kind
# sent for one is built by custodian_event_kind() below, which is the one
# place a kind is allowed to be computed rather than written out.
CUSTODIAN_EVENTS = ("booked", "moved", "cancelled", "missed")


def custodian_event_kind(event):
    """The catalogue key for a custodian appointment event ("booked", ...)."""
    if event not in CUSTODIAN_EVENTS:
        raise KeyError(f"No custodian text for the event {event!r}")
    return f"custodian_{event}"


def get(kind):
    """The entry for `kind`, or None."""
    return ENTRIES.get(kind)


def template(kind):
    return ENTRIES[kind].template


def render(kind, **values):
    """The text for `kind`. A code message keeps {otp} in place: the sender
    writes the code in (or Semaphore's code route does)."""
    entry = ENTRIES[kind]
    if entry.route == OTP:
        values.setdefault("otp", OTP_PLACEHOLDER)
    return entry.template.format(**values)


def render_sample(kind):
    """The text at its worst case, for the length test and the live check."""
    return render(kind, **ENTRIES[kind].samples)


def kinds_in_group(group):
    """Every kind with a rule in `group`: the pool a rule counts across."""
    return [e.key for e in ENTRIES.values()
            if any(r.group == group for r in e.rules)]
