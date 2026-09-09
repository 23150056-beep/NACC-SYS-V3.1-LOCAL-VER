"""One derived status, used by every screen - spec section 4.

Nothing here is stored. A status column would be a status that can disagree
with the data behind it, and on a compliance tracker that disagreement is the
entire failure: a green chip over an expired statutory window is worse than no
chip at all.

The order of the branches is the specification and it is load-bearing. A case
that is both on hold and overdue reads as ON HOLD, because the hold is the
thing a reader has to deal with first - the overdue days are a consequence of
it, not a separate problem.
"""
from datetime import timedelta

from django.utils import timezone

from adoption.models import Requirement

ON_HOLD = "on_hold"
COMPLETE = "complete"
OVERDUE = "overdue"
AT_RISK = "at_risk"
WAITING = "waiting"
ON_TRACK = "on_track"

# What each one means on screen. Amber and red are the only two that ask for
# anything; blue says "not yours to chase"; grey says "stopped".
LABELS = {
    ON_HOLD: "On hold",
    COMPLETE: "Complete",
    OVERDUE: "Overdue",
    AT_RISK: "At risk",
    WAITING: "Waiting",
    ON_TRACK: "On track",
}

# Days left on a statutory clock before the case starts reading as at risk.
CLOCK_WARNING_DAYS = 14


def days_in_stage(case):
    return (timezone.now() - case.stage_entered_at).days


def clock_days_left(clock, today=None):
    """Remaining days, extensions included. Negative once the window is blown.

    An extension lengthens the window rather than restarting it, so a case that
    already overran keeps showing the overrun.
    """
    today = today or timezone.localdate()
    ends = clock.started_at + timedelta(
        days=clock.duration_days + clock.extension_days)
    return (ends - today).days


def _any_clock_near_expiry(case):
    return any(
        clock_days_left(c) < CLOCK_WARNING_DAYS
        for c in case.clocks.all() if c.satisfied_at is None
    )


def _blocked_externally(case):
    """Every outstanding exit condition belongs to somebody outside this office.

    Deliberately "every", not "any": if one of the blockers is ours, the case
    is ours to move and should not be sitting under a chip that says we are
    waiting on the court.
    """
    outstanding = [r for r in case.requirements.all()
                   if r.stage_id == case.current_stage_id
                   and r.state not in Requirement.SATISFIED]
    return bool(outstanding) and all(r.owned_externally for r in outstanding)


def of(case):
    """The one value every chip, border and filter reads."""
    if case.on_hold:
        return ON_HOLD
    if case.closed_at:
        return COMPLETE

    target = case.current_stage.target_days
    if target:
        elapsed = days_in_stage(case)
        if elapsed > target:
            return OVERDUE
        if elapsed > target * 0.75:
            return AT_RISK

    if _any_clock_near_expiry(case):
        return AT_RISK
    if _blocked_externally(case):
        return WAITING
    return ON_TRACK


def progress_percent(case):
    """How far through the WHOLE journey, not the current stage.

    Counts waived alongside verified. The spec says "verified requirements",
    but a waiver is a decision that the step is finished with - not counting it
    would leave the bar permanently short of 100% on any case that ever needed
    one, which reads as an unfinished case rather than a waived requirement.
    """
    rows = list(case.requirements.all())
    if not rows:
        return 0
    done = sum(1 for r in rows if r.state in Requirement.SATISFIED)
    return round(done * 100 / len(rows))
