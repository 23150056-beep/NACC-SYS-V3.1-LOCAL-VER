"""Opening, moving and closing an adoption case.

Every rule the spec calls statutory lives in this module rather than in a view
or a serializer, for one reason: there is more than one way into the system —
the API, a management command, a future import — and a rule enforced in a view
is a rule that a shell script can walk straight past. The viewsets call these
functions; they do not re-implement them.
"""
from django.db import transaction
from django.utils import timezone

from adoption.models import (
    AdoptionCase, AdoptionStage, Handoff, Requirement, RequirementTemplate, StageEvent,
)
from children.models import Child
from clinical.models import PreAssessment

FIRST_STAGE = 1
LAST_STAGE = 8


class NotAdmissible(Exception):
    """The child cannot enter the pipeline, and why."""


class StageBlocked(Exception):
    """The stage will not move, and what is missing.

    Carries the blocking requirements so the caller can show them rather than
    a bare refusal — a tracker that says "no" without saying what is missing
    just sends somebody hunting through a docket.
    """

    def __init__(self, message, blockers=()):
        super().__init__(message)
        self.blockers = list(blockers)


def has_completed_assessment(child):
    """The single hard gate. No manual override, not even for administrators."""
    return PreAssessment.objects.filter(
        child=child, status=PreAssessment.COMPLETED).exists()


def pending_handoffs():
    """Children the psychologist has released who are not yet in the pipeline.

    This is the handoff banner's whole query. A child with an open case is no
    longer pending, and one whose case was closed does not come back — closing
    is a decision, not a lapse.
    """
    return (Handoff.objects
            .exclude(child__adoption_cases__isnull=False)
            .filter(child__status=Child.ACTIVE)
            .select_related("child", "released_by", "pre_assessment"))


def record_handoff(pre_assessment):
    """Release a child to staff. Idempotent per assessment."""
    Handoff.objects.get_or_create(
        child=pre_assessment.child,
        pre_assessment=pre_assessment,
        defaults={"released_by": pre_assessment.psychologist},
    )


@transaction.atomic
def admit(child, owner, actor, note=""):
    """Open an adoption case at stage 1 and seed its whole docket.

    Admission is an explicit human act. The assessment gate is checked here
    and nowhere else, so no caller can be written that skips it.
    """
    if not has_completed_assessment(child):
        raise NotAdmissible(
            "This child has no completed assessment. The psychologist must sign "
            "off before an adoption case can be opened.")
    if child.status != Child.ACTIVE:
        raise NotAdmissible("This child's record is archived.")
    if AdoptionCase.objects.filter(child=child, closed_at__isnull=True).exists():
        raise NotAdmissible("This child already has an open adoption case.")

    stage = AdoptionStage.objects.get(number=FIRST_STAGE)
    case = AdoptionCase.objects.create(child=child, current_stage=stage, owner=owner)

    # The whole docket, not just stage 1: the child view lists every
    # requirement across all stages and the progress bar measures the entire
    # journey, so the rows have to exist from day one.
    Requirement.objects.bulk_create([
        Requirement(case=case, stage=t.stage, code=t.code, label=t.label,
                    owned_externally=t.owned_externally, position=t.position)
        for t in RequirementTemplate.objects.select_related("stage").all()
    ])

    StageEvent.objects.create(
        case=case, from_stage=None, to_stage=stage, actor=actor,
        direction=StageEvent.ADVANCE,
        note=note or "Admitted to the adoption pipeline.")

    # Condition 2 of the trigger: staff confirms the plan, and admitting IS
    # that confirmation. A case in the adoption pipeline whose child record
    # still says Foster Care is a record that contradicts itself.
    if child.case_type != "Adoption":
        child.case_type = "Adoption"
        child.save(update_fields=["case_type", "updated_at"])

    return case


def blockers_for(case):
    """The current stage's unmet exit conditions, in docket order."""
    return list(case.requirements
                .filter(stage=case.current_stage)
                .exclude(state__in=Requirement.SATISFIED)
                .order_by("position", "id"))


@transaction.atomic
def advance(case, actor, note=""):
    """Move one stage forward, if every exit condition of this stage is met.

    This guard is the module. A tracker that lets somebody move a case past a
    statutory requirement is worse than no tracker, because what it leaves
    behind is a confident record of a step that did not happen.
    """
    if case.closed_at:
        raise StageBlocked("This case is closed.")
    if case.on_hold:
        raise StageBlocked(
            "The assessment has been reopened, so this case is on hold.")
    if case.current_stage.number >= LAST_STAGE:
        raise StageBlocked(
            "This is the final stage. Close the case with a reason instead.")

    blockers = blockers_for(case)
    if blockers:
        raise StageBlocked("This stage still has unmet exit conditions.", blockers)

    nxt = AdoptionStage.objects.get(number=case.current_stage.number + 1)
    StageEvent.objects.create(
        case=case, from_stage=case.current_stage, to_stage=nxt, actor=actor,
        direction=StageEvent.ADVANCE, note=note)
    case.current_stage = nxt
    case.stage_entered_at = timezone.now()
    case.save(update_fields=["current_stage", "stage_entered_at", "updated_at"])
    return case


@transaction.atomic
def revert(case, actor, note):
    """Step back exactly one stage, with a reason.

    One stage at a time, and never without a note: reverting is how a mistake
    gets corrected, and a correction with no explanation is indistinguishable
    from tampering when somebody reads the history a year later.
    """
    if case.closed_at:
        raise StageBlocked("This case is closed.")
    if not (note or "").strip():
        raise StageBlocked("Reverting a stage requires a note.")
    if case.current_stage.number <= FIRST_STAGE:
        raise StageBlocked("This case is already at the first stage.")

    prev = AdoptionStage.objects.get(number=case.current_stage.number - 1)
    StageEvent.objects.create(
        case=case, from_stage=case.current_stage, to_stage=prev, actor=actor,
        direction=StageEvent.REVERT, note=note)
    case.current_stage = prev
    case.stage_entered_at = timezone.now()
    case.save(update_fields=["current_stage", "stage_entered_at", "updated_at"])
    return case


@transaction.atomic
def close(case, actor, reason, note=""):
    """End the case, for any of the reasons the process actually ends.

    Closing never touches the child's own record. A disrupted placement is the
    end of an adoption case, not the end of a child's care — and a pipeline
    that only models success gets worked around within a month.
    """
    if reason not in {r for r, _ in AdoptionCase.CLOSURE_CHOICES}:
        raise StageBlocked(f"{reason!r} is not a closure reason.")
    if case.closed_at:
        raise StageBlocked("This case is already closed.")

    case.closed_at = timezone.now()
    case.closure_reason = reason
    case.closure_note = note
    case.save(update_fields=["closed_at", "closure_reason", "closure_note", "updated_at"])
    StageEvent.objects.create(
        case=case, from_stage=case.current_stage, to_stage=case.current_stage,
        actor=actor, direction=StageEvent.ADVANCE,
        note=f"Case closed: {reason}. {note}".strip())
    return case


def set_hold(child, on_hold):
    """Freeze or thaw every open case for a child.

    Called when a psychologist reopens or re-completes an assessment. The case
    is frozen rather than deleted: the stage stays, the clocks pause, and the
    banner says why.
    """
    AdoptionCase.objects.filter(
        child=child, closed_at__isnull=True).update(on_hold=on_hold)
