"""The emails about a session being booked, moved or cancelled (owner's request,
21 Oct 2026; design: docs/superpowers/specs/2026-10-21-email-notifications-
design.md, "Sessions and the calendar invite"). Called by AppointmentViewSet
after the save, and by nothing else: the seeders and management commands write
appointments through the ORM and must never mail anyone.

Everything goes through `accounts.email_notifications.notify`, so that module's
rules hold here too: no child's name, only to an active account in the role who
has not switched it off, after the commit, never raising, and nothing but a log
line when no mail service is set up. This module decides WHO hears of a change
and WHAT they are sent, and counts the calendar invites.

* The psychologist whose calendar it is gets `my_calendar`, with an invite
  attached, when someone ELSE changed it. When they changed it themselves they
  get nothing for a booking, and for a move or a cancellation only the short
  "Calendar update", and only if an invite was ever sent for that session:
  otherwise nothing in their Google Calendar needs correcting, and the one
  email anybody gets about their own action would be noise. It says so.
* The social worker whose record the child is gets `child_sessions`, the same
  words with no invite: the invite is for the person who has to be in the room.
* A session that moves to a different psychologist is a cancellation for the
  one it left and a booking for the one it came to. The one it left is told
  it was given on and not called off, and the social worker's email names
  both, because the time is the same and nothing else would say what moved.
* Only a session still to come is mailed about when it is edited. One never
  marked completed stays "scheduled" in the past and can still be edited.
* People are named by what they typed (`accounts.display.entered_name`), never
  by their username, which is their email address.
"""
import copy
import logging

from django.db import transaction
from django.utils import timezone

from accounts.display import entered_name
from accounts.email_notifications import notify, when
from config.clock import clock
from scheduling.booking import ends_at
from scheduling.calendar_invite import build_ics
from scheduling.models import CalendarInvite
from scheduling.visibility import case_ref

logger = logging.getLogger(__name__)

BOOKED, MOVED, CANCELLED = "booked", "moved", "cancelled"
SCHEDULE = ("Open the schedule", "/schedule")

# Sent with an email that carries an invite, by what the invite does.
ATTACHED = {
    BOOKED: "A calendar invite is attached.",
    MOVED: "An updated calendar invite is attached.",
    CANCELLED: "A calendar update is attached.",
}


def _span(start, duration_minutes):
    """"Wed 21 Oct 2026, 9:30 AM to 10:30 AM", local time. A session that runs
    past midnight - possible only on a psychologist's own calendar - names the
    day it ends as well."""
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    end = timezone.localtime(ends_at(start, duration_minutes))
    local = timezone.localtime(start)
    tail = clock(end) if end.date() == local.date() else when(end)
    return f"{when(start)} to {tail}"


def _facts(appointment, was):
    """The Case, the When and, on a move, the Was. Never the purpose or the
    notes: Brevo and Google both keep a copy."""
    facts = [("Case", case_ref(appointment.child_id)),
             ("When", _span(appointment.start, appointment.duration_minutes))]
    if was is not None:
        facts.append(("Was", _span(*was)))
    return facts


def _invite(appointment, attendee, method):
    """The callable `notify` calls once the email is certain to be queued:
    counts the invite as sent, and builds it with the number it was sent as.

    Inside the callable and not before, so a copy with no mail service set up
    (the local one) records nothing, and neither does an opted-out psychologist
    - their SEQUENCE must not run ahead of the invites they actually hold, or
    nothing would ever be wrong and nothing would ever be fixed. The first
    invite is SEQUENCE 1.

    The row is locked from the read to the end of the build, so two changes to
    one session handled at the same moment are numbered one after the other and
    each mail carries the number written for it. Counted with an UPDATE and
    read back afterwards, both could read the later number: the same SEQUENCE
    twice, which a calendar settles by timestamp, and one never sent. A file
    that fails to build rolls the count back too."""
    def attach():
        with transaction.atomic():
            row, _ = CalendarInvite.objects.select_for_update().get_or_create(
                appointment=appointment, attendee=attendee)
            row.sequence += 1
            row.cancelled = method == "CANCEL"
            row.save()
            return "invite.ics", build_ics(
                appointment, attendee, method=method, sequence=row.sequence)
    return attach


