"""Deterministic care-gap alerts (AI feature A4 — no LLM, free, reliable).

Each rule is a plain queryset/date check. Every alert names the child and the
gap so the dashboard list is directly actionable.

Two rule sets since 3 Oct 2026, because a gap is only worth listing for
somebody who can close it. `compute_alerts` is the clinical set (the
psychologist's, and the ISA's agency-wide); `compute_staff_alerts` is a social
worker's. `alerts_for` picks between them by role, and the Dashboard card and
the assistant's list_care_gaps both go through it, so they name the same
children.
"""
from django.db.models import Count
from django.utils import timezone

from accounts.models import Role
from accounts.scoping import role_of
from children.models import AssignmentRequest, Child
from clinical.models import (CaseReferral, ConsentRecord, PreAssessment,
                             PsychologicalReport, SelfReportFlag)
from scheduling.models import Appointment

# Thresholds — confirm with RACCO I; kept here so they are easy to tune.
FOLLOW_UP_OVERDUE_DAYS = 30      # no completed session in this long
PRE_ASSESSMENT_LAG_DAYS = 14     # intake without a completed pre-assessment
REPORT_LAG_DAYS = 14             # completed pre-assessment without a report
ASSIGNMENT_ANSWER_DAYS = 7       # a psychologist asked and silent this long

SEVERITY_RANK = {"danger": 0, "warning": 1, "info": 2}


def _alert(child, gap_type, message, severity="warning"):
    return {
        "type": gap_type, "severity": severity,
        "child_id": child.id, "child_name": child.fullname,
        "message": message,
    }


def _unreviewed_flags(ids):
    """Children with an unacknowledged self-report flag. Reads persisted rows —
    no text analysis happens here, because this runs on every page load."""
    return dict(SelfReportFlag.objects
                .filter(child_id__in=ids, reviewed_at__isnull=True)
                .values_list("child_id")
                .annotate(n=Count("id")))


def _self_report_gap(child, waiting):
    # The child said something worth reading. This asserts nothing
    # about the case notes — they are never read — only that her own
    # words are waiting. The message deliberately does not quote her:
    # her words belong on her page beside the notes, not in a caseload
    # list that gets skimmed.
    return _alert(child, "self_report_concern",
                  f"{waiting} self-report answer{'s' if waiting > 1 else ''} "
                  f"awaiting review.", "danger")


def _booking_state(ids, now):
    """(start of each child's latest completed session, children with a session
    still to come) — what the two booking rules read."""
    last_completed_appt = {}
    has_upcoming = set()
    for a in Appointment.objects.filter(child_id__in=ids).order_by("start"):
        if a.status == Appointment.COMPLETED:
            last_completed_appt[a.child_id] = a.start
        if a.status == Appointment.SCHEDULED and a.start >= now:
            has_upcoming.add(a.child_id)
    return last_completed_appt, has_upcoming


def _booking_gaps(child, last_completed_appt, has_upcoming, now):
    """The two booking rules, shared by both rule sets: a psychologist sees them
    for their children and a social worker, whose job the booking is, for theirs."""
    gaps = []
    # Overdue follow-up: last completed session too long ago.
    last = last_completed_appt.get(child.id)
    if last and (now - last).days > FOLLOW_UP_OVERDUE_DAYS and child.id not in has_upcoming:
        gaps.append(_alert(child, "follow_up_overdue",
                           f"Last completed session over {FOLLOW_UP_OVERDUE_DAYS} days ago."))
    # Active child with no upcoming appointment at all.
    if child.id not in has_upcoming:
        gaps.append(_alert(child, "no_upcoming_appointment",
                           "Active case with no upcoming appointment.", "info"))
    return gaps


def _sorted(alerts):
    alerts.sort(key=lambda a: (SEVERITY_RANK.get(a["severity"], 3), a["child_name"]))
    return alerts


