"""Where a psychologist's bookable windows are worked out.

Extracted so there is one copy of the capacity rule rather than three. It was
already mirrored between AvailabilityViewSet.next_slots and
AppointmentViewSet._validate_booking, with a comment in each saying so; the
chatbot asking "who is free on Friday?" would have been the third, and three
copies of an arithmetic rule is three chances for the booking screen, the
booking endpoint and the assistant to disagree about whether a slot exists.
"""
from datetime import datetime, time, timedelta

from django.utils import timezone

from scheduling.models import Appointment, AvailabilityBlock


def blocks_for_day(blocks, day):
    """The windows that apply on one local date, from active `blocks`.

    A day holds one window. A one-off window on a date REPLACES that weekday's
    weekly window for that date only ("this Monday, 1-3 PM, 1 slot"), so the
    dated ones win when any exist and the weekly ones are the fallback. Every
    reader that needs "what is on this day" goes through here, so the booking
    rule, the slot grid, the month view and the assistant agree.
    """
    dated = [b for b in blocks if b.active and b.date == day]
    if dated:
        return dated
    return [b for b in blocks
            if b.active and b.date is None and b.weekday is not None
            and b.weekday == day.weekday()]


def free_windows(psychologist, start_date, end_date, now=None):
    """Bookable windows with spare capacity for one psychologist, [start, end).

    Every non-cancelled appointment starting inside a block's time window
    occupies one of its places — the same arithmetic the booking endpoint
    applies when it accepts or refuses. What is offered here can therefore
    always actually be booked.

    A window that has already begun today is not an opening: the appointment
    serializer refuses a start in the past, so offering it produces a slot
    that can only ever answer "cannot book an appointment in the past".
    """
    now = now or timezone.localtime()
    today = now.date()
    blocks = list(AvailabilityBlock.objects.filter(
        psychologist=psychologist, active=True))
    if not blocks:
        return []

    windows, day = [], start_date
    while day < end_date:
        if day < today:                      # the past is never bookable
            day += timedelta(days=1)
            continue
        day_start = timezone.make_aware(datetime.combine(day, time.min))
        for block in blocks_for_day(blocks, day):
            if day == today and block.start_time <= now.time():
                continue
            taken = (Appointment.objects
                     .filter(psychologist=psychologist,
                             start__gte=day_start,
                             start__lt=day_start + timedelta(days=1))
                     .exclude(status=Appointment.CANCELLED)
                     .filter(start__time__gte=block.start_time,
                             start__time__lt=block.end_time)
                     .count())
            remaining = block.capacity - taken
            if remaining > 0:
                windows.append({
                    "date": day.isoformat(),
                    "weekday": day.strftime("%A"),
                    "start": str(block.start_time)[:5],
                    "end": str(block.end_time)[:5],
                    "remaining": remaining,
                })
        day += timedelta(days=1)
    return windows
