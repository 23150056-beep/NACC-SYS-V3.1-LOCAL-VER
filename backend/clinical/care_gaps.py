"""Deterministic care-gap alerts (AI feature A4 — no LLM, free, reliable).

Each rule is a plain queryset/date check. Every alert names the child and the
gap so the dashboard list is directly actionable.
"""
from django.db.models import Count
from django.utils import timezone

from children.models import Child
from clinical.models import (PreAssessment, ConsentRecord, PsychologicalReport,
                             SelfReportFlag)
from scheduling.models import Appointment

# Thresholds — confirm with RACCO I; kept here so they are easy to tune.
FOLLOW_UP_OVERDUE_DAYS = 30      # no completed session in this long
PRE_ASSESSMENT_LAG_DAYS = 14     # intake without a completed pre-assessment
REPORT_LAG_DAYS = 14             # completed pre-assessment without a report


def compute_alerts(children_qs):
    """Compute care-gap alerts for the given (already role-scoped) children."""
    today = timezone.localdate()
    now = timezone.now()
    children = list(children_qs.filter(status=Child.ACTIVE)
                    .prefetch_related("pre_assessments"))
    ids = [c.id for c in children]

    last_completed_appt = {}
    has_upcoming = set()
    for a in Appointment.objects.filter(child_id__in=ids).order_by("start"):
        if a.status == Appointment.COMPLETED:
            last_completed_appt[a.child_id] = a.start
        if a.status == Appointment.SCHEDULED and a.start >= now:
            has_upcoming.add(a.child_id)

    signed_consent = set(ConsentRecord.objects.filter(
        child_id__in=ids, status=ConsentRecord.SIGNED).values_list("child_id", flat=True))
    has_report = set(PsychologicalReport.objects.filter(
        child_id__in=ids).values_list("child_id", flat=True))

    # Children with an unacknowledged self-report flag. Reads persisted rows —
    # no text analysis happens here, because this runs on every page load.
    flagged = dict(SelfReportFlag.objects
                   .filter(child_id__in=ids, reviewed_at__isnull=True)
                   .values_list("child_id")
                   .annotate(n=Count("id")))

    alerts = []

    def add(child, gap_type, message, severity="warning"):
        alerts.append({
            "type": gap_type, "severity": severity,
            "child_id": child.id, "child_name": child.fullname,
            "message": message,
        })

    for c in children:
        pas = list(c.pre_assessments.all())
        completed_pas = [p for p in pas if p.status == PreAssessment.COMPLETED]
        open_pas = [p for p in pas if p.status != PreAssessment.COMPLETED]

        # 1. Consent missing on an open pre-assessment (no signed consent on file).
        if open_pas and c.id not in signed_consent:
            add(c, "consent_missing",
                "Pre-assessment in progress without a signed consent.", "danger")

        # 2. No completed pre-assessment too long after intake.
        if not completed_pas and c.created_at and \
                (today - c.created_at.date()).days > PRE_ASSESSMENT_LAG_DAYS:
            add(c, "pre_assessment_overdue",
                f"No completed pre-assessment {PRE_ASSESSMENT_LAG_DAYS}+ days after intake.")

        # 3. No report uploaded after a completed pre-assessment.
        if completed_pas and c.id not in has_report:
            oldest = min(p.completed_at or now for p in completed_pas)
            if (now - oldest).days > REPORT_LAG_DAYS:
                add(c, "report_missing",
                    "Completed pre-assessment but no psychological report uploaded.")

        # 4. Overdue follow-up: last completed session too long ago.
        last = last_completed_appt.get(c.id)
        if last and (now - last).days > FOLLOW_UP_OVERDUE_DAYS and c.id not in has_upcoming:
            add(c, "follow_up_overdue",
                f"Last completed session over {FOLLOW_UP_OVERDUE_DAYS} days ago.")

        # 5. Active child with no upcoming appointment at all.
        if c.id not in has_upcoming:
            add(c, "no_upcoming_appointment",
                "Active case with no upcoming appointment.", "info")

        # 6. The child said something worth reading. This asserts nothing
        # about the case notes — they are never read — only that her own
        # words are waiting. The message deliberately does not quote her:
        # her words belong on her page beside the notes, not in a caseload
        # list that gets skimmed.
        waiting = flagged.get(c.id)
        if waiting:
            add(c, "self_report_concern",
                f"{waiting} self-report answer{'s' if waiting > 1 else ''} "
                f"awaiting review.", "danger")

    severity_rank = {"danger": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (severity_rank.get(a["severity"], 3), a["child_name"]))
    return alerts
