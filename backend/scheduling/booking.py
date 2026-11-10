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

There is ONE rule and two ways to feed it. `Diary` reads what the rule needs
(the psychologist's windows, their leave, their sessions and the child's, and
whether the referral exists) for a range of days, and `Diary.errors_for` is the
rule itself, working on that data alone. `errors_for()` below reads one day;
the slot grid reads a day, and the month calendar reads a month, and each puts
every candidate time through the same evaluator. Asking the database per
candidate was fine for a day and would be thousands of queries for a month, and
a cheaper look-alike for the month would let the screen offer what the booking
endpoint then refuses.
"""
import calendar
from collections import namedtuple
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from accounts.display import display_name
from clinical.models import CaseReferral
from config.clock import clock
from scheduling.availability import blocks_for_day
from scheduling.models import Appointment, Unavailability

# How far either side of the new appointment to look for clashes. Comfortably
# wider than any appointment anybody books, and it keeps the comparison in
# Python: end times are start + duration, which is not a column to filter on.
CLASH_MARGIN = timedelta(hours=12)


def _spoken_date(day):
    """"12 Sep" - %-d is a glibc extension and raises on Windows."""
    return f"{day.day} {day:%b}"


def leave_error(leave):
    name = getattr(leave.psychologist, "fullname", "") or "This psychologist"
    when = (_spoken_date(leave.starts_on) if leave.starts_on == leave.ends_on
            else f"{_spoken_date(leave.starts_on)} to {_spoken_date(leave.ends_on)}")
    detail = f" ({leave.reason})" if leave.reason else ""
    return {"start": f"{name} is away on {when}{detail}. Nothing can be booked "
                     f"into those dates."}


def referral_on_file(child):
    """Has the social worker's case referral been uploaded for this child?

    The referral says why the child is here. Booking counselling before it
    exists puts the appointment ahead of the paperwork that authorises it, and
    the appointment is the part with a child sitting in a room.
    """
    return CaseReferral.objects.filter(child=child).exists()


def missing_referral_error(child):
    return {"child": f"{child.fullname} has no case referral on file. Upload "
                     f"the case referral form on their record before booking "
                     f"a session."}


def ends_at(start, duration_minutes):
    return start + timedelta(minutes=duration_minutes or 60)


def overlaps(a_start, a_end, b_start, b_end):
    """Half-open intervals, so 09:00-10:00 and 10:00-11:00 do NOT overlap.

    Back-to-back appointments are how a full clinic day is booked; a rule that
    called them a clash would be unusable by lunchtime.
    """
    return a_start < b_end and b_start < a_end


# One session as the rule sees it: the columns it needs and the start in the
# agency's own clock, worked out once rather than once per candidate time.
Booked = namedtuple("Booked", "id child_id start duration local_start")


class Diary:
    """What the booking rule needs to know, read once for a range of days.

    `first_day` and `last_day` are local dates, inclusive. The sessions are
    read with room either side for the clash look-around, so any candidate
    that starts inside those days and is no longer than `duration_minutes`
    is judged on complete data. Asking about anything else raises rather than
    answering from a diary that does not hold it.

    `exclude_id` is the appointment being MOVED. It is left out of the
    sessions here, which is the same as ignoring it in both clash checks and
    in capacity, and it also lifts the case-referral rule: that applies to
    new bookings only, so children booked before it existed can still be moved.
    """

    def __init__(self, psychologist, child, first_day, last_day, *, blocks,
                 leaves, psych_sessions, child_sessions, referral_ok,
                 duration_minutes):
        self.psychologist = psychologist
        self.child = child
        self.first_day = first_day
        self.last_day = last_day
        self.blocks = blocks
        self.leaves = leaves
        self.referral_ok = referral_ok
        self.duration_minutes = duration_minutes
        self.psych_sessions = psych_sessions
        self.child_sessions = child_sessions
        self._psych_by_day = {}
        for session in psych_sessions:
            self._psych_by_day.setdefault(session.local_start.date(), []).append(session)

    @classmethod
    def load(cls, psychologist, child, first_day, last_day, *,
             duration_minutes=60, exclude_id=None):
        """Read the diary: a handful of queries however many days it spans."""
        duration = duration_minutes or 60
        lo = timezone.make_aware(datetime.combine(first_day, time.min)) - CLASH_MARGIN
        hi = (timezone.make_aware(datetime.combine(last_day + timedelta(days=1), time.min))
              + timedelta(minutes=duration) + CLASH_MARGIN)

        def sessions(qs):
            qs = qs.exclude(status=Appointment.CANCELLED).filter(
                start__gte=lo, start__lte=hi)
            if exclude_id is not None:
                qs = qs.exclude(pk=exclude_id)
            return [Booked(pk, child_id, start, mins, timezone.localtime(start))
                    for pk, child_id, start, mins in
                    qs.order_by("start", "id").values_list(
                        "id", "child_id", "start", "duration_minutes")]

        # Every active window, not only the ones inside the range: "no
        # availability posted at all" is a different answer from "none this
        # month", and a psychologist has a handful of windows.
        blocks = list(psychologist.availability_blocks.filter(active=True)
                      .order_by("weekday", "date", "start_time", "id"))
        leaves = list(Unavailability.objects
                      .filter(psychologist=psychologist, starts_on__lte=last_day,
                              ends_on__gte=first_day)
                      .order_by("starts_on", "id"))
        for leave in leaves:
            leave.psychologist = psychologist     # the message names them
        referral_ok = (child is None or exclude_id is not None
                       or referral_on_file(child))
        return cls(
            psychologist, child, first_day, last_day, blocks=blocks, leaves=leaves,
            psych_sessions=sessions(Appointment.objects.filter(psychologist=psychologist)),
            child_sessions=(sessions(Appointment.objects.filter(child=child))
                            if child is not None else []),
            referral_ok=referral_ok, duration_minutes=duration)

    # -- what the day looks like -------------------------------------------

    def leave_on(self, day):
        """The leave, if any, that covers this local date. An absence is not a
        preference: a psychologist may work outside their own posted hours but
        not on a day they are not there, so it is checked for every role."""
        for leave in self.leaves:
            if leave.covers(day):
                return leave
        return None

    def blocks_on(self, day):
        """The active windows for one date: the dated ones for that date if
        there are any (a one-off REPLACES the weekly window for that date),
        else the weekly ones for its weekday."""
        return blocks_for_day(self.blocks, day)

    def works_on(self):
        """Weekdays (Monday = 0) the psychologist has a weekly window for."""
        return sorted({b.weekday for b in self.blocks
                       if b.date is None and b.weekday is not None})

    # -- the rule ----------------------------------------------------------

    def _window_for(self, local_start, local_end):
        """The first active block that FULLY contains [start, end), not merely
        the start: a 60-minute session at 11:30 in a window closing at 12:00
        was once accepted."""
        for block in self.blocks_on(local_start.date()):
            if not block.covers(local_start):
                continue
            if local_end.date() == local_start.date() and local_end.time() <= block.end_time:
                return block
        return None

    def _capacity_left(self, block, local_start):
        """Places left in `block` that date, counted by start time within it.
        `block` comes from blocks_on(), so a dated override's capacity is the
        one that applies and the weekly window it replaces is not added."""
        taken = sum(1 for s in self._psych_by_day.get(local_start.date(), ())
                    if block.start_time <= s.local_start.time() < block.end_time)
        return block.capacity - taken

    @staticmethod
    def _clash(sessions, start, end):
        """The first session whose time collides with [start, end)."""
        for other in sessions:
            if not (start - CLASH_MARGIN <= other.start <= end + CLASH_MARGIN):
                continue
            if overlaps(start, end, other.start, ends_at(other.start, other.duration)):
                return other
        return None

    def errors_for(self, start, duration_minutes, *, own_calendar=False):
        """Every reason to refuse this booking, as {field: message}, or {} to
        allow. The one place the rule is written.

        `own_calendar` waives the availability window and its capacity - never
        the overlap rules.
        """
        if timezone.is_naive(start):
            start = timezone.make_aware(start)
        local_start = timezone.localtime(start)
        day = local_start.date()
        if not (self.first_day <= day <= self.last_day
                and (duration_minutes or 60) <= self.duration_minutes):
            raise ValueError("That time is outside what this diary was read for.")

        # Checked before anything about time, and outside the own_calendar
        # waiver: a psychologist may work outside their posted hours, but
        # whether a child's paperwork exists has nothing to do with whose
        # calendar it is. New bookings only (see the class note).
        if self.child is not None and not self.referral_ok:
            return missing_referral_error(self.child)

        # Checked on moves as well as new bookings, unlike the referral: a
        # referral arriving late is paperwork catching up, but putting a
        # session on a day somebody is away is wrong whenever it is done.
        leave = self.leave_on(day)
        if leave is not None:
            return leave_error(leave)

        end = ends_at(start, duration_minutes)
        local_end = ends_at(local_start, duration_minutes)

        if not own_calendar:
            block = self._window_for(local_start, local_end)
            if block is None:
                return {"start": "That time is outside the psychologist's availability "
                                 "window, or the session would run past the end of it."}
            if self._capacity_left(block, local_start) <= 0:
                return {"start": "That availability block is fully booked."}

        clash = self._clash(self.psych_sessions, start, end)
        if clash is not None:
            # The other child is named by case reference only: whoever is
            # booking may be a social worker who did not refer them
            # (visibility.py).
            return {"start": f"{self.psychologist.fullname or 'This psychologist'} is "
                             f"already booked (case C-{clash.child_id:04d}) from "
                             f"{clock(clash.local_start)}."}

        if self.child is not None:
            clash = self._clash(self.child_sessions, start, end)
            if clash is not None:
                return {"child": f"{self.child.fullname} already has an appointment at "
                                 f"{clock(clash.local_start)} "
                                 f"that day. A child cannot be in two places at once."}

        return {}


def errors_for(psychologist, child, start, duration_minutes,
               *, own_calendar=False, exclude_id=None):
    """Every reason to refuse this booking, as {field: message}, or {} to allow.

    Reads the one day the booking falls on and asks the diary's rule.
    `exclude_id` is the appointment being edited, so moving one is not read as
    a clash with itself.
    """
    day = (timezone.localtime(start) if timezone.is_aware(start) else start).date()
    diary = Diary.load(psychologist, child, day, day,
                       duration_minutes=duration_minutes, exclude_id=exclude_id)
    return diary.errors_for(start, duration_minutes, own_calendar=own_calendar)


# How finely the day is sliced when offering start times. Thirty minutes is
# what an office actually books on; a finer grid produces a wall of buttons
# nobody reads, and a coarser one hides real openings.
STEP_MINUTES = 30


def slots_on(diary, day, duration_minutes=60, step_minutes=STEP_MINUTES):
    """Start times on `day` that this booking could actually take, judged on
    `diary`, which must hold that day.

    Every candidate goes through the diary's rule, the one errors_for() uses
    rather than a cheaper approximation of it. That is the whole contract:
    what it offers can be booked. The moment the offer is computed one way and
    the refusal another, the screen starts lying, and a booking screen that
    lies is worse than a blank time field because it looks authoritative.
    """
    # Short-circuit rather than letting every candidate fail the same check:
    # the answer is the same for the whole day.
    if not diary.referral_ok or diary.leave_on(day) is not None:
        return []

    now = timezone.localtime()
    slots = []
    for block in diary.blocks_on(day):
        cursor = timezone.make_aware(datetime.combine(day, block.start_time))
        window_end = timezone.make_aware(datetime.combine(day, block.end_time))
        step = timedelta(minutes=step_minutes)
        while ends_at(cursor, duration_minutes) <= window_end:
            if cursor > now and not diary.errors_for(cursor, duration_minutes):
                slots.append({
                    "start": cursor.strftime("%H:%M"),
                    "end": ends_at(cursor, duration_minutes).strftime("%H:%M"),
                })
            cursor += step
    # De-duplicated by start time. Two overlapping windows on one day would
    # otherwise offer the shared hours twice, and the grid would show 09:00
    # next to 09:00. The availability form refuses to create such a pair, but
    # a database can still hold one, and the fix belongs where the reader is.
    unique = {}
    for slot in slots:
        unique.setdefault(slot["start"], slot)
    return sorted(unique.values(), key=lambda s: s["start"])


def bookable_slots(psychologist, child, day, duration_minutes=60,
                   step_minutes=STEP_MINUTES, exclude_id=None, diary=None):
    """Start times on `day` that this booking could actually take - see
    slots_on for the contract.

    `child` may be None - before one is chosen the form still wants to show
    the shape of the day. `exclude_id` is the appointment being MOVED: without
    it, the time that appointment currently holds is read as a clash with
    itself and disappears from the grid offering to move it. A caller that has
    read the diary already passes it, so the day is not read twice.
    """
    if diary is None:
        diary = Diary.load(psychologist, child, day, day,
                           duration_minutes=duration_minutes, exclude_id=exclude_id)
    return slots_on(diary, day, duration_minutes, step_minutes)


def next_openings(psychologist, child, *, duration_minutes=60, days=14,
                  per_day=2, limit=6, today=None):
    """The next times `child` could be booked with `psychologist`, across
    days - "From my availability" in the dialog after accepting a case.

    Built from the diary's rule, so every time offered is one the booking
    endpoint accepts. At most `per_day` a day, and within a day only times
    that do not overlap each other, so the choice is between days and parts of
    a day rather than between 09:00 and 09:30.
    """
    if child is not None and not referral_on_file(child):
        return []
    today = today or timezone.localdate()
    diary = Diary.load(psychologist, child, today, today + timedelta(days=days - 1),
                       duration_minutes=duration_minutes)
    found = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        taken_until, that_day = "", 0
        for slot in slots_on(diary, day, duration_minutes):
            if that_day >= per_day:
                break
            if slot["start"] < taken_until:      # "HH:MM" compares as time
                continue
            found.append({"date": day.isoformat(), "weekday": f"{day:%A}", **slot})
            taken_until, that_day = slot["end"], that_day + 1
            if len(found) >= limit:
                return found
    return found


def _spoken(day):
    """"Wednesday 9 Sep" - %-d is a glibc extension and raises on Windows."""
    return f"{day:%A} {day.day} {day:%b}"


def _longest_window(blocks):
    """Minutes in the longest of `blocks`, 0 for none."""
    return max(((datetime.combine(date.min, b.end_time)
                 - datetime.combine(date.min, b.start_time)).total_seconds() // 60
                for b in blocks), default=0)


def why_empty(psychologist, day, duration_minutes=60, child=None, exclude_id=None,
              diary=None):
    """Why there is nothing to offer - an empty list is not an explanation.

    "They do not work Wednesdays", "the day is full" and "a 3-hour session
    does not fit" send somebody to three different next actions, and a screen
    that renders all three as an empty panel just looks broken.
    """
    if diary is None:
        diary = Diary.load(psychologist, child, day, day,
                           duration_minutes=duration_minutes, exclude_id=exclude_id)
    name = getattr(psychologist, "fullname", "") or "This psychologist"
    # The paperwork answer comes first: it is true of every day, so telling
    # somebody "fully booked on Wednesday" would send them to try Thursday.
    if child is not None and not diary.referral_ok:
        return missing_referral_error(child)["child"]
    # Before "no availability" and before "fully booked": both of those send
    # somebody to try another day, and if the person is away all week that is
    # a wasted trip back through the form.
    leave = diary.leave_on(day)
    if leave is not None:
        return leave_error(leave)["start"]
    blocks = diary.blocks_on(day)
    if not blocks:
        return f"{name} has no availability on {_spoken(day)}."
    longest = _longest_window(blocks)
    if duration_minutes > longest:
        return (f"A {duration_minutes}-minute session does not fit any of "
                f"{name}'s windows that day - the longest is "
                f"{int(longest)} minutes.")
    if day < timezone.localdate():
        return "That day has already passed."
    if day == timezone.localdate():
        return f"{name}'s windows for today have already started."
    return f"{name} is fully booked on {_spoken(day)}."


def month_view(psychologist, child, year, month, duration_minutes=60, exclude_id=None):
    """Every day of one month, as the booking calendar needs it.

    The month is read ONCE (a diary for all its days) and each day's times are
    judged by the same rule as the day grid and the booking endpoint, so a day
    marked open has times that book. States: past (before today), leave,
    open (at least one bookable start), full (a window applies and nothing is
    bookable) and off (no window that day). Leave outranks off so an away
    stretch is shaded through its weekend; past outranks everything.
    """
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    diary = Diary.load(psychologist, child, first, last,
                       duration_minutes=duration_minutes, exclude_id=exclude_id)
    today = timezone.localdate()
    days = []
    for n in range((last - first).days + 1):
        day = first + timedelta(days=n)
        slots = []
        if day < today:
            state = "past"
        else:
            slots = slots_on(diary, day, duration_minutes)
            if slots:
                state = "open"
            elif diary.leave_on(day) is not None:
                state = "leave"
            elif diary.blocks_on(day):
                state = "full"
            else:
                state = "off"
        days.append({"date": day.isoformat(), "state": state, "slots": slots})

    reason_kind, reason = "", ""
    if not any(d["state"] == "open" for d in days):
        reason_kind, reason = _month_reason(psychologist, child, diary, days, first,
                               duration_minutes)
    # The server's own idea of today, so a browser in another zone does not
    # mark a different day. `blocked` says every working day reads "full" only
    # because the child has no case referral, so the screen can lead with that.
    return {"works": diary.works_on(), "days": days, "reason": reason,
            "reason_kind": reason_kind,
            "today": today.isoformat(),
            "blocked": child is not None and not diary.referral_ok}


def _month_reason(psychologist, child, diary, days, first, duration_minutes):
    """(kind, sentence) for a month with no open day. The kind lets the screen
    tell a reason about this month (away, full, none: paging on may help) from
    one that paging cannot change."""
    name = getattr(psychologist, "fullname", "") or "This psychologist"
    month_name = f"{first:%B}"
    if child is not None and not diary.referral_ok:
        return "referral", missing_referral_error(child)["child"]
    if not diary.blocks:
        return "no_availability", f"{name} has no availability posted yet."
    ahead = [d for d in days if d["state"] != "past"]
    if ahead and all(d["state"] == "leave" for d in ahead):
        return "away", f"{name} is away for the rest of {month_name}."
    if duration_minutes > _longest_window(diary.blocks):
        return "too_long", (f"A {duration_minutes}-minute session does not fit any of "
                            f"{name}'s windows.")
    if any(d["state"] == "full" for d in ahead):
        return "full", f"{name} is fully booked for the rest of {month_name}."
    return "none", f"{name} has no open time in {month_name}."


# How far ahead a booking can be made: a year, this month included. The month
# endpoint, the picker's day check and the picker's range all read this.
MONTHS_AHEAD = 12


def last_bookable_day(today=None):
    """The last day a booking may be made for: the end of the month
    MONTHS_AHEAD months from today's. The month endpoint accepts exactly the
    months up to this one, so every range check derives from here."""
    today = today or timezone.localdate()
    index = today.year * 12 + (today.month - 1) + MONTHS_AHEAD
    year, month = divmod(index, 12)
    return date(year, month + 1, calendar.monthrange(year, month + 1)[1])


def _long_date(day):
    """"Thursday, November 12" - the picker's wording."""
    return f"{day:%A}, {day:%B} {day.day}"


