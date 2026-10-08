"""The facts shown above a pre-session brief.

They come from plain queries, never from the model, so they show when the
assistant is off, hosted or slow, and give the reader something to check the
prose against. They are NOT fed into the prompt: that is item 5 of
docs/superpowers/specs/2026-09-27-assistant-role-access-design.md and needs
its own `ai_eval --feature brief` run.

Each fact is counted the way the screen that already shows it counts it -
never more than the screen, and never a different number from it.

Two kinds, by who is reading (owner's decision, 8 Oct 2026). A psychologist's
is "clinical" and sits above the written brief. A social worker's and the
ISA's is "case": the same facts plus where the case stands on the paperwork -
referral, psychologist, consent, custodian texts, survey - and NO written
brief, because the model reads a child's notes and neither of them has a case
reason to ask for that. The written brief is the psychologist's only
(assistant/views.py).
"""
from django.utils import timezone

from accounts.display import display_name
from accounts.models import Role
from accounts.scoping import (hide_earlier_history, role_of, scope_to_visible,
                              visible_children)
from children import custodian, intake
from children.models import AssignmentRequest
from clinical.care_gaps import alerts_for
from clinical.models import (CaseReferral, ConsentRecord, OpinionnaireInvite,
                             ProblemEntry, SelfReportFlag, TreatmentPlan)
from scheduling.models import Appointment

CLINICAL, CASE = "clinical", "case"

# The care-gap lists' self-report line is the same count as `unreviewed_self_reports`;
# showing it twice is noise.
_SAID_ELSEWHERE = {"self_report_concern"}

# A case brief says these in a row of its own (referral, psychologist, consent),
# so the same gap listed again below would be the same sentence twice. The
# strings are care_gaps.compute_staff_alerts' own.
_SAID_IN_CASE_ROWS = {"no_case_referral", "no_psychologist", "no_signed_consent"}


def brief_kind(role):
    """Which brief this role gets: the psychologist's clinical one, or the
    case brief of facts alone. Anyone who is not a psychologist gets the
    latter, a role this does not know included."""
    return CLINICAL if role == Role.PSYCHOLOGIST else CASE


def _when(dt):
    # DATA, not prose: the screen writes it on the 12-hour clock with
    # exactDate()/clock(), so no server-formatted time is made here.
    return timezone.localtime(dt).isoformat()


def _case_referral(request, child):
    """How many referrals are on file, the latest one's date, and its summary
    only once a person has confirmed it - an unconfirmed draft is the model's
    wording and has not been read by anyone (the serializers keep it from
    readers who may not confirm it, and so does this)."""
    referrals = scope_to_visible(CaseReferral.objects.filter(child=child), request)
    latest = referrals.order_by("-created_at", "-id").first()
    if latest is None:
        return None
    confirmed = bool(latest.ai_summary_confirmed and latest.ai_summary)
    return {
        "count": referrals.count(),
        "latest_uploaded_on": timezone.localtime(latest.created_at).date().isoformat(),
        "summary": latest.ai_summary if confirmed else None,
        "summary_confirmed": confirmed,
    }


def _psychologist(request, child):
    """Who the child is with, or who has been asked, or who said no and why.
    The same names the child's page shows (display_name), and the same rule for
    a decline: it counts only until somebody holds the child or is asked."""
    if child.assigned_psychologist_id:
        return {"state": "assigned", "name": display_name(child.assigned_psychologist)}
    requests = scope_to_visible(
        AssignmentRequest.objects.filter(child=child).select_related("psychologist"),
        request)
    pending = requests.filter(status=AssignmentRequest.PENDING).first()
    if pending is not None:
        return {"state": "asked", "name": display_name(pending.psychologist),
                "days_ago": (timezone.localdate()
                             - timezone.localtime(pending.created_at).date()).days}
    latest = requests.first()
    if latest is not None and latest.status == AssignmentRequest.DECLINED:
        return {"state": "declined", "name": display_name(latest.psychologist),
                "reason": latest.reason}
    return {"state": "none"}


def _consent(request, child):
    latest = scope_to_visible(ConsentRecord.objects.filter(child=child), request).first()
    if latest is None:
        return None
    return {"status": latest.status, "date": latest.date.isoformat()}


def _custodian_texts(child):
    """Why the custodian's texts are on or off - the sentence and nothing else.
    Not the custodian's name or number: those are the social worker's record,
    and a fact line has no need of them. None where the case type asks for no
    custodian at all."""
    if "custodian_name" not in intake.CASE_TYPE_FIELDS.get(child.case_type, []):
        return None
    return custodian.status_of(child)


def _survey(request, child):
    """The newest survey link: answered, still out, or lapsed. Only its state
    and a date - never an answer."""
    invite = (scope_to_visible(OpinionnaireInvite.objects.filter(child=child), request)
              .order_by("-created_at", "-id").first())
    if invite is None:
        return None
    if invite.status == OpinionnaireInvite.SUBMITTED:
        return {"state": "answered",
                "date": timezone.localtime(invite.submitted_at or invite.created_at)
                .date().isoformat()}
    lapsed = (invite.status == OpinionnaireInvite.EXPIRED
              or timezone.now() >= invite.expires_at)
    return {"state": "expired" if lapsed else "sent",
            "date": timezone.localtime(invite.created_at).date().isoformat()}


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
    # The Dashboard's alerts for this one child, by the reader's role (alerts_for):
    # a social worker's brief lists their rules, not the psychologist's. All
    # pre-assessments on purpose (CLAUDE.md).
    gaps = alerts_for(request, visible_children(request).filter(pk=child.pk))
    kind = brief_kind(role_of(request))
    said = _SAID_ELSEWHERE | (_SAID_IN_CASE_ROWS if kind == CASE else set())
    facts = {
        "kind": kind,
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
                      for a in gaps if a["type"] not in said],
    }
    if kind == CASE:
        facts.update({
            "case_referral": _case_referral(request, child),
            "psychologist": _psychologist(request, child),
            "consent": _consent(request, child),
            "custodian_texts": _custodian_texts(child),
            "survey": _survey(request, child),
        })
    return facts
