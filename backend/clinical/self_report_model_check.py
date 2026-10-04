"""The second detector: the local model reads the same (question, answer) pair.

It exists to catch phrasing nobody thought to list — precisely where the
Ilocano entries are weakest, since those have not been read by a speaker. It
can only ADD a flag. It is never consulted about removing one, and the runtime
being unavailable degrades silently to the lexicon.

Its accuracy on Ilocano is unmeasured. qwen2.5:3b was measured on Taglish, not
on a low-resource Philippine language, and there is no reason to assume the
result transfers. That is why it supplements the lexicon rather than replacing
it.
"""
import logging
import re
import threading
import time

from django.db import connection

logger = logging.getLogger(__name__)

_YES = re.compile(r"^\s*(yes|oo)\b", re.IGNORECASE)
_LEAD = re.compile(r"^\s*(yes|oo)\b[\s\-–—:,.]*", re.IGNORECASE)


def _parse(reply):
    """Return the reason if the reply is a YES, else None.

    An unparseable reply yields None. Never flag on something we did not
    understand — a flag nobody can explain is worse than no flag.
    """
    text = " ".join(str(reply or "").split())
    if not _YES.match(text):
        return None
    reason = _LEAD.sub("", text)
    return (reason or "flagged by the local model")[:255]


def run_model_check(invite_id):
    """Synchronous. Safe to call from a thread, a command, or a test."""
    from assistant import prompts
    from assistant.models import AssistantJob, AssistantSetting
    from assistant.services import AIUnavailable, get_ai_client, services_lock
    from clinical.models import OpinionnaireInvite, SelfReportFlag

    if not AssistantSetting.load().enabled:
        return

    invite = (OpinionnaireInvite.objects
              .select_related("child").filter(pk=invite_id).first())
    if invite is None:
        return

    client = get_ai_client()
    for question, answer in (invite.answers or {}).items():
        if not str(answer or "").strip():
            continue
        if SelfReportFlag.objects.filter(
                invite=invite, question=question,
                source=SelfReportFlag.MODEL).exists():
            continue

        started = time.monotonic()
        try:
            with services_lock():
                reply = client.generate(
                    prompts.build_self_report_prompt(question, answer),
                    system=prompts.SELF_REPORT_SYSTEM)
        except AIUnavailable as exc:
            # Expected, not exceptional. The lexicon has already run.
            AssistantJob.objects.create(
                job_type="self_report", input_ref=f"invite:{invite.pk}",
                ok=False, error=str(exc)[:255],
                model_used=getattr(client, "model", ""),
                latency_ms=int((time.monotonic() - started) * 1000),
                child=invite.child)
            return

        reason = _parse(reply)
        AssistantJob.objects.create(
            job_type="self_report", input_ref=f"invite:{invite.pk}",
            output_text=str(reply)[:2000], model_used=client.model, ok=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            child=invite.child)

        if reason:
            SelfReportFlag.objects.get_or_create(
                invite=invite, question=question, source=SelfReportFlag.MODEL,
                defaults={"child": invite.child, "answer": answer,
                          "matched": reason})


def start_model_check(invite_id):
    """Fire and forget. The caller is a child's device; it does not wait."""
    def worker():
        try:
            run_model_check(invite_id)
        except Exception:                                    # noqa: BLE001
            logger.exception("Self-report model check failed for %s", invite_id)
        finally:
            # A thread owns its own connection and must hand it back.
            connection.close()

    threading.Thread(target=worker, daemon=True).start()
