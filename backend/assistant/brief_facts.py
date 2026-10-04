"""The facts shown above a pre-session brief.

They come from plain queries, never from the model, so they show when the
assistant is off, hosted or slow, and give the reader something to check the
prose against. They are NOT fed into the prompt: that is item 5 of
docs/superpowers/specs/2026-09-27-assistant-role-access-design.md and needs
its own `ai_eval --feature brief` run.

Each fact is counted the way the screen that already shows it counts it -
never more than the screen, and never a different number from it.
"""
from django.utils import timezone

from accounts.scoping import hide_earlier_history, scope_to_visible, visible_children
from clinical.care_gaps import compute_alerts
from clinical.models import ProblemEntry, SelfReportFlag, TreatmentPlan
from scheduling.models import Appointment

# compute_alerts' self-report line is the same count as `unreviewed_self_reports`;
# showing it twice is noise.
_SAID_ELSEWHERE = {"self_report_concern"}


def _when(dt):
    # DATA, not prose: the screen writes it on the 12-hour clock with
    # exactDate()/clock(), so no server-formatted time is made here.
    return timezone.localtime(dt).isoformat()


def brief_facts(request, child):
    """The facts for one child, as the requester may see them.

    `child` MUST have been fetched through visible_children(request); every
    queryset below is still built on scope_to_visible so a misuse finds
    nothing.
    """
    now = timezone.now()
    appts = scope_to_visible(Appointment.objects.filter(child=child), request)
    # Monitoring's next session: the child's, whoever the psychologist. No name is
    # carried, so a psychologist learns only the time Monitoring already shows them.
    nxt = (appts.filter(status=Appointment.SCHEDULED, start__gte=now)
           .order_by("start", "id").first())
    # care_gaps' follow-up rule: the latest COMPLETED appointment. A no-show is not
    # a session held, and one dated in the future cannot have been.
    last = (appts.filter(status=Appointment.COMPLETED, start__lte=now)
            .order_by("-start", "-id").first())
    # Problems are the case, not an opinion: no carry-history (ProblemEntryViewSet
    # hides_history=False, and the child's page shows them whoever logged them).
    problems = scope_to_visible(
        ProblemEntry.objects.filter(child=child, resolved=False), request)
    # A treatment plan IS an opinion record: carry-history applies, after scope.
    plan = (hide_earlier_history(
                scope_to_visible(TreatmentPlan.objects.filter(
                    child=child, status=TreatmentPlan.ACTIVE), request),
                request, "author")
            .order_by("-created_at", "-id").first())
    # The child's own words: exempt from carry-history. COUNTED, never quoted.
    waiting = scope_to_visible(SelfReportFlag.objects.filter(
        child=child, reviewed_at__isnull=True), request).count()
    # The Dashboard's alerts for this one child. All pre-assessments on purpose (CLAUDE.md).
    gaps = compute_alerts(visible_children(request).filter(pk=child.pk))
    return {
        "next_session": ({"start": _when(nxt.start), "purpose": nxt.get_purpose_display()}
                         if nxt else None),
        "last_session": ({"start": _when(last.start), "purpose": last.get_purpose_display(),
                          "days_ago": (timezone.localdate()
                                       - timezone.localtime(last.start).date()).days}
                         if last else None),
        "open_problems": [{"description": p.description, "category": p.category or None,
                           "identified_on": p.identified_on.isoformat()} for p in problems],
        "treatment_plan": ({"objectives": plan.objectives,
                            "review_date": plan.review_date.isoformat() if plan.review_date else None}
                           if plan else None),
        "unreviewed_self_reports": waiting,
        "care_gaps": [{"type": a["type"], "severity": a["severity"], "message": a["message"]}
                      for a in gaps if a["type"] not in _SAID_ELSEWHERE],
    }
