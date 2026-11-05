"""How long the assistant keeps the words it was given and the words it wrote.

Every model call leaves an `AssistantJob` row: the audit trail the ISA reads
(who had the model read which child, how often, how it ended) and the figures
behind the usage table. The same row also carried TEXT - the typed question,
the tool call, a whole drafted brief, the model's summary of a report - and
nothing ever removed it, so the table slowly became a second copy of case
material in a place with its own, wider, readers.

This module blanks that text once it has no use, and keeps everything else:
the row, its type, who, which child, when, whether it worked, the outcome and
the figures. The access log, the usage table and the unanswered-questions list
read exactly what they read before.

    chat              question and tool call     30 days   (the unanswered
                                                            list's own window)
    brief, case_brief,
    remark_polish     the draft                  2 days    (only today's brief
                                                            is ever served)
    doc_intelligence  the model's summary        once it is confirmed - the
                                                 person's own words are on the
                                                 document - or its document is
                                                 gone, or 30 days
    self_report       the model's reply          30 days   (the flag, and the
                                                 reason it shows, live on the
                                                 SelfReportFlag)

`input_ref` is never blanked on any job but a chat: the access log reads
"child:12" and "report:3" off it. The census narrative is not here: it is
agency figures, not a record.

It runs lazily (`prune_if_due`, from `run_job` and the chat answer) and by hand
(`manage.py prune_assistant_text`). Hosted deployments have no shell, so nothing
may depend on one.
"""
import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from assistant.models import AssistantJob

logger = logging.getLogger(__name__)

CHAT_DAYS = 30
DRAFT_DAYS = 2
SUMMARY_DAYS = 30
SELF_REPORT_DAYS = 30
DRAFT_JOB_TYPES = ("brief", "case_brief", "remark_polish")

# How often the lazy hook looks. Pruning is idempotent and cheap, so this is
# not a correctness matter: it keeps a busy server from running four queries on
# every request.
EVERY = timedelta(hours=1)

# input_ref prefix -> the model whose summary the job drafted. Both keep the
# summary on the document, as `ai_summary` / `ai_summary_confirmed`.
_DOCUMENTS = {"report": "clinical.PsychologicalReport",
              "casereferral": "clinical.CaseReferral"}


def _blank(rows, fields, dry_run):
    """Blank `fields` on `rows`; returns how many rows that was (or would be)."""
    if dry_run:
        return rows.count()
    return rows.update(**{field: "" for field in fields})


def _older_than(job_types, days, now):
    return AssistantJob.objects.filter(
        job_type__in=job_types, created_at__lt=now - timedelta(days=days))


def _finished_summaries(now):
    """Ids of doc_intelligence rows whose text is no longer needed because of
    what happened to the DOCUMENT: the summary was confirmed (it is now the
    person's own text, on the document) or the document is gone."""
    from django.apps import apps

    rows = (AssistantJob.objects.filter(job_type="doc_intelligence")
            .exclude(output_text="").values_list("pk", "input_ref"))
    wanted = {}                       # prefix -> {document id: [job ids]}
    for pk, ref in rows:
        prefix, _, doc_id = ref.partition(":")
        if prefix in _DOCUMENTS and doc_id.isdigit():
            wanted.setdefault(prefix, {}).setdefault(int(doc_id), []).append(pk)
    done = []
    for prefix, by_doc in wanted.items():
        model = apps.get_model(_DOCUMENTS[prefix])
        waiting = set(model.objects.filter(pk__in=by_doc, ai_summary_confirmed=False)
                      .values_list("pk", flat=True))
        for doc_id, job_ids in by_doc.items():
            if doc_id not in waiting:
                done.extend(job_ids)
    return done


def prune(now=None, *, dry_run=False):
    """Blank the text that has outlived its use. Returns {rule: rows}.

    `now` is a parameter so a test can hold the clock; nothing here reads it
    otherwise. With `dry_run` it only counts.
    """
    now = now or timezone.now()
    nothing_to_keep = AssistantJob.objects.exclude(output_text="")
    counts = {}

    chats = _older_than(("chat",), CHAT_DAYS, now).exclude(input_ref="", output_text="")
    counts["chat"] = _blank(chats, ("input_ref", "output_text"), dry_run)

    drafts = _older_than(DRAFT_JOB_TYPES, DRAFT_DAYS, now).exclude(output_text="")
    counts["drafts"] = _blank(drafts, ("output_text",), dry_run)

    old_summaries = _older_than(("doc_intelligence",), SUMMARY_DAYS, now).exclude(output_text="")
    counts["summaries_aged"] = _blank(old_summaries, ("output_text",), dry_run)
    finished = nothing_to_keep.filter(pk__in=_finished_summaries(now))
    counts["summaries_finished"] = _blank(finished, ("output_text",), dry_run)

    reports = _older_than(("self_report",), SELF_REPORT_DAYS, now).exclude(output_text="")
    counts["self_report"] = _blank(reports, ("output_text",), dry_run)
    return counts


_lock = threading.Lock()
_last_run = None


def prune_if_due(now=None):
    """prune(), at most once an hour per process, and never a reason for a
    request to fail. Returns whether it ran.

    Per process on purpose: pruning is idempotent, so a second worker doing it
    in the same hour costs a few queries and changes nothing. Off under the
    test runner (ASSISTANT_PRUNE_ON_REQUEST), where it would run at an
    arbitrary point of an arbitrary test and blank rows another one is about
    to read; its own tests switch it on.
    """
    global _last_run
    if not getattr(settings, "ASSISTANT_PRUNE_ON_REQUEST", True):
        return False
    now = now or timezone.now()
    with _lock:
        # A clock that went backwards is treated as due, not as "wait".
        if _last_run is not None and timedelta(0) <= now - _last_run < EVERY:
            return False
        _last_run = now
    try:
        prune(now)
    except Exception:                                        # noqa: BLE001
        logger.exception("Pruning the assistant log failed")
    return True
