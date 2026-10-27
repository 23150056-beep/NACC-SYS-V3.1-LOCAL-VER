"""The messages this system sends by text: three to staff and psychologists,
and, since 29 Sep 2026, appointment texts to a child's custodian (section 4).

Each one is a nudge to open the system, never the thing itself. That is the
whole design, and it comes from what a text message is: unencrypted, carried
by a telco, sitting on a lock screen that anyone beside the person can read.

So none of these carries a child's name, a case detail, or a password. The
email already refuses to carry a name for the same reason (see
children/notifications.py); a text is the weaker channel, so the rule is
applied harder rather than relaxed.

An unverified number gets nothing. A number somebody typed is a number that
might be a typo, and a typo is a stranger's handset.
"""
import logging
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from accounts import sms_catalogue
from accounts.models import PhoneVerification
from accounts.sms import SmsResult, limit_reason, queue_sms, send_sms
from config.clock import clock

logger = logging.getLogger(__name__)

# How long a verification code is good for, and how many tries it gets. Short
# enough that a code read off a screen months later is useless; long enough
# that a text arriving slowly is not.
CODE_TTL_SECONDS = 10 * 60
CODE_MAX_ATTEMPTS = 5

# How often one account may ask for a code. Every request is a paid message to
# a number the caller typed, so without a limit any signed-in account could
# text a stranger's handset on a loop and spend the agency's credit doing it.
CODE_RESEND_SECONDS = 60
CODE_MAX_PER_HOUR = 5


class TooManyCodes(Exception):
    """Raised with a sentence for the person waiting on a code."""


def _deliverable(user):
    """The number to text, or "" — a number nobody has proved they can receive
    at is not a destination."""
    number = getattr(user, "phone", "") or ""
    if not number:
        return ""
    if not getattr(user, "phone_verified", False):
        logger.info("Skipped SMS to %s: number on file is not verified", user.pk)
        return ""
    return number


# --------------------------------------------------------------------------
# 1. An administrator issued a temporary password
# --------------------------------------------------------------------------

def notify_temporary_password(user):
    """Tell them a password is waiting, not what it is.

    The password itself goes by email, where it is behind a login. Texting it
    would put a working credential on a lock screen, which is a downgrade from
    what this system already does, not a convenience.
    """
    number = _deliverable(user)
    if not number:
        return False
    return queue_sms(
        number,
        sms_catalogue.render("temporary_password"),
        "temporary_password",
        subject=f"user:{user.pk}",
    )


# --------------------------------------------------------------------------
# 2. A psychologist is asked to take a child
# --------------------------------------------------------------------------

def notify_new_assignment(child, psychologist=None):
    """Tell the psychologist a case is waiting for their answer - by default
    the one assigned. Not which child.

    The case reference is enough to find it after signing in, and it means
    the child's name never reaches a telco. It is written the way every
    screen writes it - C-0042, not 42 - so it can be typed straight into the
    search box.
    """
    from scheduling.visibility import case_ref

    psychologist = psychologist or getattr(child, "assigned_psychologist", None)
    if psychologist is None:
        return False
    number = _deliverable(psychologist)
    if not number:
        return False
    # A question since 28 Sep 2026: the case is theirs once they accept it.
    return queue_sms(
        number,
        sms_catalogue.render("assignment", case_ref=case_ref(child)),
        "assignment",
        subject=f"child:{child.pk}",
    )


# --------------------------------------------------------------------------
# 3. Tomorrow's sessions
# --------------------------------------------------------------------------

def notify_session_reminder(psychologist, count, when="tomorrow"):
    """One message for the whole day, not one per appointment.

    Deliberate on two counts: five separate texts about five sessions is the
    kind of thing that gets a sender muted, and a per-appointment message
    would have to say which child it was about to be worth reading.

    Sent now and the SmsResult returned, unlike the two above. This runs from
    a scheduled job rather than a save, so nothing is waiting on it - and the
    job needs the gateway's answer, because it records who was told. It used
    to be queued on a daemon thread, which dies with the process: a
    `manage.py send_session_reminders` run exits the moment it has queued, so
    it reported messages "queued" that were never sent, and recorded them as
    told so a second run would not try again.
    """
    number = _deliverable(psychologist)
    if not number:
        return SmsResult(False, "No verified mobile number on file.")
    if count < 1:
        return SmsResult(False, "No sessions to remind about.")
    sessions = "session" if count == 1 else "sessions"
    return send_sms(
        number,
        sms_catalogue.render("session_reminder", count=count,
                             sessions=sessions, when=when),
        "session_reminder",
        subject=f"user:{psychologist.pk}",
    )


