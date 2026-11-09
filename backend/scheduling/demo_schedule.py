"""Give the demo caseload a working week, and put its sessions inside it.

Two faults, and the second only became visible once booking got strict.

**Nobody had availability.** The three psychologists carrying all forty
children had no bookable window between them, while the one psychologist who
did have windows had no caseload. So the single thing a staff member opens the
calendar to do - book a session for a child - was impossible for every child
in the database, and the booking screen correctly said so for all of them.

**The sessions were at night and at weekends.** The seeder placed them at
`now()` plus a few hours of jitter, so whatever time of day the seeder was run
became the office's clinic hours. A demo of a child-welfare system showed
counselling at 23:22 on a Sunday.

Both are fixed here rather than only in the seeder, because there are already
databases with the bad data in them and re-seeding throws away six months of
invented history to fix a timestamp.

The rule this module holds itself to: **what it writes, the booking endpoint
would accept.** Demo data the real rules would reject is not a sample of the
system - it is a second system sharing a database, and every feature built
against it inherits the difference.
"""
from datetime import datetime, time, timedelta

from django.utils import timezone

from scheduling.availability import blocks_for_day
from scheduling.models import Appointment, AvailabilityBlock

# Monday to Friday. A government office, not a hotline.
CLINIC_WEEKDAYS = (0, 1, 2, 3, 4)

# One window a day, which is the rule the availability form enforces (a day
# holds a single window and its slot count is the day's limit). The lunch hour
# is inside it: a session is simply not offered a window of its own for it.
CLINIC_WINDOWS = (
    (time(8, 0), time(17, 0)),
)

# The day's limit. Nine sixty-minute sessions fit between 8:00 AM and 5:00 PM;
# eight keeps the diary honest, and MAX_SESSIONS_PER_DAY below stays under it
# so a respread day can always take one more.
DEFAULT_CAPACITY = 8

# How far either side of its original date an appointment may be moved to find
# a free weekday slot. Two working weeks is plenty and keeps the history
# roughly where the seeder meant it.
SEARCH_DAYS = 12

# Eight sessions are the day's limit. Stopping at five leaves
# every clinic day with something to book into, which is the entire point of
# repairing the demo: the seeder puts every child on the same few dates, so
# without a ceiling the repair produces days that are perfectly correct and
# completely unbookable.
MAX_SESSIONS_PER_DAY = 5


def install_availability(psychologists, capacity=DEFAULT_CAPACITY):
    """A Monday-to-Friday working week for each psychologist.

    One window a day, and a day that already has one is left alone - a
    capacity or hours somebody corrected in the form survive, and a second
    window would break the rule the endpoint enforces (one per psychologist
    per weekday). Re-running fills gaps and never overwrites.

    Returns the number of blocks created.
    """
    made = 0
    for person in psychologists:
        for weekday in CLINIC_WEEKDAYS:
            if AvailabilityBlock.objects.filter(
                    psychologist=person, weekday=weekday, date=None,
                    active=True).exists():
                continue
            start, end = CLINIC_WINDOWS[0]
            AvailabilityBlock.objects.create(
                psychologist=person, weekday=weekday, date=None,
                start_time=start, end_time=end,
                capacity=capacity, active=True)
            made += 1
    return made


def prune_overlapping_blocks():
    """Remove windows that straddle another, keeping the older of each pair.

    Repairs databases an earlier version of install_availability damaged: it
    wrote 08:00-12:00 straight over an existing 09:00-12:00, which the
    availability form refuses to create and the slot grid then rendered as
    09:00 beside 09:00.

    The older one is kept because it is the one somebody chose; the newer is
    the one a seeder guessed.

    Returns the number removed.
    """
    removed = 0
    for person_id in (AvailabilityBlock.objects
                      .values_list("psychologist_id", flat=True).distinct()):
        blocks = list(AvailabilityBlock.objects
                      .filter(psychologist_id=person_id, active=True)
                      .order_by("id"))
        kept = []
        for block in blocks:
            straddles = any(
                other.date == block.date
                and other.weekday == block.weekday
                and other.start_time < block.end_time
                and block.start_time < other.end_time
                for other in kept)
            if straddles:
                block.delete()
                removed += 1
            else:
                kept.append(block)
    return removed


def _grid(windows, step_minutes=30, duration_minutes=60):
    """Every start time a session of `duration_minutes` can take in `windows`."""
    slots = []
    for start, end in windows:
        cursor = datetime.combine(datetime.min.date(), start)
        closes = datetime.combine(datetime.min.date(), end)
        while cursor + timedelta(minutes=duration_minutes) <= closes:
            slots.append(cursor.time())
            cursor += timedelta(minutes=step_minutes)
    return slots


def clinic_slots(step_minutes=30, duration_minutes=60):
    """Every start time a session of `duration_minutes` can take in a clinic day."""
    return _grid(CLINIC_WINDOWS, step_minutes, duration_minutes)


