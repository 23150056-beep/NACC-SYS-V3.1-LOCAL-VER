"""The pre-assessment tracker: when each wizard step was started, last edited
and finished. The only place the rule is written; the views and the data
migration's tests go through these functions.

Every write is a conditional UPDATE after a get_or_create, so two requests
cannot lose each other's stamps. A completed pre-assessment is never written
to here - complete() is the one path that stamps the closing step.
"""
from django.db.models import Case, F, Value, When
from django.db.models.functions import Coalesce

from clinical.models import PreAssessment, PreAssessmentStepTime

STEP_ORDER = [key for key, _ in PreAssessment.STEP_CHOICES]
STEP_LABELS = dict(PreAssessment.STEP_CHOICES)


def _row(pa, step):
    row, _ = PreAssessmentStepTime.objects.get_or_create(pre_assessment=pa, step=step)
    return row


def _open(pa, step):
    """The step's rows, only while the pre-assessment is still open in the
    database (a completion that landed in between wins)."""
    still_open = PreAssessment.objects.filter(pk=pa.pk).exclude(
        status=PreAssessment.COMPLETED).exists()
    if not still_open:
        return PreAssessmentStepTime.objects.none()
    _row(pa, step)
    return PreAssessmentStepTime.objects.filter(pre_assessment=pa, step=step).exclude(
        pre_assessment__status=PreAssessment.COMPLETED)


def entered(pa, step, at):
    """The psychologist arrived on `step`: stamp started_at, once."""
    _open(pa, step).update(started_at=Coalesce(F("started_at"), Value(at)))


def edited(pa, step, at):
    """Something on `step` was changed: started_at if never set, and
    last_edited_at moves to `at` (never backwards)."""
    _open(pa, step).update(
        started_at=Coalesce(F("started_at"), Value(at)),
        last_edited_at=Case(
            When(last_edited_at__gt=at, then=F("last_edited_at")),
            default=Value(at)))


def moved_past(pa, from_step, to_step, at):
    """Moving FORWARD past `from_step` finishes it (the most recent move wins).
    Moving back, or to the same step, records nothing."""
    if from_step not in STEP_ORDER or to_step not in STEP_ORDER:
        return
    if STEP_ORDER.index(to_step) <= STEP_ORDER.index(from_step):
        return
    # A row created here has no start: the step was left without being seen,
    # and a start is never invented (started_at stays NULL, so no duration).
    _open(pa, from_step).update(done_at=at)


def finished(pa, at):
    """complete(): the Review step is done at the completion moment."""
    row = _row(pa, "review")
    PreAssessmentStepTime.objects.filter(pk=row.pk).update(done_at=at)


def seconds_between(started_at, done_at):
    if started_at is None or done_at is None:
        return None
    return max(0, int((done_at - started_at).total_seconds()))


def entries(rows):
    """The five-entry shape the API serves, in step order; a step with no row
    is all nulls. `rows` is any iterable of PreAssessmentStepTime (a prefetch)."""
    by_step = {r.step: r for r in rows}
    out = []
    for key in STEP_ORDER:
        r = by_step.get(key)
        out.append({
            "step": key, "label": STEP_LABELS[key],
            "started_at": r.started_at.isoformat() if r and r.started_at else None,
            "last_edited_at": r.last_edited_at.isoformat() if r and r.last_edited_at else None,
            "done_at": r.done_at.isoformat() if r and r.done_at else None,
            "seconds": seconds_between(r.started_at, r.done_at) if r else None,
        })
    return out


def open_pre_assessment_at_problems(child_id, psychologist):
    """The psychologist's live pre-assessment for this child if it is open and
    on the problems step - the same "newest open row newer than the latest
    completion" rule the wizard's resume uses. None otherwise."""
    rows = list(PreAssessment.objects.filter(child_id=child_id))
    done = [(p.created_at, p.id) for p in rows if p.status == PreAssessment.COMPLETED]
    last_done = max(done, default=None)
    mine = [p for p in rows
            if p.psychologist_id == getattr(psychologist, "id", None)
            and p.status != PreAssessment.COMPLETED
            and (last_done is None or (p.created_at, p.id) > last_done)]
    if not mine:
        return None
    newest = max(mine, key=lambda p: (p.created_at, p.id))
    return newest if newest.current_step == "problems" else None
