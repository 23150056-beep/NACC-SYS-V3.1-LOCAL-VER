import logging
import threading
import time
from datetime import timedelta

from django.db import connection
from django.db.models import Avg, Count, Max, Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Role
from accounts.scoping import (role_of as _role, role_of_user as _role_of,
                              visible_children)
from accounts.permissions import (IsAdministrator, IsAdminOrStaff, is_admin_or_assignee,
                                  writes_case_referrals)
from assistant import evaluation, prompts, tools
from assistant.brief_facts import brief_facts, brief_kind
from assistant.models import AssistantJob, AssistantSetting
from assistant.serializers import AssistantSettingSerializer
from assistant.services import (AIUnavailable, CASE_BRIEF_DISCLAIMER, DISCLAIMER,
                                HOSTED_DRAFTING_REFUSED, OpenAICompatibleClient,
                                drafting_available, gate, get_ai_client, run_job,
                                services_lock)
from children.models import Child
from clinical.models import CaseReferral, OpinionnaireInvite, PsychologicalReport
from clinical.services import ensure_text
from scheduling.models import Appointment

logger = logging.getLogger(__name__)




def _brief_only_author(child, user, role):
    """Author filter for build_brief_prompt(): the carry-history control
    (Child.assignee_sees_history) is enforced here so a brief can never read
    a previous psychologist's remarks once that flag hides them elsewhere."""
    if role == Role.PSYCHOLOGIST and not child.assignee_sees_history:
        return user
    return None


# What a social worker or the ISA is told when they reach for the written brief.
# The model reads a child's case notes to write it, and neither has a case reason
# to ask for that (owner's decision, 8 Oct 2026); what they get is the case facts
# beside it, from plain queries.
WRITTEN_BRIEF_REFUSED = ("The written brief is for the child's psychologist. "
                         "The case facts are on the brief panel.")


def _written_brief_refused(request):
    """The 403 for anyone but a psychologist, or None.

    Asked FIRST in every door to the written brief, before gate() and before
    the child is looked up: nothing is sent to the model and no AssistantJob is
    written for a read that was refused.
    """
    if _role(request) == Role.PSYCHOLOGIST:
        return None
    return Response({"detail": WRITTEN_BRIEF_REFUSED}, status=status.HTTP_403_FORBIDDEN)


