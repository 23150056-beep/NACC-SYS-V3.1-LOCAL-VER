"""Whether a booking can exist at all.

The capacity check used to be the whole of it, and capacity is a count per
day: it cannot tell that two of a psychologist's three places are at the same
o'clock. So a calendar could show - and did show - a psychologist seeing two
children at 09:00, a session running past the end of the working day, and a
child in two rooms at once.

The rules live here rather than in the viewset because there are three ways in
(create, update, and whatever comes next) and the update path already proved
the point: `perform_update` was never written, so moving an appointment went
straight to the model with nothing checked at all. A rule enforced on one verb
is not enforced.

Two tiers, and the difference matters:

* The **availability window and its capacity** are a preference. A
  psychologist working late on their own calendar is their business, so those
  are waived when they book themselves - as they always were.
* **Overlaps are not a preference.** Nobody is in two places at once whatever
  their role, so those checks apply to everybody.
"""
from datetime import datetime, timedelta

from django.utils import timezone

from scheduling.models import Appointment

# How far either side of the new appointment to look for clashes. Comfortably
# wider than any appointment anybody books, and it keeps the comparison in
# Python: end times are start + duration, which is not a column to filter on.
CLASH_MARGIN = timedelta(hours=12)


def ends_at(start, duration_minutes):
    return start + timedelta(minutes=duration_minutes or 60)


def overlaps(a_start, a_end, b_start, b_end):
    """Half-open intervals, so 09:00-10:00 and 10:00-11:00 do NOT overlap.

    Back-to-back appointments are how a full clinic day is booked; a rule that
    called them a clash would be unusable by lunchtime.
    """
    return a_start < b_end and b_start < a_end


def _clashes(qs, start, end, exclude_id):
    """The first appointment in `qs` whose time collides with [start, end)."""
    qs = qs.exclude(status=Appointment.CANCELLED).filter(
        start__gte=start - CLASH_MARGIN, start__lte=end + CLASH_MARGIN)
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    for other in qs:
        if overlaps(start, end, other.start, ends_at(other.start, other.duration_minutes)):
            return other
    return None


def window_for(psychologist, local_start, local_end):
    """The active availability block that fully contains [start, end).

    Fully, not merely the start: duration_minutes was accepted and then never
    looked at, which let a 60-minute session start at 11:30 in a window that
    closed at 12:00.
    """
    for block in psychologist.availability_blocks.filter(active=True):
        if not block.covers(local_start):
            continue
        if local_end.date() == local_start.date() and local_end.time() <= block.end_time:
            return block
    return None


def capacity_left(psychologist, block, local_start, exclude_id=None):
    day_start = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
    taken = (Appointment.objects
             .filter(psychologist=psychologist,
                     start__gte=day_start, start__lt=day_start + timedelta(days=1))
             .exclude(status=Appointment.CANCELLED)
             .filter(start__time__gte=block.start_time, start__time__lt=block.end_time))
    if exclude_id is not None:
        taken = taken.exclude(pk=exclude_id)
    return block.capacity - taken.count()


def errors_for(psychologist, child, start, duration_minutes,
               *, own_calendar=False, exclude_id=None):
    """Every reason to refuse this booking, as {field: message}, or {} to allow.

    `own_calendar` waives the availability window and its capacity - never the
    overlap rules. `exclude_id` is the appointment being edited, so moving one
    is not read as a clash with itself.
    """
    local_start = timezone.localtime(start) if timezone.is_aware(start) else start
    end = ends_at(start, duration_minutes)
    local_end = ends_at(local_start, duration_minutes)

    if not own_calendar:
        block = window_for(psychologist, local_start, local_end)
        if block is None:
            return {"start": "That time is outside the psychologist's availability "
                             "window, or the session would run past the end of it."}
        if capacity_left(psychologist, block, local_start, exclude_id) <= 0:
            return {"start": "That availability block is fully booked."}

    clash = _clashes(Appointment.objects.filter(psychologist=psychologist),
                     start, end, exclude_id)
    if clash is not None:
        return {"start": f"{clash.psychologist.fullname or 'This psychologist'} is "
                         f"already booked with {clash.child.fullname} from "
                         f"{timezone.localtime(clash.start).strftime('%H:%M')}."}

    if child is not None:
        clash = _clashes(Appointment.objects.filter(child=child), start, end, exclude_id)
        if clash is not None:
            return {"child": f"{clash.child.fullname} already has an appointment at "
                             f"{timezone.localtime(clash.start).strftime('%H:%M')} "
                             f"that day. A child cannot be in two places at once."}

    return {}


# How finely the day is sliced when offering start times. Thirty minutes is
# what an office actually books on; a finer grid produces a wall of buttons
# nobody reads, and a coarser one hides real openings.
STEP_MINUTES = 30


def blocks_on(psychologist, day):
    """The psychologist's active availability windows for one date."""
    return [b for b in psychologist.availability_blocks.filter(active=True)
            if (b.date == day if b.date is not None
                else b.weekday is not None and b.weekday == day.weekday())]


def bookable_slots(psychologist, child, day, duration_minutes=60,
                   step_minutes=STEP_MINUTES, exclude_id=None):
    """Start times on `day` that this booking could actually take.

    Every candidate is put through the same errors_for() the booking endpoint
    uses, rather than through a cheaper approximation of it. That is the whole
    contract of this function: what it offers can be booked. The moment the
    offer is computed one way and the refusal another, the screen starts
    lying, and a booking screen that lies is worse than a blank time field
    because it looks authoritative.

    `child` may be None - before one is chosen the form still wants to show
    the shape of the day. `exclude_id` is the appointment being MOVED: without
    it, the time that appointment currently holds is read as a clash with
    itself and disappears from the grid offering to move it.
    """
    now = timezone.localtime()
    slots = []
    for block in blocks_on(psychologist, day):
        cursor = timezone.make_aware(datetime.combine(day, block.start_time))
        window_end = timezone.make_aware(datetime.combine(day, block.end_time))
        step = timedelta(minutes=step_minutes)
        while ends_at(cursor, duration_minutes) <= window_end:
            if cursor > now and not errors_for(psychologist, child, cursor,
                                               duration_minutes,
                                               exclude_id=exclude_id):
                slots.append({
                    "start": cursor.strftime("%H:%M"),
                    "end": ends_at(cursor, duration_minutes).strftime("%H:%M"),
                })
            cursor += step
    slots.sort(key=lambda s: s["start"])
    return slots


def _spoken(day):
    """"Wednesday 9 Sep" - %-d is a glibc extension and raises on Windows."""
    return f"{day:%A} {day.day} {day:%b}"


def why_empty(psychologist, day, duration_minutes=60):
    """Why there is nothing to offer - an empty list is not an explanation.

    "They do not work Wednesdays", "the day is full" and "a 3-hour session
    does not fit" send somebody to three different next actions, and a screen
    that renders all three as an empty panel just looks broken.
    """
    name = getattr(psychologist, "fullname", "") or "This psychologist"
    blocks = blocks_on(psychologist, day)
    if not blocks:
        return f"{name} has no availability on {_spoken(day)}."
    longest = max(
        (datetime.combine(day, b.end_time) - datetime.combine(day, b.start_time))
        for b in blocks)
    if timedelta(minutes=duration_minutes) > longest:
        return (f"A {duration_minutes}-minute session does not fit any of "
                f"{name}'s windows that day - the longest is "
                f"{int(longest.total_seconds() // 60)} minutes.")
    if day < timezone.localdate():
        return "That day has already passed."
    if day == timezone.localdate():
        return f"{name}'s windows for today have already started."
    return f"{name} is fully booked on {_spoken(day)}."