def _unavailable_label(diary, day, duration_minutes):
    """(state, sentence) for a day with nothing to offer. The same order as
    why_empty: leave first (it outranks a missing window), then no window,
    then a length no window fits, then a full day."""
    spoken = _long_date(day)
    if diary.leave_on(day) is not None:
        return "away", f"Away on leave on {spoken}"
    blocks = diary.blocks_on(day)
    if not blocks:
        return "off", f"No hours posted on {spoken}"
    if duration_minutes > _longest_window(blocks):
        return "full", f"No window fits a {duration_minutes}-minute session on {spoken}"
    if day == timezone.localdate():
        return "full", f"No open time left on {spoken}"
    return "full", f"Fully booked on {spoken}"


def psychologist_picker(psychologists, child, *, duration_minutes=60, day=None,
                        exclude_id=None):
    """Which psychologists the booking drawer may still offer.

    A psychologist who cannot take this booking must not be selectable. The
    answer is NOT a count of free places: each person's diary is read once
    (one Diary.load) for the day asked about, or - when no day is chosen - for
    the whole booking window, today to last_bookable_day(), and every
    candidate goes through slots_on, the rule the booking endpoint applies, so
    "open" here means a time on that day books. The walk starts today and
    stops at the first open day, which is `next_open`, so an open psychologist
    is cheap; only one with nothing open all year reads every day.

    States: open, full (a window applies and nothing is bookable), away (leave
    covers the day, or every working day of the range) and off (no window). A
    child with no case referral (and no appointment being moved) cannot be
    booked with ANYBODY, which is true of everyone alike, so nobody is judged:
    the state is "unknown" and `blocked` is true. The labels carry dates only.
    """
    today = timezone.localdate()
    first, last = (day, day) if day else (today, last_bookable_day(today))
    blocked = (child is not None and exclude_id is None
               and not referral_on_file(child))
    rows = []
    for psych in psychologists:
        row = {"id": psych.id, "name": display_name(psych),
               "state": "unknown", "next_open": None, "label": ""}
        rows.append(row)
        if blocked:
            continue
        diary = Diary.load(psych, child, first, last,
                           duration_minutes=duration_minutes, exclude_id=exclude_id)
        if day:
            if slots_on(diary, day, duration_minutes):
                row["state"] = "open"
            else:
                row["state"], row["label"] = _unavailable_label(diary, day, duration_minutes)
            continue
        states = set()
        for n in range((last - first).days + 1):
            this = first + timedelta(days=n)
            if slots_on(diary, this, duration_minutes):
                row.update(state="open", next_open=this.isoformat())
                break
            # Why nothing is offered, without building the sentence per day.
            if diary.leave_on(this) is not None:
                states.add("away")
            elif not diary.blocks_on(this):
                states.add("off")
            else:
                states.add("full")
        if row["state"] != "open":
            # Away only when every working day is leave; "off" when no window
            # applies at all (the old order: full, then away, then off).
            row["state"] = ("full" if "full" in states
                            else "away" if "away" in states else "off")
            row["label"] = {
                "full": "No open time in the next 12 months",
                "away": "Away on leave for the next 12 months",
                "off": "No hours posted in the next 12 months",
            }[row["state"]]
    return {"day": day.isoformat() if day else None,
            "first": first.isoformat(), "last": last.isoformat(),
            "blocked": blocked, "psychologists": rows}