class AssistantBaseView(generics.GenericAPIView):
    """Turns AIUnavailable into a 503 for every assistant endpoint.

    503 rather than 500: the assistant being off, or the runtime being
    unreachable, is a normal state of this system, not a fault.
    """
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, AIUnavailable):
            return Response({"detail": str(exc)},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return super().handle_exception(exc)


class AssistantSettingView(AssistantBaseView):
    """Read/update the singleton. Administrator only — this switch decides
    whether case text reaches a model at all."""
    permission_classes = [IsAdministrator]
    serializer_class = AssistantSettingSerializer

    def get(self, request):
        return Response(AssistantSettingSerializer(AssistantSetting.load()).data)

    def put(self, request):
        serializer = AssistantSettingSerializer(
            AssistantSetting.load(), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class RemarkPolishView(AssistantBaseView):
    """Polish a remark the psychologist is writing. Returns a draft only —
    nothing is saved to the remark until the human saves it themselves."""
    throttle_scope = "assistant_draft"

    def post(self, request):
        gate()
        raw = request.data.get("text")
        if not isinstance(raw, str) or not raw.strip():
            return Response({"detail": "Nothing to polish."},
                            status=status.HTTP_400_BAD_REQUEST)
        raw = raw.strip()
        draft, job = run_job(
            "remark_polish",
            prompts.build_remark_prompt(raw),
            system=prompts.REMARK_POLISH_SYSTEM,
            input_ref="remark:draft",
            user=request.user)

        # Measured 4/4 against Taglish case notes: asked to rewrite in clear
        # professional English, the model returned Tagalog instead — once
        # garbled enough to lose the original meaning entirely. A draft that
        # drifts is worse than no draft, so it is rejected rather than shown.
        #
        # Only polish is guarded this way. A brief legitimately quotes remarks
        # that are themselves Taglish, so the same check there would reject
        # correct output.
        drift = evaluation.language_drift(draft)
        if drift:
            job.ok = False
            job.error = f"rejected: language drift ({', '.join(drift[:4])})"[:255]
            job.save(update_fields=["ok", "error"])
            return Response(
                {"detail": "The draft came back in Tagalog rather than English, "
                           "so it was not shown. Write the note in your own "
                           "words, or try again in English."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        return Response({"draft": draft, "job_id": job.id,
                         "disclaimer": DISCLAIMER})


class AssistantJobFeedbackView(AssistantBaseView):
    """Record what the human did with a draft. Deliberately does NOT gate on
    the feature flags: it writes history, and history must stay recordable
    after an administrator switches the assistant off."""

    def post(self, request, job_id):
        outcome = request.data.get("outcome")
        valid = {AssistantJob.ACCEPTED, AssistantJob.EDITED, AssistantJob.DISCARDED}
        if outcome not in valid:
            return Response({"detail": f"outcome must be one of {sorted(valid)}."},
                            status=status.HTTP_400_BAD_REQUEST)
        qs = AssistantJob.objects.all()
        if _role(request) != Role.ADMINISTRATOR:
            qs = qs.filter(created_by=request.user)
        try:
            job = qs.get(pk=job_id)
        except AssistantJob.DoesNotExist:
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
        job.outcome = outcome
        job.save(update_fields=["outcome"])
        return Response({"outcome": job.outcome})


class PreSessionBriefView(AssistantBaseView):
    """Generate a brief now. This is the ~40s path — the UI reaches for
    LatestBriefView first and only falls back to here."""
    throttle_scope = "assistant_draft"

    def post(self, request, child_id):
        refused = _written_brief_refused(request)
        if refused:
            return refused
        gate()
        try:
            child = visible_children(request).get(pk=child_id)
        except Child.DoesNotExist:
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
        only_author = _brief_only_author(child, request.user, _role(request))
        draft, job = run_job(
            "brief",
            prompts.build_brief_prompt(child, only_author=only_author),
            system=prompts.BRIEF_SYSTEM,
            input_ref=f"child:{child.id}",
            user=request.user, child=child)
        return Response({"draft": draft, "job_id": job.id,
                         "generated_at": job.created_at,
                         "disclaimer": DISCLAIMER})


def _todays_briefs(user):
    """Briefs drafted today FOR this user - never anyone else's.

    A brief is written from what its requester may see: the carry-history
    control (_brief_only_author) decides which remarks go in. Keyed by child
    alone, the cache handed the first brief of the day to whoever opened the
    child next, so an administrator's full-history brief reached a newly
    assigned psychologist the control was hiding that history from - and so
    did the previous psychologist's own, the same morning as a reassignment.
    """
    return AssistantJob.objects.filter(
        job_type="brief", ok=True, created_by=user,
        created_at__date=timezone.localdate())


def _current_brief(user, child):
    """Today's brief of this child for this user, if it can still be served.

    Per user is not enough on its own. A brief drafted while the child's
    history was carried quotes the previous psychologist; once the ISA hides
    that history, the same psychologist's brief of that morning still did. So
    where history is hidden from this user, a brief older than the child
    record's last change is not served - it may have been drafted under the
    other setting - and is drafted again from what the user may now see.
    Found in review of the first version.
    """
    briefs = _todays_briefs(user).filter(input_ref=f"child:{child.id}")
    if _brief_only_author(child, user, _role_of(user)) is not None:
        briefs = briefs.filter(created_at__gte=child.updated_at)
    return briefs.first()


class LatestBriefView(AssistantBaseView):
    """Today's already-generated brief, served instantly.

    Reads history only, so it deliberately does NOT gate: a brief drafted this
    morning stays readable after an administrator switches the assistant off.
    """

    def get(self, request, child_id):
        refused = _written_brief_refused(request)
        if refused:
            return refused
        try:
            child = visible_children(request).get(pk=child_id)
        except Child.DoesNotExist:
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
        job = _current_brief(request.user, child)
        if not job:
            return Response({"detail": "No brief drafted today."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({"draft": job.output_text, "job_id": job.id,
                         "generated_at": job.created_at,
                         "disclaimer": DISCLAIMER})


class BriefFactsView(AssistantBaseView):
    """The facts shown above a brief, from plain queries (assistant/brief_facts.py).

    Their own endpoint rather than part of the brief's response: the brief
    answers 503 when the assistant is off or the model is hosted, 404 when
    nothing was drafted today, is throttled, and takes up to a minute -
    and these must show in all of those cases, at once. Nor are they stored
    with the brief: LatestBriefView serves this morning's prose, and a
    session booked or a self-report read since then must not be stale.

    Not gated, not throttled, and writes no AssistantJob: no model is
    involved, so there is nothing to switch off or audit.
    """

    def get(self, request, child_id):
        try:
            child = visible_children(request).get(pk=child_id)
        except Child.DoesNotExist:
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(brief_facts(request, child))


# --- the social worker's written case brief --------------------------------
#
# Owner's decision, 8 Oct 2026: a social worker gets the case facts AND a short
# written part drafted from them; the ISA (IT support) gets the facts and never
# the written part, and the psychologist keeps their own brief above. Drafted
# only where drafting is (the local copy with the model runtime), measured by
# `manage.py ai_eval --feature case_brief`, and behind the one assistant switch
# like everything else - no switch of its own.

# What anyone but a social worker is told at either door. One sentence: the
# facts are on the brief panel for the ISA and the psychologist has their own
# brief, so there is nothing else to point them at.
CASE_BRIEF_REFUSED = "The written case brief is for the child's social worker."


def _case_brief_refused(request):
    """The 403 for anyone but a social worker, or None.

    Asked FIRST at both doors, before the child is looked up and before gate():
    nothing is sent to the model and no AssistantJob is written for a read that
    was refused.
    """
    if _role(request) == Role.STAFF:
        return None
    return Response({"detail": CASE_BRIEF_REFUSED}, status=status.HTTP_403_FORBIDDEN)


def _case_brief_prompt(request, child):
    """(prompt, its SHA-256) as the facts stand now.

    The facts are the very dict the panel above the draft is made from, so what
    the model was given and what the worker can check it against cannot differ.
    """
    prompt = prompts.build_case_brief_prompt(brief_facts(request, child), child)
    return prompt, prompts.prompt_sha(prompts.CASE_BRIEF_SYSTEM, prompt)


class CaseBriefView(AssistantBaseView):
    """Draft today's written case brief for a social worker's own child.

    The ~20-60s path on the agency machine: the screen asks LatestCaseBriefView
    first and only comes here when there is no current draft.
    """
    throttle_scope = "assistant_draft"

    def post(self, request, child_id):
        refused = _case_brief_refused(request)
        if refused:
            return refused
        # Staff are narrowed to their own records by visible_children: another
        # social worker's child is a 404, as everywhere else.
        child = visible_children(request).filter(pk=child_id).first()
        if child is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        gate()
        if not drafting_available():
            # Refused before the prompt is built or a job is written, as the
            # prefetch is: run_job would audit a read that never happened.
            raise AIUnavailable(HOSTED_DRAFTING_REFUSED)
        prompt, sha = _case_brief_prompt(request, child)
        draft, job = run_job(
            "case_brief", prompt, system=prompts.CASE_BRIEF_SYSTEM,
            input_ref=f"child:{child.id}", user=request.user, child=child,
            prompt_sha=sha)
        if not draft.strip():
            # An empty "written brief" under a disclaimer reads as broken, and
            # must not be served again as today's.
            job.ok = False
            job.error = "the model returned nothing"
            job.save(update_fields=["ok", "error"])
            return Response({"detail": "The assistant did not write a brief. "
                                       "Try again."},
                            status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        return Response({"draft": draft, "job_id": job.id,
                         "generated_at": job.created_at,
                         "disclaimer": CASE_BRIEF_DISCLAIMER})


class LatestCaseBriefView(AssistantBaseView):
    """Today's case brief by THIS user for THIS child, if it is still true.

    Served only while the prompt built from the facts NOW hashes the same as
    the one it was drafted from (`AssistantJob.prompt_sha`). A session booked,
    a consent recorded or a referral summary confirmed since changes the facts,
    so the draft answers 404 "No current brief" and the worker drafts it again,
    rather than reading yesterday's account of a case that has moved on.

    Reads history only, so, like LatestBriefView, it does NOT gate: a brief
    drafted this morning stays readable after the assistant is switched off.
    """

    def get(self, request, child_id):
        refused = _case_brief_refused(request)
        if refused:
            return refused
        child = visible_children(request).filter(pk=child_id).first()
        if child is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        _, sha = _case_brief_prompt(request, child)
        job = (AssistantJob.objects
               .filter(job_type="case_brief", ok=True, created_by=request.user,
                       child=child, prompt_sha=sha,
                       created_at__date=timezone.localdate())
               .order_by("-created_at", "-id").first())
        if job is None:
            return Response({"detail": "No current brief."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({"draft": job.output_text, "job_id": job.id,
                         "generated_at": job.created_at,
                         "disclaimer": CASE_BRIEF_DISCLAIMER})


# (user id, child id) pairs currently being briefed, so two page loads cannot
# queue the same brief twice. Per user, like the briefs themselves. Guarded by
# its own lock; the generation lock lives in services.
_IN_FLIGHT = set()
_IN_FLIGHT_LOCK = threading.Lock()


def _generate_briefs_now(child_ids, user):
    """Generate briefs one at a time. Never raises.

    Sequential on purpose: the runtime is CPU-only and concurrent generations
    make every request slower rather than parallel.
    """
    role = _role_of(user)
    try:
        for child_id in child_ids:
            child = Child.objects.filter(pk=child_id).first()
            if not child:
                continue
            only_author = _brief_only_author(child, user, role)
            try:
                run_job("brief", prompts.build_brief_prompt(child, only_author=only_author),
                        system=prompts.BRIEF_SYSTEM,
                        input_ref=f"child:{child.id}", user=user, child=child)
            except AIUnavailable:
                # Already audited by run_job. A runtime that is down must not
                # abandon the rest of the queue.
                logger.info("Prefetch skipped child %s: runtime unavailable", child_id)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.difference_update((user.pk, cid) for cid in child_ids)


def _start_prefetch_thread(child_ids, user):
    def worker():
        try:
            _generate_briefs_now(child_ids, user)
        finally:
            # A thread owns its own connection and must hand it back.
            connection.close()

    threading.Thread(target=worker, daemon=True).start()


class PrefetchBriefsView(AssistantBaseView):
    """Draft briefs ahead of today's sessions so the button press is instant.

    Returns immediately. The caller ignores the result — this is fire and
    forget, and a failure here must never be visible on the schedule screen.
    """
    throttle_scope = "assistant_draft"

    def post(self, request):
        refused = _written_brief_refused(request)
        if refused:
            return refused
        gate()
        if not drafting_available():
            # Every brief queued below would be refused by get_ai_client() and
            # audited as a failure - a row per child per schedule visit, in a
            # background thread, for nobody. prefetchBriefs() on the schedule
            # screen already swallows the 503.
            raise AIUnavailable(HOSTED_DRAFTING_REFUSED)
        today = timezone.localdate()
        visible = visible_children(request)
        appts = Appointment.objects.filter(
            child__in=visible, status=Appointment.SCHEDULED,
            start__date=today, psychologist=request.user)

        child_ids = list(dict.fromkeys(appts.values_list("child_id", flat=True)))
        # A day's sessions, so a query per child is a handful - and one rule
        # for what counts as done, shared with LatestBriefView.
        already = {child.id for child in visible.filter(pk__in=child_ids)
                   if _current_brief(request.user, child)}

        queued, skipped = [], []
        with _IN_FLIGHT_LOCK:
            for cid in child_ids:
                key = (request.user.pk, cid)
                if cid in already or key in _IN_FLIGHT:
                    skipped.append(cid)
                else:
                    _IN_FLIGHT.add(key)
                    queued.append(cid)

        if queued:
            _start_prefetch_thread(queued, request.user)
        return Response({"queued": queued, "skipped": skipped})


def _writes_reports(request, doc):
    # _ChildScopedClinicalViewSet._assert_can_write: an administrator, or the
    # psychologist the child is assigned to. Staff read reports, never write.
    return is_admin_or_assignee(request, doc.child)


def _writes_referrals(request, doc):
    # CaseReferralViewSet's rule: administrators and social workers. A social
    # worker only ever reaches their own records here, because the document
    # was found through visible_children.
    return writes_case_referrals(request)


# kind -> (model, input_ref prefix, human label for the prompt, author field
#          name, who may write the document, what anyone else is told)
_DOC_KINDS = {
    "report": (PsychologicalReport, "report", "psychological report", "author",
               _writes_reports,
               "Only the child's psychologist or an administrator can summarise "
               "a psychological report."),
    "case-referral": (CaseReferral, "casereferral", "case referral", "uploaded_by",
                      _writes_referrals,
                      "Only a social worker or an administrator can summarise a "
                      "case referral."),
}


def _document_to_summarise(request, kind, doc_id):
    """(document, None) when this caller may put a summary on it, else
    (None, the refusal).

    Summarising and confirming are both WRITES to the document: a draft
    replaces what is in `ai_summary` - the screen warns a confirmed one
    "cannot be recovered" - and a confirmation saves text as the
    psychologist's own. They used to check only that the caller could READ
    the document, so a social worker could replace a psychologist's confirmed
    summary, a psychologist a social worker's, and either could save words of
    their own in its place. One helper for both, so the two cannot differ
    again; confirming had already lost the carry-history check below.
    """
    model, _, _, author_field, may_write, refusal = _DOC_KINDS[kind]
    doc = (model.objects.select_related("child")
           .filter(pk=doc_id, child__in=visible_children(request)).first())
    # Carry-history control: without it, a newly assigned psychologist must
    # not have a document they did not author fed to the model, since the
    # draft it produces would surface facts this screen otherwise hides - nor
    # overwrite the summary of a document the screen does not show them.
    if doc is not None and (
            _role(request) == Role.PSYCHOLOGIST and not doc.child.assignee_sees_history
            and getattr(doc, f"{author_field}_id") != request.user.id):
        doc = None
    if doc is None:
        return None, Response({"detail": "Not found."},
                              status=status.HTTP_404_NOT_FOUND)
    if not may_write(request, doc):
        return None, Response({"detail": refusal}, status=status.HTTP_403_FORBIDDEN)
    return doc, None


class DocumentSummaryView(AssistantBaseView):
    """Draft a summary of an uploaded document into its `ai_summary` column.

    The draft is saved unconfirmed. It only becomes clinical text when a human
    confirms it, at which point it is their words, not a draft.
    """
    throttle_scope = "assistant_draft"
    kind = None

    def post(self, request, doc_id):
        gate()
        _, prefix, label, *_ = _DOC_KINDS[self.kind]
        doc, refused = _document_to_summarise(request, self.kind, doc_id)
        if refused:
            return refused
        if not ensure_text(doc).strip():
            return Response(
                {"detail": "No text could be extracted from this document."},
                status=status.HTTP_400_BAD_REQUEST)

        # A long report is read in part; the draft says which part, so the
        # psychologist confirming it knows what it could not have seen.
        coverage = prompts.fit_document(doc.extracted_text)[1]
        draft, job = run_job(
            "doc_intelligence",
            prompts.build_summary_prompt(doc.extracted_text, label),
            system=prompts.SUMMARY_SYSTEM,
            input_ref=f"{prefix}:{doc.id}",
            user=request.user, child=doc.child)
        doc.ai_summary = draft
        doc.ai_summary_confirmed = False
        doc.save(update_fields=["ai_summary", "ai_summary_confirmed"])
        return Response({"draft": draft, "job_id": job.id, "coverage": coverage,
                         "disclaimer": DISCLAIMER})


class ConfirmSummaryView(AssistantBaseView):
    """Confirm a summary as the human's own words.

    Not gated: confirming is the psychologist's act, and must keep working
    after an administrator switches the assistant off.
    """
    kind = None

    def post(self, request, doc_id):
        prefix = _DOC_KINDS[self.kind][1]
        doc, refused = _document_to_summarise(request, self.kind, doc_id)
        if refused:
            return refused
        text = request.data.get("text")
        if not isinstance(text, str) or not text.strip():
            return Response({"detail": "A confirmed summary cannot be empty."},
                            status=status.HTTP_400_BAD_REQUEST)
        text = text.strip()

        doc.ai_summary = text
        doc.ai_summary_confirmed = True
        doc.save(update_fields=["ai_summary", "ai_summary_confirmed"])

        # Whether the human kept the draft verbatim is the evaluation signal.
        job = AssistantJob.objects.filter(
            job_type="doc_intelligence", input_ref=f"{prefix}:{doc.id}",
            ok=True).first()
        if job:
            job.outcome = (AssistantJob.ACCEPTED
                           if job.output_text.strip() == text
                           else AssistantJob.EDITED)
            job.save(update_fields=["outcome"])

        return Response({"ai_summary": doc.ai_summary,
                         "ai_summary_confirmed": doc.ai_summary_confirmed})


class CensusNarrativeView(AssistantBaseView):
    """Narrate figures the caller already computed.

    The model receives finished numbers and is told to restate them. It never
    counts anything: a wrong caseload figure in an agency report is far worse
    than no narrative at all.
    """
    throttle_scope = "assistant_draft"
    permission_classes = [IsAdminOrStaff]

    def post(self, request):
        gate()
        figures = request.data.get("figures")
        if not isinstance(figures, dict) or not figures:
            return Response({"detail": "figures must be a non-empty object."},
                            status=status.HTTP_400_BAD_REQUEST)
        draft, job = run_job(
            "census_narrative",
            prompts.build_census_prompt(figures),
            system=prompts.CENSUS_SYSTEM,
            input_ref="agency:summary",
            user=request.user)
        return Response({"draft": draft, "job_id": job.id,
                         "disclaimer": DISCLAIMER})


WINDOW_DAYS = 30


class AssistantMetricsView(AssistantBaseView):
    """Per-feature usage over the last 30 days.

    Reads history only, so it is not gated — an administrator deciding whether
    to switch the assistant back on needs exactly this while it is off.
    """
    permission_classes = [IsAdministrator]

    def get(self, request):
        since = timezone.now() - timedelta(days=WINDOW_DAYS)
        rows = []
        for job_type, _label in AssistantJob.TYPE_CHOICES:
            qs = AssistantJob.objects.filter(job_type=job_type, created_at__gte=since)
            # The aggregate alias can't be named "ok" — that collides with the
            # model's own `ok` field and Django raises "'ok' is an aggregate"
            # while resolving the sibling Count(filter=Q(ok=...)) calls below.
            agg = qs.aggregate(
                runs=Count("id"),
                n_ok=Count("id", filter=Q(ok=True)),
                errors=Count("id", filter=Q(ok=False)),
                avg_latency_ms=Avg("latency_ms"),
                accepted=Count("id", filter=Q(outcome=AssistantJob.ACCEPTED)),
                edited=Count("id", filter=Q(outcome=AssistantJob.EDITED)),
                discarded=Count("id", filter=Q(outcome=AssistantJob.DISCARDED)),
                pending=Count("id", filter=Q(outcome=AssistantJob.PENDING)),
            )
            avg = agg["avg_latency_ms"]
            rows.append({
                "job_type": job_type,
                "runs": agg["runs"],
                "ok": agg["n_ok"],
                "errors": agg["errors"],
                "avg_latency_ms": int(avg) if avg is not None else None,
                "accepted": agg["accepted"],
                "edited": agg["edited"],
                "discarded": agg["discarded"],
                "pending": agg["pending"],
            })
        return Response({"window_days": WINDOW_DAYS, "features": rows})


def _why_unanswered(job):
    """One reason per turn.

    The mechanical reason wins when there is one, because it says what to
    build. The asker's own verdict is shown only when nothing else explains it
    — a full answer marked not helpful is the one case feedback alone reveals.
    """
    if job.answer in (AssistantJob.DECLINED, AssistantJob.NOT_UNDERSTOOD):
        return job.answer
    if job.answer == AssistantJob.DATA and job.result_count == 0:
        return "empty"
    return "not_helpful"


class AssistantUnansweredView(AssistantBaseView):
    """What people asked the chatbot that it could not answer, most-asked first.

    The list of what to build next, taken from real use rather than guessed:
    questions no tool fits, questions it could not parse, lookups that came
    back empty, and answers the asker marked not helpful. Greetings, requests
    to change something and runtime failures are counted but not listed —
    none of them is a question the assistant should learn to answer.

    Administrators only, like the usage table it sits beside. The questions are
    already readable in the Django admin's job list, so this is a view of rows
    that exist, not new exposure. It names the asker's ROLE and never the
    person: the point is what the agency needs, not who asked.

    Not gated, for the metrics' reason: reading history has to keep working
    while the assistant is switched off.
    """
    permission_classes = [IsAdministrator]
    LIST_LIMIT = 25

    def get(self, request):
        since = timezone.now() - timedelta(days=WINDOW_DAYS)
        # Typed questions only: see FOLLOWUP_MODEL.
        chats = (AssistantJob.objects
                 .filter(job_type="chat", created_at__gte=since)
                 .exclude(model_used=FOLLOWUP_MODEL))

        data = Q(answer=AssistantJob.DATA)
        breakdown = chats.aggregate(
            total=Count("id"),
            answered=Count("id", filter=data & Q(result_count__gt=0)),
            empty=Count("id", filter=data & Q(result_count=0)),
            # Lookups logged before sizes were recorded. Unknown, not empty.
            unmeasured=Count("id", filter=data & Q(result_count__isnull=True)),
            declined=Count("id", filter=Q(answer=AssistantJob.DECLINED)),
            not_understood=Count("id", filter=Q(answer=AssistantJob.NOT_UNDERSTOOD)),
            action=Count("id", filter=Q(answer=AssistantJob.ACTION)),
            greeting=Count("id", filter=Q(answer=AssistantJob.GREETING)),
            failed=Count("id", filter=Q(answer=AssistantJob.FAILED)),
            helpful=Count("id", filter=Q(outcome=AssistantJob.ACCEPTED)),
            not_helpful=Count("id", filter=Q(outcome=AssistantJob.DISCARDED)),
        )

        misses = (chats
                  .filter(Q(answer__in=[AssistantJob.DECLINED,
                                        AssistantJob.NOT_UNDERSTOOD])
                          | (data & Q(result_count=0))
                          | Q(outcome=AssistantJob.DISCARDED))
                  .select_related("created_by__role")
                  .order_by("-created_at"))
        groups = {}
        for job in misses:
            why = _why_unanswered(job)
            # The same question asked twice is one row with a count. Case,
            # spacing and a trailing "?" are not a different question.
            key = (" ".join(job.input_ref.lower().split()).rstrip("?!. "), why)
            group = groups.get(key)
            if group is None:
                # Newest first, so the wording kept is the most recent.
                group = groups[key] = {
                    "question": job.input_ref, "why": why, "times": 0,
                    "last_asked": timezone.localtime(job.created_at).date().isoformat(),
                    "roles": set()}
            group["times"] += 1
            group["roles"].add(_role_of(job.created_by) or "Unknown")

        # Most-asked first; among equals, the most recent.
        ordered = sorted(groups.values(), key=lambda g: g["last_asked"], reverse=True)
        ordered.sort(key=lambda g: g["times"], reverse=True)
        for group in ordered:
            group["roles"] = sorted(group["roles"])
        return Response({"window_days": WINDOW_DAYS, "breakdown": breakdown,
                         "distinct": len(ordered),
                         "questions": ordered[:self.LIST_LIMIT]})


# input_ref prefix -> what the access log calls the read.
_ACCESS_KINDS = {"child": "brief", "report": "report_summary",
                 "casereferral": "referral_summary", "invite": "survey_check"}
# A case brief is "child:<id>" like the psychologist's brief and is told apart
# by its job type; the log names it for what it is.
_ACCESS_KINDS_BY_JOB_TYPE = {"case_brief": "case_brief"}

# Every job type the log lists: drafts, one row each, and the self-report check,
# one entry per survey. A job type missing from here, and not deliberately
# exempt in test_access_log.TheLogCoversEveryJobType, is a read of a child's
# record the ISA cannot see.
_LOGGED_DRAFTS = ("brief", "case_brief", "doc_intelligence")
_LOGGED_SURVEYS = ("self_report",)
ACCESS_LOGGED_JOB_TYPES = _LOGGED_DRAFTS + _LOGGED_SURVEYS


def _who(user):
    if user is None:
        return None
    return {"name": user.fullname or user.email, "role": _role_of(user)}


class ChildAccessLogView(AssistantBaseView):
    """Who had the model read this child's record: the ISA's question, answered
    for one child.

    Lists every pre-session brief, every social worker's case brief, every report
    and referral summary and the automatic self-report check, with when, who,
    what kind and how it ended.
    Metadata only - never `output_text` - so it shows who and when, not what
    was drafted.

    Administrators only, like the metrics beside it, and not gated for the
    same reason: reading history has to keep working while the assistant is
    switched off. `hide_earlier_history` does not apply because only an
    administrator reaches this view.

    Not listed, on purpose. Chat: the chatbot's model only picks a lookup and
    never reads a record - the results come from the database. Remark polish:
    it reads only the words being typed, and the request names no child. The
    census narrative: agency figures, not a record.
    """
    permission_classes = [IsAdministrator]
    LIMIT = 100

    def get(self, request, child_id):
        child = visible_children(request).filter(pk=child_id).first()
        if child is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        jobs = AssistantJob.objects.filter(child=child)
        drafts = (jobs.filter(job_type__in=_LOGGED_DRAFTS)
                  .select_related("created_by__role").order_by("-created_at"))
        # One row per answer checked; one entry per survey on screen.
        surveys = (jobs.filter(job_type__in=_LOGGED_SURVEYS).values("input_ref")
                   .annotate(at=Max("created_at"),
                             reads=Count("id", filter=Q(ok=True)),
                             failed=Count("id", filter=Q(ok=False)))
                   .order_by("-at"))
        names = {
            "report": dict(PsychologicalReport.objects.filter(child=child)
                           .values_list("id", "original_filename")),
            "casereferral": dict(CaseReferral.objects.filter(child=child)
                                 .values_list("id", "original_filename")),
            "invite": dict(OpinionnaireInvite.objects.filter(child=child)
                           .values_list("id", "template__title")),
        }

        def described(ref, job_type=None):
            prefix, _, pk = ref.partition(":")
            kept = names.get(prefix)
            doc_id = int(pk) if pk.isdigit() else None
            kind = _ACCESS_KINDS_BY_JOB_TYPE.get(job_type) or _ACCESS_KINDS.get(prefix, "other")
            return {"kind": kind,
                    "document": kept.get(doc_id) if kept is not None else None,
                    "document_deleted": kept is not None and doc_id not in kept}

        entries = [{"key": f"job:{job.id}", "at": job.created_at,
                    **described(job.input_ref, job.job_type),
                    "by": _who(job.created_by),
                    "status": job.outcome if job.ok else "failed"}
                   for job in drafts[:self.LIMIT]]
        entries += [{"key": s["input_ref"], "at": s["at"], **described(s["input_ref"]),
                     "by": None, "reads": s["reads"],
                     # Some answers read and the check then stopped is neither
                     # a clean read nor a failure to read.
                     "status": (("partly_read" if s["failed"] else "read")
                                if s["reads"] else "failed")}
                    for s in surveys[:self.LIMIT]]
        entries.sort(key=lambda e: e["at"], reverse=True)
        return Response({"entries": entries[:self.LIMIT],
                         "total": drafts.count() + surveys.count(),
                         "limit": self.LIMIT})


class AssistantCheckView(AssistantBaseView):
    """Probe the runtime and describe what happened.

    Returns 200 with ok=false rather than 503: the administrator asked a
    question about the runtime, and "it is unreachable" is a successful answer
    to that question. It also does not write an AssistantJob — a connection
    test is not clinical work and would skew the usage table.
    """
    permission_classes = [IsAdministrator]

    def post(self, request):
        cfg = AssistantSetting.load()
        if not cfg.enabled:
            return Response({"ok": False, "latency_ms": None,
                             "detail": "The assistant is switched off."})
        started = time.monotonic()
        try:
            get_ai_client(allow_hosted=True).generate("Reply with the single word: OK.")
        except AIUnavailable as exc:
            return Response({"ok": False, "latency_ms": None, "detail": str(exc)})
        elapsed = int((time.monotonic() - started) * 1000)
        return Response({"ok": True, "latency_ms": elapsed,
                         "detail": f"{cfg.model_name} answered in {elapsed} ms."})


MAX_QUESTION = 150          # AssistantJob.input_ref is max_length=150, so a
                            # valid question always fits the audit row whole.


class AssistantCapabilitiesView(AssistantBaseView):
    """What this user can ask. Read by the panel's empty state so a person who
    opened it from a button has somewhere to start.

    Deliberately not gated on the assistant being switched on: the answer is a
    fixed sentence, needs no model, and an empty panel with no hint is worse
    than one that explains itself. It reads the same source as the refusal
    text, so the two cannot drift apart.

    `brief` says which brief the signed-in role gets: "clinical" (the
    psychologist's, facts and a written brief) or "case" (everyone else's,
    facts alone). It follows the role, not the deployment, so the screen can
    choose the button before anything else has answered.

    `case_brief_writing` says whether this user is offered a written case
    brief: a social worker, on a deployment that drafts. The ISA and the
    psychologist never are, whatever the deployment.

    `drafting` is False where the model is hosted, because get_ai_client()
    refuses every caller without allow_hosted. A brief's prose, polish, summary
    or census narrative there can only answer 503, so the screens hide those
    buttons (the brief's facts stay: they need no model). It follows the
    deployment, not the administrator's switch. It stays authenticated because
    the deploy probe in CLAUDE.md reads its 401.
    """

    def get(self, request):
        role = _role(request)
        return Response({"can_ask": tools.capability_text(role),
                         "examples": tools.capability_examples(role),
                         "drafting": drafting_available(),
                         "brief": brief_kind(role),
                         "case_brief_writing": role == Role.STAFF and drafting_available()})


# answer_directly's `reason` defaults to "unsupported" in its resolver, so it
# must default the same way here or a missing reason is logged as something
# the user never saw.
_DIRECT_ANSWERS = {"greeting_or_closing": AssistantJob.GREETING,
                   "action_request": AssistantJob.ACTION}


def _answer_kind(call):
    """How a turn that reached a resolver ended."""
    if call.tool != "answer_directly":
        return AssistantJob.DATA
    return _DIRECT_ANSWERS.get(call.args.get("reason", "unsupported"),
                               AssistantJob.DECLINED)


# What a follow-up chip's turn records as the model that answered it: none did.
# The unanswered-questions card leaves these turns out — "This year?" means
# nothing out of context, and a refinement that finds nothing is not a
# question anybody typed.
FOLLOWUP_MODEL = "follow-up (no model)"


def _answer(request, call, question, model_used, started):
    """Log the turn, run the lookup, record how it ended, and respond.

    One path for both ways a turn arrives — a typed question the model routed,
    and a follow-up chip that skipped the model — so the log, the failure
    handling and the response cannot differ between them.
    """
    creator = request.user if request.user.is_authenticated else None
    job = AssistantJob.objects.create(
        job_type="chat", input_ref=question[:150],
        output_text=f"{call.tool}({call.args})"[:2000],
        model_used=model_used, ok=call.ok, error=call.error[:255],
        answer="" if call.ok else AssistantJob.NOT_UNDERSTOOD,
        latency_ms=int((time.monotonic() - started) * 1000),
        created_by=creator)

    if not call.ok:
        # Never guess. Say what happened and what it can do instead — from
        # capability_text, not a copy. This sentence was written before the
        # flags tool existed and never learned about it, which is what a
        # second hardcoded list of capabilities always does.
        return Response({
            "ok": False, "tool": call.tool,
            "message": f"I didn't follow that. {tools.capability_text(_role(request))}",
            "detail": call.error})

    # The audit row is written above, before the queryset runs, so a
    # resolver that raises would leave a row saying the turn succeeded —
    # the log would agree the question was answered while the user saw a
    # 500. Nothing is swallowed: the traceback goes to the logger and the
    # failure goes to the row. The panel already renders ok:false, so the
    # assistant declines instead of breaking the screen it is docked on.
    try:
        result = tools.REGISTRY[call.tool]["resolve"](request, call.args)
    except Exception:                                        # noqa: BLE001
        logger.exception("Chat resolver failed: %s(%s)", call.tool, call.args)
        job.ok = False
        job.error = f"resolver failed: {call.tool}"[:255]
        job.answer = AssistantJob.FAILED
        job.save(update_fields=["ok", "error", "answer"])
        return Response({
            "ok": False, "tool": call.tool,
            "message": "I couldn't finish looking that up. Nothing was "
                       "changed — try again, or open the screen directly.",
            "detail": "resolver failed"})

    # Recorded now because only now is it known. An empty lookup is the
    # failure this chatbot ranks worst, and until this line it left no
    # trace: the row said which tool ran, never what it found.
    job.answer = _answer_kind(call)
    job.result_count = tools.result_size(result)
    job.save(update_fields=["answer", "result_count"])

    return Response({"ok": True, "tool": call.tool, "echo": call.echo,
                     "result": result, "job": job.id,
                     "followups": tools.followups(call, result, _role(request))})


class AssistantAskView(AssistantBaseView):
    """The chatbot. A question in; a validated tool call and its result out.

    The model's entire output is a tool name and arguments — it never sees what
    comes back. Results go from the database to the response, so a turn costs
    seconds rather than tens of seconds and a child's name cannot be invented
    on the way out.

    Stateless: no history is sent. It would sit after the cached prefix and be
    re-prefilled at CPU speed every turn.
    """
    throttle_scope = "assistant_chat"

    def post(self, request):
        gate()
        question = request.data.get("question")
        if not isinstance(question, str) or not question.strip():
            return Response({"detail": "Ask a question first."},
                            status=status.HTTP_400_BAD_REQUEST)
        question = question.strip()
        if len(question) > MAX_QUESTION:
            return Response(
                {"detail": f"Keep the question under {MAX_QUESTION} characters."},
                status=status.HTTP_400_BAD_REQUEST)

        # The one feature whose prompt may leave the machine: it is the typed
        # question, and records only ever come back from the database.
        client = get_ai_client(allow_hosted=True)
        creator = request.user if request.user.is_authenticated else None
        started = time.monotonic()
        try:
            with services_lock():
                name, raw_args = client.choose_tool(
                    question, tools.ollama_payload(), system=prompts.CHAT_SYSTEM)
        except AIUnavailable as exc:
            AssistantJob.objects.create(
                job_type="chat", input_ref=question[:150], ok=False,
                answer=AssistantJob.FAILED,
                error=str(exc)[:255], model_used=getattr(client, "model", ""),
                latency_ms=int((time.monotonic() - started) * 1000),
                created_by=creator)
            raise

        # Prose instead of a tool call is not an error — it means nothing here
        # fits, which is a real answer.
        if not name:
            name, raw_args = "answer_directly", {"reason": "unsupported"}

        call = tools.validate(name, raw_args)
        # Deterministic backstop for the one misroute seen in the wild: "how
        # many psychologists are in the system?" answered "40 active children".
        # It can only make the assistant decline, never assert.
        call = tools.correct_obvious_misroute(question, call)
        # Same shape, different sentence: "book Ana for Friday" is a request to
        # change something, and "I can't answer that" is the wrong refusal.
        call = tools.correct_action_request(question, call)
        # And a greeting is a greeting even when the model forgets to say
        # so, which it does two times in three.
        call = tools.correct_greeting(question, call)
        return _answer(request, call, question, client.model, started)


class AssistantFollowupView(AssistantBaseView):
    """A follow-up chip: a ready-made tool call, run without the model.

    The panel only sends calls it was offered by tools.followups, but nothing
    here relies on that. The tool must be one that chips come from, the
    arguments go through the same validator as the model's, and every resolver
    takes scope from request.user — so a forged chip reaches nothing a
    perfectly behaved model could not. A call the validator refuses is a 400
    rather than "I didn't follow that": no model misunderstood anything, the
    request itself is wrong.

    Same switch and same rate budget as typed questions. The budget exists for
    the model more than for the database, but one door per person is simpler
    to reason about than two — and when there are two budgets, the cheaper
    door is the one that gets hammered.
    """
    throttle_scope = "assistant_chat"

    def post(self, request):
        gate()
        started = time.monotonic()
        tool, args, label = (request.data.get(k) for k in ("tool", "args", "label"))
        if tool not in tools.FOLLOWUP_TOOLS or not isinstance(args, dict):
            return Response({"detail": "That is not a follow-up the assistant offers."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(label, str) or not label.strip() or len(label) > MAX_QUESTION:
            return Response({"detail": "A follow-up needs the label it was offered with."},
                            status=status.HTTP_400_BAD_REQUEST)
        call = tools.validate(tool, args)
        if not call.ok:
            return Response({"detail": call.error}, status=status.HTTP_400_BAD_REQUEST)
        return _answer(request, call, label.strip(), FOLLOWUP_MODEL, started)


class ModelHealthView(AssistantBaseView):
    """Does the configured model actually answer? Administrators only.

    /healthz/ proves the database credential and nothing else. Without this,
    diagnosing "the chatbot is broken" means reading platform logs to work out
    whether the host is down, the token is wrong, or the model was retired —
    and a hosted model can be deprecated underneath a running deployment. A
    spike hit exactly that when @cf/meta/llama-3.1-8b-instruct began returning
    HTTP 410.
    """
    permission_classes = [IsAdministrator]

    def get(self, request):
        cfg = AssistantSetting.load()
        if not cfg.enabled:
            return Response({"reachable": False, "provider": "off", "model": "",
                             "detail": "The assistant is switched off."})

        client = get_ai_client(allow_hosted=True)
        provider = ("hosted" if isinstance(client, OpenAICompatibleClient)
                    else "local")
        model = getattr(client, "model", "")
        try:
            with services_lock():
                client.generate("Reply with the single word: ok", system="")
        except AIUnavailable as exc:
            # Never a 5xx. The question was answered, and the answer is "no".
            return Response({"reachable": False, "provider": provider,
                             "model": model, "detail": str(exc)[:300]})
        return Response({"reachable": True, "provider": provider,
                         "model": model, "detail": ""})
