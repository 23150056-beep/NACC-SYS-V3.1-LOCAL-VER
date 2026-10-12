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

A social worker's case brief also says what to do next (`next_steps`, 12 Oct
2026), worked out below by plain rules from these same facts. It replaced a
written brief drafted by the model, which was measured and invented dates and
relationships; nothing here reaches a model.
"""
from django.http import Http404
from django.utils import timezone

from accounts.display import display_name
from accounts.models import Role
from accounts.scoping import (hide_earlier_history, role_of, scope_to_visible,
                              visible_children)
from case_study.access import access_for
from case_study.completeness import missing_sections
from case_study.models import CaseStudy
from children import custodian, intake
from children.models import AssignmentRequest, Child
from clinical.care_gaps import (ASSIGNMENT_ANSWER_DAYS, SURVEY_UNANSWERED_DAYS,
                                alerts_for)
from clinical.models import (CaseReferral, ConsentRecord, OpinionnaireInvite,
                             ProblemEntry, SelfReportFlag, TreatmentPlan)
from scheduling.models import Appointment

CLINICAL, CASE = "clinical", "case"

# The care-gap lists' self-report line is the same count as `unreviewed_self_reports`;
# showing it twice is noise.
_SAID_ELSEWHERE = {"self_report_concern"}

# A case brief says these in a row of its own, so the same gap listed again
# below would be the same sentence twice. Each row is keyed to EVERY gap type
# that says what it says, from both rule sets in clinical/care_gaps.py: the
# social worker's (compute_staff_alerts) and the clinical set the ISA reads
# (compute_alerts), which words the consent gap differently. Missing one left
# the ISA reading "without a signed consent" under a Consent row.
_CASE_ROW_GAPS = {
    "case_referral": {"no_case_referral"},
    "psychologist": {"no_psychologist"},
    "consent": {"no_signed_consent", "consent_missing"},
    "survey": {"survey_unanswered"},
}
_SAID_IN_CASE_ROWS = set().union(*_CASE_ROW_GAPS.values())

# A social worker's "What to do next" says these in a step of its own, so the
# same gap listed again below would be the same sentence twice. Each step is
# keyed to EVERY gap type that says what it says (compute_staff_alerts); a step
# that says no gap maps to an empty set, and a test fails on a step missing here.
# Applied to a social worker's list ONLY: the ISA gets no steps and keeps
# these gaps (the booking one included), as the psychologist does.
_NEXT_STEP_GAPS = {
    "case_referral": {"no_case_referral"},
    "psychologist_none": {"no_psychologist"},
    "psychologist_silent": {"no_psychologist"},
    "psychologist_declined": {"no_psychologist"},
    "consent": {"no_signed_consent"},
    "book_session": {"no_upcoming_appointment"},
    "custodian_texts": set(),
    "survey_unanswered": {"survey_unanswered"},
    "self_reports": {"self_report_concern"},
    "case_study_start": set(),
    "case_study_complete": set(),
}
_SAID_IN_NEXT_STEPS = set().union(*_NEXT_STEP_GAPS.values())


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
                             - timezone.localtime(pending.created_at).date()).days,
                # Whole 24-hour days since it was asked, which is how the care
                # gap counts it (care_gaps.compute_staff_alerts). The next step
                # fires on this, so the two cannot disagree about a request
                # asked at 11 PM that is not yet seven days old.
                "elapsed_days": (timezone.now() - pending.created_at).days}
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
            "date": timezone.localtime(invite.created_at).date().isoformat(),
            # Whole 24-hour days since the link went out: the care gap's count,
            # which the next step fires on (see _psychologist).
            "elapsed_days": (timezone.now() - invite.created_at).days}


def _is_the_social_worker(request, child):
    """The signed-in Staff account that holds this record. The ISA, a
    psychologist and another social worker are not."""
    return (role_of(request) == Role.STAFF
            and child.social_worker_id is not None
            and child.social_worker_id == request.user.pk)


def _case_study(request, child):
    """Where the case study stands, never what it says: {"state": "none"}, or
    its state and how many applicable sections are still empty. Read through
    the case study's own access and completeness rules, so whoever may not
    read it is told nothing (None) and "missing" is the number every other
    screen shows."""
    try:
        access = access_for(request, child)
    except Http404:
        return None
    if not access.can_write:
        return None
    study = CaseStudy.objects.filter(child=child).first()
    if study is None:
        return {"state": "none"}
    return {"state": study.status, "missing": len(missing_sections(study))}


# --- what to do next (a social worker's) ------------------------------------
#
# Plain rules over the facts above - no model, no database. An ordered list of
# {"key", "text", "link"}: one plain sentence each, and the frontend path where
# the thing is done (null where a social worker cannot do it). A step is a TASK:
# a psychologist who was asked yesterday is waiting, not a task, so there is
# none. Counted nothing the facts did not already carry, and worded without a
# custodian's name or number, a case note, a child's own words or a line of the
# case study - the facts carry none of them.

DECLINE_REASON_CHARS = 200


def _one_line(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def next_steps(facts, child):
    """What the record's social worker has left to do for this child, in the
    order to do it. `facts` is brief_facts() of kind "case" with the social
    worker's `case_study` row; `child` is only asked for its id and whether the
    case is still open (a closed case has nothing to do: the care gaps skip it
    too).

    Pure: the same facts give the same steps.
    """
    if child.status != Child.ACTIVE:
        return []
    cid = child.id
    steps = []

    def add(key, text, link=None):
        steps.append({"key": key, "text": text, "link": link})

    referral = facts.get("case_referral")
    psychologist = facts.get("psychologist") or {}
    state = psychologist.get("state")
    who = psychologist.get("name") or "the psychologist"
    edit = f"/children?edit={cid}"

    if referral is None:
        add("case_referral",
            "File the case referral. Sessions cannot be booked without it.",
            f"/reports?upload=1&child={cid}")

    if state == "none":
        add("psychologist_none", "Ask a psychologist to take the case.", edit)
    elif state == "asked" and psychologist.get("elapsed_days", 0) >= ASSIGNMENT_ANSWER_DAYS:
        add("psychologist_silent",
            f"No answer from {who} after {psychologist['days_ago']} days. "
            "Ask them, or ask someone else.", edit)
    elif state == "declined":
        reason = _one_line(psychologist.get("reason"), DECLINE_REASON_CHARS)
        said = f"{who} declined: {reason}" if reason else f"{who} declined"
        # One full stop, whether or not the reason brought its own.
        stop = "" if said[-1] in ".!?" else "."
        add("psychologist_declined", f"{said}{stop} Ask another psychologist.", edit)

    consent = facts.get("consent")
    if consent is None or consent.get("status") != "signed":
        # Recording a consent is the psychologist's (or the ISA's) to do: a
        # social worker has no screen for it, so there is no link.
        add("consent", "Ask the psychologist to record the signed consent.")

    if (facts.get("next_session") is None and state == "assigned"
            and referral is not None):
        add("book_session", "Book the next session.",
            f"/schedule?book=1&child={cid}")

    texts = facts.get("custodian_texts")
    if texts and texts != custodian.TEXTS_ON:
        reason = texts.removesuffix(custodian.TEXTS_OFF_ENDING)
        add("custodian_texts",
            f"Texts to the custodian are off: {reason[:1].lower()}{reason[1:]}. "
            "Fix it on the record.", edit)

    survey = facts.get("survey") or {}
    if (survey.get("state") in ("sent", "expired")
            and survey.get("elapsed_days", 0) >= SURVEY_UNANSWERED_DAYS):
        add("survey_unanswered",
            f"The survey sent {_plural(survey['elapsed_days'], 'day', 'days')} ago has "
            "no answer yet. Follow it up or send a new link.",
            f"/report/child/{cid}?tab=voice")

    waiting = facts.get("unreviewed_self_reports") or 0
    if waiting > 0:
        # Overview, not the survey tab: the flagged answers and "Mark as read"
        # are on the Overview tab.
        add("self_reports",
            f"Read the {_plural(waiting, 'survey answer', 'survey answers')} "
            "waiting for review.", f"/report/child/{cid}?tab=overview")

    study = facts.get("case_study")
    if study is not None:
        if study["state"] == "none":
            add("case_study_start", "Start the case study.",
                f"/report/child/{cid}?tab=casestudy")
        elif study["state"] == "draft" and study.get("missing", 0) > 0:
            add("case_study_complete",
                f"Case study: {_plural(study['missing'], 'section', 'sections')} "
                "still to complete.", f"/report/child/{cid}?tab=casestudy")

    return steps


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
    # Only the record's own social worker is told what to do next: it is their
    # task list, and neither the ISA nor a psychologist can do these things.
    own_sw = _is_the_social_worker(request, child)
    said = _SAID_ELSEWHERE | (_SAID_IN_CASE_ROWS if kind == CASE else set())
    if own_sw:
        said |= _SAID_IN_NEXT_STEPS
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
    if own_sw:
        facts["case_study"] = _case_study(request, child)
        facts["next_steps"] = next_steps(facts, child)
    return facts