# --------------------------------------------------------------------------
# 4. The child's custodian: their appointments (29 Sep 2026)
# --------------------------------------------------------------------------
#
# The only texts to somebody who is not a system user, so the rules at the top
# of this file apply hardest here. No child's name, no case detail, nothing
# clinical - a date, a time and a place. "The child in your care" is who it is
# about; the custodian knows. Only when children/custodian.texts_allowed()
# says so: consent recorded, number confirmed with a code, case open.
#
# The wording lives in accounts/sms_catalogue.py, one entry per text, so it can
# be changed in one place (the owner means to revise it). Every entry must stay
# ONE plain GSM-7 segment - test_sms_catalogue.py measures each at its longest -
# and must not begin with TEST, which Semaphore silently discards.

CUSTODIAN_OFFICE = sms_catalogue.CUSTODIAN_OFFICE
# The wording by event, as a view onto the catalogue ("reminder" is the
# day-before text; the others are the appointment's events).
CUSTODIAN_TEXTS = {
    event: sms_catalogue.template(f"custodian_{event}")
    for event in (*sms_catalogue.CUSTODIAN_EVENTS, "reminder")
}


def custodian_when(start):
    """"Tue 30 Sep, 9:30 AM", in local time. Built by hand: %-d and %-I are
    glibc extensions and raise on Windows, where the local copy runs."""
    local = timezone.localtime(start)
    return f"{local:%a} {local.day} {local:%b}, {clock(local)}"


def custodian_text(event, appointment, day="tomorrow"):
    kind = ("custodian_reminder" if event == "reminder"
            else sms_catalogue.custodian_event_kind(event))
    return sms_catalogue.render(
        kind, when=custodian_when(appointment.start), day=day,
        office=CUSTODIAN_OFFICE)


def _custodian_number(child):
    from children.custodian import texts_allowed
    return child.custodian_contact if texts_allowed(child) else ""


class TextOutcome(str):
    """What became of a custodian's text: the plain word, plus the server's
    own sentence where there is one (`detail`, set for "limit")."""
    detail = ""

    def __new__(cls, word, detail=""):
        obj = super().__new__(cls, word)
        obj.detail = detail
        return obj


def notify_custodian(appointment, event):
    """Booked, moved, cancelled or missed: queued for after the save commits.

    Returns what became of the text, for the screen to act on:
    "queued", "off" (texts to this custodian are not allowed, or no number),
    "unchanged" (a move to the start the custodian was last told) or "limit"
    (one of the limits - per appointment, per number or the agency's day - has
    been reached, so the SW is told to phone instead; `.detail` is the
    server's sentence saying which).

    "Moved" is sent only when the start differs from what the custodian was
    last told (`custodian_told_start`; none counts as different). After a
    booked or moved text is queued both "told" fields are set by a queryset
    update - bookkeeping, not an edit, so updated_at is left alone and no
    signal fires. The day-before reminder reads them.
    """
    number = _custodian_number(appointment.child)
    if not number:
        return "off"
    if event == "moved" and appointment.start == appointment.custodian_told_start:
        return "unchanged"
    subject = f"appointment:{appointment.pk}"
    # queue_sms answers nothing about the limits, and the request is over by
    # the time they bite; ask now so the screen can say so.
    held = limit_reason(number, sms_catalogue.custodian_event_kind(event),
                        subject=subject)
    if held:
        return TextOutcome("limit", held)
    queue_sms(number, custodian_text(event, appointment),
              sms_catalogue.custodian_event_kind(event), subject=subject)
    if event in ("booked", "moved"):
        told_at = timezone.now()
        type(appointment).objects.filter(pk=appointment.pk).update(
            custodian_told_start=appointment.start, custodian_told_at=told_at)
        appointment.custodian_told_start = appointment.start
        appointment.custodian_told_at = told_at
    return "queued"


def remind_custodian(appointment, day="tomorrow"):
    """The day-before reminder, sent now and the SmsResult returned - it runs
    from the reminder job, which records who was told (scheduling/reminders)."""
    return remind_custodian_group([appointment], day=day)


# Up to this many times are listed in a sibling reminder; past it the text
# gives the count and the first time (the catalogue's longest sample).
GROUP_TIMES_LISTED = 4


def _times_in_words(times):
    """"9:00 AM", "9:00 AM and 10:30 AM", "9:00 AM, 10:30 AM and 1:00 PM"."""
    if len(times) == 1:
        return times[0]
    return ", ".join(times[:-1]) + " and " + times[-1]


