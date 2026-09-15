import logging
import threading
import time
from datetime import timedelta

from django.db import connection
from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Role
from accounts.scoping import (role_of as _role, role_of_user as _role_of,
                              visible_children)
from accounts.permissions import IsAdministrator, IsAdminOrStaff
from assistant import evaluation, prompts, tools
from assistant.models import AssistantJob, AssistantSetting
from assistant.serializers import AssistantSettingSerializer
from assistant.services import (AIUnavailable, DISCLAIMER, OpenAICompatibleClient,
                                gate, get_ai_client, run_job, services_lock)
from children.models import Child
from clinical.models import CaseReferral, PsychologicalReport
from scheduling.models import Appointment

logger = logging.getLogger(__name__)




def _brief_only_author(child, user, role):
    """Author filter for build_brief_prompt(): the carry-history control
    (Child.assignee_sees_history) is enforced here so a brief can never read
    a previous psychologist's remarks once that flag hides them elsewhere."""
    if role == Role.PSYCHOLOGIST and not child.assignee_sees_history:
        return user
    return None


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
            user=request.user)
        return Response({"draft": draft, "job_id": job.id,
                         "generated_at": job.created_at,
                         "disclaimer": DISCLAIMER})


class LatestBriefView(AssistantBaseView):
    """Today's already-generated brief, served instantly.

    Reads history only, so it deliberately does NOT gate: a brief drafted this
    morning stays readable after an administrator switches the assistant off.
    """

    def get(self, request, child_id):
        try:
            child = visible_children(request).get(pk=child_id)
        except Child.DoesNotExist:
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
        job = AssistantJob.objects.filter(
            job_type="brief", input_ref=f"child:{child.id}", ok=True,
            created_at__date=timezone.localdate()).first()
        if not job:
            return Response({"detail": "No brief drafted today."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response({"draft": job.output_text, "job_id": job.id,
                         "generated_at": job.created_at,
                         "disclaimer": DISCLAIMER})


# Children currently being briefed, so two page loads cannot queue the same
# child twice. Guarded by its own lock; the generation lock lives in services.
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
                        input_ref=f"child:{child.id}", user=user)
            except AIUnavailable:
                # Already audited by run_job. A runtime that is down must not
                # abandon the rest of the queue.
                logger.info("Prefetch skipped child %s: runtime unavailable", child_id)
    finally:
        with _IN_FLIGHT_LOCK:
            _IN_FLIGHT.difference_update(child_ids)


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
        gate()
        today = timezone.localdate()
        visible = visible_children(request)
        appts = Appointment.objects.filter(
            child__in=visible, status=Appointment.SCHEDULED,
            start__date=today, psychologist=request.user)

        child_ids = list(dict.fromkeys(appts.values_list("child_id", flat=True)))
        already = set(AssistantJob.objects.filter(
            job_type="brief", ok=True, created_at__date=today,
            input_ref__in=[f"child:{cid}" for cid in child_ids]
        ).values_list("input_ref", flat=True))

        queued, skipped = [], []
        with _IN_FLIGHT_LOCK:
            for cid in child_ids:
                if f"child:{cid}" in already or cid in _IN_FLIGHT:
                    skipped.append(cid)
                else:
                    _IN_FLIGHT.add(cid)
                    queued.append(cid)

        if queued:
            _start_prefetch_thread(queued, request.user)
        return Response({"queued": queued, "skipped": skipped})


# kind -> (model, input_ref prefix, human label for the prompt, author field name)
_DOC_KINDS = {
    "report": (PsychologicalReport, "report", "psychological report", "author"),
    "case-referral": (CaseReferral, "casereferral", "case referral", "uploaded_by"),
}


class DocumentSummaryView(AssistantBaseView):
    """Draft a summary of an uploaded document into its `ai_summary` column.

    The draft is saved unconfirmed. It only becomes clinical text when a human
    confirms it, at which point it is their words, not a draft.
    """
    throttle_scope = "assistant_draft"
    kind = None

    def post(self, request, doc_id):
        gate()
        model, prefix, label, author_field = _DOC_KINDS[self.kind]
        doc = model.objects.filter(
            pk=doc_id, child__in=visible_children(request)).first()
        if not doc:
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
        # Carry-history control: without it, a newly assigned psychologist must
        # not have a document they did not author fed to the model, since the
        # draft it produces would surface facts this screen otherwise hides.
        if (_role(request) == Role.PSYCHOLOGIST and not doc.child.assignee_sees_history
                and getattr(doc, f"{author_field}_id") != request.user.id):
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
        if not (doc.extracted_text or "").strip():
            return Response(
                {"detail": "No text could be extracted from this document."},
                status=status.HTTP_400_BAD_REQUEST)

        draft, job = run_job(
            "doc_intelligence",
            prompts.build_summary_prompt(doc.extracted_text, label),
            system=prompts.SUMMARY_SYSTEM,
            input_ref=f"{prefix}:{doc.id}",
            user=request.user)
        doc.ai_summary = draft
        doc.ai_summary_confirmed = False
        doc.save(update_fields=["ai_summary", "ai_summary_confirmed"])
        return Response({"draft": draft, "job_id": job.id,
                         "disclaimer": DISCLAIMER})


class ConfirmSummaryView(AssistantBaseView):
    """Confirm a summary as the human's own words.

    Not gated: confirming is the psychologist's act, and must keep working
    after an administrator switches the assistant off.
    """
    kind = None

    def post(self, request, doc_id):
        model, prefix, _, _ = _DOC_KINDS[self.kind]
        doc = model.objects.filter(
            pk=doc_id, child__in=visible_children(request)).first()
        if not doc:
            return Response({"detail": "Not found."},
                            status=status.HTTP_404_NOT_FOUND)
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
            get_ai_client().generate("Reply with the single word: OK.")
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
    """

    def get(self, request):
        role = _role(request)
        return Response({"can_ask": tools.capability_text(role),
                         "examples": tools.capability_examples(role)})


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

        client = get_ai_client()
        creator = request.user if request.user.is_authenticated else None
        started = time.monotonic()
        try:
            with services_lock():
                name, raw_args = client.choose_tool(
                    question, tools.ollama_payload(), system=prompts.CHAT_SYSTEM)
        except AIUnavailable as exc:
            AssistantJob.objects.create(
                job_type="chat", input_ref=question[:150], ok=False,
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
        job = AssistantJob.objects.create(
            job_type="chat", input_ref=question[:150],
            output_text=f"{call.tool}({call.args})"[:2000],
            model_used=client.model, ok=call.ok, error=call.error[:255],
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
            job.save(update_fields=["ok", "error"])
            return Response({
                "ok": False, "tool": call.tool,
                "message": "I couldn't finish looking that up. Nothing was "
                           "changed — try again, or open the screen directly.",
                "detail": "resolver failed"})

        return Response({"ok": True, "tool": call.tool, "echo": call.echo,
                         "result": result})


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

        client = get_ai_client()
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