def _tell_psychologist(appointment, psychologist, event, *, by, was, reassigned_to=None):
    """`my_calendar`, with the invite. See the module note for who gets what.
    `reassigned_to` is the psychologist a session went to when it left this
    one: a cancellation alone reads as though the child's session is off."""
    ref = case_ref(appointment.child_id)
    method = "CANCEL" if event == CANCELLED else "REQUEST"
    if psychologist.pk == by.pk:
        # Their own change. A booking leaves nothing in their calendar to
        # correct; a move or a cancellation does, but only if we put it there.
        if event == BOOKED or not CalendarInvite.objects.filter(
                appointment=appointment, attendee=psychologist, cancelled=False).exists():
            return
        subject = f"Calendar update: session {ref}"
        paragraphs = [
            f"You {event} the session for case {ref}. This email only carries the "
            "updated calendar invite, so your calendar can follow your change."]
    else:
        who = entered_name(by, "A colleague")
        subject = f"{who} {event} a session on your calendar"
        paragraphs = [f"{who} {event} a session on your calendar for case {ref}.",
                      ATTACHED[event]]
        if reassigned_to is not None:
            paragraphs.insert(1, "The session itself goes ahead; it was given to "
                                 f"{entered_name(reassigned_to, 'another psychologist')}.")
    notify("my_calendar", psychologist, subject=subject, heading=subject,
           paragraphs=paragraphs, facts=_facts(appointment, was), button=SCHEDULE,
           attachment=_invite(appointment, psychologist, method))


def _tell_social_worker(appointment, event, *, by, was, was_psychologist=None):
    """`child_sessions`: the same news without an invite. Only to the worker
    whose record the child is, and `notify` sees to it that they are Staff.

    When the session changed hands, with no new time, there is no "Was" to show
    and the email would say "moved" over two identical rows: the Psychologist
    row says what did move."""
    worker = appointment.child.social_worker
    if worker is None or worker.pk == by.pk:
        return
    ref, who = case_ref(appointment.child_id), entered_name(by, "A colleague")
    subject = f"{who} {event} a session for case {ref}"
    facts = _facts(appointment, was)
    if was_psychologist is not None:
        facts.append(("Psychologist",
                      f"{entered_name(appointment.psychologist, 'A psychologist')} "
                      f"(was {entered_name(was_psychologist, 'a colleague')})"))
    notify("child_sessions", worker, subject=subject, heading=subject,
           paragraphs=[f"{who} {event} a session for case {ref}, which is in "
                       "your records."],
           facts=facts, button=SCHEDULE)


def _as_it_was(appointment, was_start, was_duration):
    """A copy of `appointment` at the time it had before it was changed, for the
    psychologist it was taken from: their calendar holds the old time, and the
    cancellation should name it. Never saved."""
    earlier = copy.copy(appointment)
    earlier.start = was_start or appointment.start
    earlier.duration_minutes = was_duration or appointment.duration_minutes
    return earlier


def _safely(what, send, *args, **kwargs):
    """One person's email failing to prepare must not stop the next person's,
    and none of it may fail the booking that is in the middle of being saved."""
    try:
        send(*args, **kwargs)
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not prepare the session email (%s)", what)


def session_changed(appointment, event, *, by, was_start=None, was_duration=None,
                    was_psychologist=None):
    """`by` booked, moved or cancelled `appointment` (event is "booked",
    "moved" or "cancelled"). Returns nothing and never raises.

    For a move, `was_start`, `was_duration` and `was_psychologist` are what the
    appointment was before the save; the email says "Was" only when the time
    really did change. A `was_psychologist` other than the appointment's own
    means it changed hands."""
    try:
        if event not in (BOOKED, MOVED, CANCELLED):
            raise ValueError(f"A session is booked, moved or cancelled, not {event!r}.")
        psychologist = appointment.psychologist
        was = None
        if event == MOVED and was_start is not None:
            earlier = (was_start, was_duration or appointment.duration_minutes)
            if earlier != (appointment.start, appointment.duration_minutes):
                was = earlier

        handed_over = was_psychologist is not None and was_psychologist.pk != psychologist.pk
        if handed_over:
            _safely("previous psychologist", _tell_psychologist,
                    _as_it_was(appointment, was_start, was_duration), was_psychologist,
                    CANCELLED, by=by, was=None, reassigned_to=psychologist)
            _safely("new psychologist", _tell_psychologist,
                    appointment, psychologist, BOOKED, by=by, was=None)
        else:
            _safely("psychologist", _tell_psychologist,
                    appointment, psychologist, event, by=by, was=was)
        _safely("social worker", _tell_social_worker, appointment, event, by=by, was=was,
                was_psychologist=was_psychologist if handed_over else None)
    except Exception:                                            # noqa: BLE001
        logger.exception("Could not prepare the session emails")