def compute_alerts(children_qs):
    """Compute care-gap alerts for the given (already role-scoped) children."""
    today = timezone.localdate()
    now = timezone.now()
    children = list(children_qs.filter(status=Child.ACTIVE)
                    .prefetch_related("pre_assessments"))
    ids = [c.id for c in children]

    last_completed_appt, has_upcoming = _booking_state(ids, now)

    signed_consent = set(ConsentRecord.objects.filter(
        child_id__in=ids, status=ConsentRecord.SIGNED).values_list("child_id", flat=True))
    has_report = set(PsychologicalReport.objects.filter(
        child_id__in=ids).values_list("child_id", flat=True))

    flagged = _unreviewed_flags(ids)

    alerts = []

    def add(child, gap_type, message, severity="warning"):
        alerts.append(_alert(child, gap_type, message, severity))

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

        # 4 and 5. Overdue follow-up, and no upcoming appointment at all.
        alerts.extend(_booking_gaps(c, last_completed_appt, has_upcoming, now))

        # 6. The child said something worth reading.
        waiting = flagged.get(c.id)
        if waiting:
            alerts.append(_self_report_gap(c, waiting))

    return _sorted(alerts)


def compute_staff_alerts(children_qs):
    """A social worker's gaps, over their own (already scoped) records.

    These are what stops a case moving that is theirs to close. They cannot
    start a pre-assessment or upload a psychological report, so those two
    rules are not here; booking is their job, so the booking rules are.
    docs/superpowers/specs/2026-09-27-assistant-role-access-design.md, next
    step 2.
    """
    now = timezone.now()
    children = list(children_qs.filter(status=Child.ACTIVE))
    ids = [c.id for c in children]

    has_referral = set(CaseReferral.objects.filter(
        child_id__in=ids).values_list("child_id", flat=True))
    signed_consent = set(ConsentRecord.objects.filter(
        child_id__in=ids, status=ConsentRecord.SIGNED).values_list("child_id", flat=True))
    assessed = set(PreAssessment.objects.filter(
        child_id__in=ids, status=PreAssessment.COMPLETED).values_list("child_id", flat=True))
    asked_at = dict(AssignmentRequest.objects.filter(
        child_id__in=ids, status=AssignmentRequest.PENDING
    ).values_list("child_id", "created_at"))
    last_completed_appt, has_upcoming = _booking_state(ids, now)
    flagged = _unreviewed_flags(ids)

    alerts = []
    for c in children:
        if c.id not in has_referral:
            alerts.append(_alert(
                c, "no_case_referral",
                "No case referral on file, so no session can be booked.", "danger"))

        # A pending request is the social worker's job done, and the Records
        # row already says "Awaiting X". It becomes a gap again only when it
        # has gone unanswered for a week, because a psychologist who never
        # answers is the one way a child can sit unassigned for good (a
        # decline needs a reason and reappears here at once).
        if c.assigned_psychologist_id is None:
            asked = asked_at.get(c.id)
            if asked is None:
                alerts.append(_alert(
                    c, "no_psychologist",
                    "No psychologist yet, and nobody is being asked."))
            elif (now - asked).days >= ASSIGNMENT_ANSWER_DAYS:
                alerts.append(_alert(
                    c, "no_psychologist",
                    f"No psychologist yet. One was asked {ASSIGNMENT_ANSWER_DAYS}+ "
                    f"days ago and has not answered."))

        # A child with a completed pre-assessment is skipped: completing one
        # required a signed consent (PreAssessmentViewSet.complete).
        if c.id not in signed_consent and c.id not in assessed:
            alerts.append(_alert(c, "no_signed_consent", "No signed consent on file."))

        # Booking is a social worker's job, so they keep the two booking rules
        # exactly as the psychologist's list has them.
        alerts.extend(_booking_gaps(c, last_completed_appt, has_upcoming, now))

        # Kept because they are the child's own words, not a psychologist's
        # process. A social worker can acknowledge them
        # (SelfReportFlagViewSet.acknowledge), and they are danger severity.
        waiting = flagged.get(c.id)
        if waiting:
            alerts.append(_self_report_gap(c, waiting))

    return _sorted(alerts)


def alerts_for(request, children_qs):
    """The care gaps for whoever is asking, over children already scoped to them.

    The Dashboard's card and the assistant's list_care_gaps both call this, so
    they name the same children. A psychologist keeps the clinical rules and the
    ISA keeps them agency-wide; a user with no role is scoped to nothing
    upstream and gets [].
    """
    if role_of(request) == Role.STAFF:
        return compute_staff_alerts(children_qs)
    return compute_alerts(children_qs)
