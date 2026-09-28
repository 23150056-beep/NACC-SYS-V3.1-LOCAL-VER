"""Why a case is closed, and which reasons the record bears out (28 Sep 2026).

The ISA closes a case for where the child went - reunified, adopted,
transferred: `TerminationRecord.CASE_OUTCOMES`, unchanged. A psychologist
closes one for where the clinical work ended, and each of those reasons is
offered only when the child's record shows it happened:

- "Counseling completed" needs a counseling session marked completed;
- the two pre-assessment reasons need a completed pre-assessment and no
  counseling at all;
- "Counseling discontinued" needs counseling to have been started;
- a referral elsewhere, and Other, are always open.

The dialog asks `ChildViewSet.closure_reasons` for this list and the terminate
endpoint refuses by the same function, so the two cannot disagree about which
reasons exist. Design:
docs/superpowers/specs/2026-09-28-interview-upload-and-closure-reasons-design.md
"""
from accounts.models import Role
from children.models import Child, TerminationRecord

COUNSELING_COMPLETED = "Counseling completed"
FAVORABLE_PRE_ASSESSMENT = "Favorable pre-assessment, no counseling needed"
PRE_ASSESSMENT_ONLY = "Pre-assessment only, evaluation completed"
DISCONTINUED = "Counseling discontinued"
REFERRED = "Referred to another specialist or service"
OTHER = "Other"

# What each clinical reason means, for the dialog.
HINTS = {
    COUNSELING_COMPLETED: "Counseling sessions are finished and the goals were met.",
    FAVORABLE_PRE_ASSESSMENT: "The pre-assessment found the child adjusting well; counseling is not needed.",
    PRE_ASSESSMENT_ONLY: "The referral asked for an evaluation, and it is done.",
    DISCONTINUED: "Counseling started but stopped - the child or family stopped attending or declined.",
    REFERRED: "The child needs a psychiatrist, another specialist or another service.",
    OTHER: "Anything else. The closing summary says what.",
}


def record_facts(child):
    """What the record shows about the clinical work, for the reasons below
    and for the dialog to show beside them."""
    from clinical.models import PreAssessment
    from scheduling.models import Appointment

    counseling = Appointment.objects.filter(
        child=child, purpose__in=[Appointment.SESSION, Appointment.FOLLOW_UP])
    held = counseling.filter(status=Appointment.COMPLETED).count()
    in_counseling = child.case_status == Child.STAGE_COUNSELING
    return {
        "pre_assessment_completed": PreAssessment.objects.filter(
            child=child, status=PreAssessment.COMPLETED).exists(),
        "in_counseling": in_counseling,
        "sessions_held": held,
        "counseling_started": (in_counseling or held > 0
                               or counseling.exclude(status=Appointment.CANCELLED).exists()),
    }


def _why_not(reason, facts):
    """'' when the record bears the reason out, else what is missing."""
    no_counseling = not facts["in_counseling"] and facts["sessions_held"] == 0
    if reason == COUNSELING_COMPLETED and facts["sessions_held"] == 0:
        return "No counseling session is marked completed on the calendar yet."
    if reason in (FAVORABLE_PRE_ASSESSMENT, PRE_ASSESSMENT_ONLY):
        if not facts["pre_assessment_completed"]:
            return "The pre-assessment is not completed yet."
        if not no_counseling:
            return "Counseling has started on this case - choose a counseling reason."
    if reason == DISCONTINUED and not facts["counseling_started"]:
        return "No counseling was started on this case."
    return ""


def reasons_for(role, child):
    """The reasons this role chooses from for this child, in order, each with
    whether the record bears it out and why not."""
    if role == Role.PSYCHOLOGIST:
        facts = record_facts(child)
        return [{"value": r, "hint": HINTS.get(r, ""), "why": _why_not(r, facts),
                 "available": not _why_not(r, facts)}
                for r in TerminationRecord.CLINICAL], facts
    return [{"value": r, "hint": "", "why": "", "available": True}
            for r in TerminationRecord.CASE_OUTCOMES], None


def refusal(role, child, reason):
    """Why this reason cannot close this case, or None when it can."""
    listed = (TerminationRecord.CLINICAL if role == Role.PSYCHOLOGIST
              else TerminationRecord.CASE_OUTCOMES)
    if reason not in listed:
        return "Select a termination reason."
    if role == Role.PSYCHOLOGIST:
        return _why_not(reason, record_facts(child)) or None
    return None
