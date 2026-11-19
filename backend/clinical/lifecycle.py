"""The pre-assessment's status table and the rule for completing one.

A completed pre-assessment is final: a re-assessment is a new row. Nothing
writes `pending` any more (a new row starts in_progress) but older rows may
still hold it, so the table keeps it.
"""
from clinical.models import ConsentRecord, PreAssessment
from config.lifecycle import Lifecycle

PRE_ASSESSMENT_LIFECYCLE = Lifecycle(PreAssessment, "status", {
    PreAssessment.PENDING: {PreAssessment.IN_PROGRESS, PreAssessment.COMPLETED},
    PreAssessment.IN_PROGRESS: {PreAssessment.COMPLETED},
    PreAssessment.COMPLETED: set(),
}, name="pre-assessment")

CONSENT_REQUIRED = "A signed consent is required before completing the pre-assessment."
INSTRUMENT_REQUIRED = "Select at least one instrument title."
LOST_RACE = "This pre-assessment was completed a moment ago. Reload to see it."
KEEPS_EVIDENCE = ("A completed pre-assessment keeps the consent and instruments it "
                  "was completed with. Start a re-assessment to change them.")
CANNOT_DELETE = "A completed pre-assessment is part of the child's record and cannot be deleted."
CONSENT_IS_EVIDENCE = ("This consent is part of a completed pre-assessment and "
                       "cannot be deleted.")
CONSENT_STAYS_SIGNED = ("This consent is part of a completed pre-assessment and "
                        "must stay signed.")


def completion_problems(pa):
    """What stops `pa` being completed, as (field, sentence) pairs; [] when it
    may be. The one rule: complete() asks it, and so does the test that every
    seeded completed row would pass it."""
    problems = []
    if not pa.consent_id or pa.consent.status != ConsentRecord.SIGNED:
        problems.append(("consent", CONSENT_REQUIRED))
    if pa.instruments.count() == 0:
        problems.append(("instruments", INSTRUMENT_REQUIRED))
    return problems