def remind_custodian_group(appointments, day="tomorrow"):
    """One day-before text for every appointment of one custodian's children
    on the same day, in time order: the single-child wording for one, the
    sibling wording for two to four, and a count and the first time beyond.

    Sent now and the SmsResult returned, like remind_custodian. The kinds are
    written out in three calls rather than computed, because the catalogue
    guard test reads them from the source.
    """
    appointments = sorted(appointments, key=lambda a: (a.start, a.pk))
    first = appointments[0]
    number = _custodian_number(first.child)
    if not number:
        return SmsResult(False, "Texts to this custodian are off.")
    subject = f"appointment:{first.pk}"
    local = timezone.localtime(first.start)
    date = f"{local:%a} {local.day} {local:%b}"
    if len(appointments) == 1:
        return send_sms(number, custodian_text("reminder", first, day=day),
                        "custodian_reminder", subject=subject)
    if len(appointments) <= GROUP_TIMES_LISTED:
        times = _times_in_words(
            [clock(timezone.localtime(a.start)) for a in appointments])
        return send_sms(
            number,
            sms_catalogue.render("custodian_reminder_group", day=day, date=date,
                                 times=times, office=CUSTODIAN_OFFICE),
            "custodian_reminder_group", subject=subject)
    return send_sms(
        number,
        sms_catalogue.render("custodian_reminder_group_many", count=len(appointments),
                             day=day, date=date, time=clock(local),
                             office=CUSTODIAN_OFFICE),
        "custodian_reminder_group_many", subject=subject)


# --------------------------------------------------------------------------
# Proving a number belongs to the person who typed it
# --------------------------------------------------------------------------

def start_phone_verification(user, number):
    """Text a code to `number` and remember it for a few minutes.

    Sent synchronously, unlike the notifications: the person is sitting in
    front of the screen waiting for it, and if the gateway refuses they need
    to be told now rather than left watching a handset.

    Sent as a one-time code (send_sms's otp_code), which on Semaphore means
    its code route - kept apart from bulk traffic, so a busy afternoon on the
    networks does not hold a ten-minute code in a queue.

    Raises TooManyCodes when this account has asked too recently or too
    often. A refused send does not count against either limit.
    """
    now = timezone.now()
    with transaction.atomic():
        PhoneVerification.objects.get_or_create(user=user)
        row = PhoneVerification.objects.select_for_update().get(user=user)
        before = {"last_sent_at": row.last_sent_at,
                  "window_started_at": row.window_started_at,
                  "sent_in_window": row.sent_in_window}
        if (row.window_started_at is None
                or now - row.window_started_at >= timedelta(hours=1)):
            row.window_started_at, row.sent_in_window = now, 0
        if row.sent_in_window >= CODE_MAX_PER_HOUR:
            raise TooManyCodes("Too many codes have been sent to this account "
                               "in the last hour. Try again later.")
        if (row.last_sent_at is not None
                and now - row.last_sent_at < timedelta(seconds=CODE_RESEND_SECONDS)):
            raise TooManyCodes("A code was sent less than a minute ago. Give "
                               "it a moment to arrive, or ask again in a "
                               "minute.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        row.number, row.code, row.tries = number, code, 0
        row.expires_at = now + timedelta(seconds=CODE_TTL_SECONDS)
        row.last_sent_at = now
        row.sent_in_window += 1
        row.save()

    # Sent after the claim commits, not inside it: SQLite locks the whole
    # database for a write, and a gateway can take twenty seconds to answer.
    result = send_sms(
        number,
        sms_catalogue.render("phone_code"),
        "phone_code",
        otp_code=code,
        requested_by=user,
        subject=f"user:{user.pk}",
    )
    mine = PhoneVerification.objects.filter(pk=row.pk, code=code)
    if result.refused:
        mine.update(code="", **before)
        return result
    if not result.ok:
        # "unknown": the request may have reached the gateway and the code may
        # be on the handset. Keep the code, the counters and last_sent_at, so
        # a retry is held by the one-minute limit instead of being a free
        # second paid text; the caller tells the person it may have gone.
        return result
    if result.code and result.code != code:
        # The gateway put a different code on the handset. That one is what
        # the person will type, so it is the one that has to be remembered.
        mine.update(code=result.code)
    return result


def confirm_phone_verification(user, submitted):
    """Check a code. Returns (ok, message).

    Counts attempts, because six digits is guessable given enough tries and
    this is the step that decides whether an account can be texted at all.
    """
    with transaction.atomic():
        row = (PhoneVerification.objects.select_for_update()
               .filter(user=user).first())
        if row is None or not row.code or row.expires_at <= timezone.now():
            return False, "That code has expired. Ask for a new one."

        row.tries += 1
        if row.tries > CODE_MAX_ATTEMPTS:
            row.code = ""
            row.save(update_fields=["code", "tries"])
            return False, "Too many wrong codes. Ask for a new one."

        if (submitted or "").strip() != row.code:
            row.save(update_fields=["tries"])
            left = CODE_MAX_ATTEMPTS - row.tries
            return False, (f"That code is not right. {left} attempt"
                           f"{'' if left == 1 else 's'} left.")

        row.code = ""
        row.save(update_fields=["code", "tries"])
        user.phone = row.number
        user.phone_verified = True
        user.save(update_fields=["phone", "phone_verified", "updated_at"])
    return True, "Number verified. You will now get text notifications."