class _Hours:
    """Where each psychologist's sessions may sit: their own posted windows.

    The windows a database really holds, not the constant the seeder writes -
    a demo database seeded before the one-window rule still has a lunch gap,
    and a session placed in it is one the booking rule refuses to move. A
    psychologist with no window at all falls back to the clinic day.
    """

    def __init__(self):
        self._blocks = {}

    def windows(self, psychologist_id, day):
        if psychologist_id not in self._blocks:
            self._blocks[psychologist_id] = list(AvailabilityBlock.objects.filter(
                psychologist_id=psychologist_id, active=True))
        blocks = self._blocks[psychologist_id]
        if not blocks:
            return list(CLINIC_WINDOWS), MAX_SESSIONS_PER_DAY
        today = blocks_for_day(blocks, day)
        return ([(b.start_time, b.end_time) for b in today],
                min(MAX_SESSIONS_PER_DAY, sum(b.capacity for b in today)))

    def slots(self, psychologist_id, day, duration_minutes):
        windows, _ = self.windows(psychologist_id, day)
        return _grid(windows, duration_minutes=duration_minutes)

    def limit(self, psychologist_id, day):
        return self.windows(psychologist_id, day)[1]


def _is_clinic_time(local_dt, duration_minutes, psychologist_id=None, hours=None):
    if local_dt.weekday() not in CLINIC_WEEKDAYS:
        return False
    if hours is not None:
        return local_dt.time() in hours.slots(
            psychologist_id, local_dt.date(), duration_minutes)
    return local_dt.time() in clinic_slots(duration_minutes=duration_minutes)


def _span(slot, minutes, step_minutes=30):
    """Every grid time a session starting at `slot` occupies.

    Reserving only the start time is not enough: the grid steps every 30
    minutes and a session runs 60, so 08:00 has to close 08:30 as well. Marking
    just the start let two sessions be placed half an hour apart and the
    booking rules then refused the data this module had written.
    """
    base = datetime.combine(datetime.min.date(), slot)
    covered, cursor = [], base
    while cursor < base + timedelta(minutes=minutes):
        covered.append(cursor.time())
        cursor += timedelta(minutes=step_minutes)
    return covered


def _candidate_days(day):
    """The original day first, then outwards - nearest weekday wins.

    Moving a session should be the smallest change that makes it legal, so a
    Saturday becomes the Friday before or the Monday after rather than being
    swept to the start of the month.
    """
    seen = []
    for offset in range(0, SEARCH_DAYS + 1):
        for delta in ((0,) if offset == 0 else (-offset, offset)):
            candidate = day + timedelta(days=delta)
            if candidate.weekday() in CLINIC_WEEKDAYS and candidate not in seen:
                seen.append(candidate)
    return seen


def realign_appointments(duration_minutes=60, respread=False):
    """Move every out-of-hours appointment onto a real clinic slot.

    `respread` re-places the legal ones too. An in-hours day can still be a
    useless one: a pass that packed eight sessions into a single Monday leaves
    a calendar nobody can book into, and every appointment in it is perfectly
    valid. Ordinary runs leave valid rows alone, which is right; respreading is
    for undoing a bad pack.

    Cancelled ones are left alone: they are a record of something that did not
    happen, and tidying their timestamps would rewrite history for no benefit.

    Deterministic - appointments are processed oldest first and each takes the
    first free slot - so running it twice gives the same answer and running it
    on an already-clean database changes nothing.

    Returns (moved, left_alone).
    """
    hours = _Hours()
    taken = {}          # (psychologist_id, date) -> set of occupied grid times
    counts = {}         # (psychologist_id, date) -> sessions placed that day
    for appt in (Appointment.objects
                 .exclude(status=Appointment.CANCELLED)
                 .select_related("psychologist")
                 .order_by("start", "id")):
        local = timezone.localtime(appt.start)
        minutes = appt.duration_minutes or duration_minutes
        if not respread and _is_clinic_time(local, minutes, appt.psychologist_id, hours):
            key = (appt.psychologist_id, local.date())
            taken.setdefault(key, set()).update(_span(local.time(), minutes))
            counts[key] = counts.get(key, 0) + 1

    moved = 0
    kept = 0
    for appt in (Appointment.objects
                 .exclude(status=Appointment.CANCELLED)
                 .select_related("psychologist")
                 .order_by("start", "id")):
        local = timezone.localtime(appt.start)
        minutes = appt.duration_minutes or duration_minutes
        if not respread and _is_clinic_time(local, minutes, appt.psychologist_id, hours):
            kept += 1
            continue

        placed = None
        for day in _candidate_days(local.date()):
            used = taken.setdefault((appt.psychologist_id, day), set())
            if counts.get((appt.psychologist_id, day), 0) >= hours.limit(
                    appt.psychologist_id, day):
                continue
            for slot in hours.slots(appt.psychologist_id, day, minutes):
                span = _span(slot, minutes)
                if used.isdisjoint(span):
                    used.update(span)
                    counts[(appt.psychologist_id, day)] =                         counts.get((appt.psychologist_id, day), 0) + 1
                    placed = timezone.make_aware(datetime.combine(day, slot))
                    break
            if placed is not None:
                break

        if placed is None:                      # no room anywhere nearby
            kept += 1
            continue

        Appointment.objects.filter(pk=appt.pk).update(start=placed)
        moved += 1

    return moved, kept
