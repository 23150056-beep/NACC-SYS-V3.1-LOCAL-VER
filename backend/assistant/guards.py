"""A model's draft is shown to a social worker only if it says nothing its source
did not.

Why this exists (owner, 12 Oct 2026). The written part of the social worker's
case brief was measured on the owner's PC with `ai_eval --feature case_brief`:
2 of 30 drafts invented dates, and drafts the checker called clean still made up
things a social worker could act on. It was taken away. This module is what
keeps any OTHER model text a social worker can still obtain from reaching them
unchecked, and `STAFF_DRAFTING` (below) is the rule that keeps that true for
every kind of draft there will ever be.

`checked_draft(job_type, text, source_text)` returns the text, or raises
`DraftRefused` when the draft states a name, a date or a number that is not in
the source. It is built on the checks `ai_eval` is scored with
(assistant/evaluation.py), which are string checks - no model, no database - and
which compare against what the model was GIVEN, never against the instructions.

What it does NOT catch, and so is not a promise: a draft can still be wrong
without naming anything, by pairing true facts wrongly ("a home visit by the
social worker" for a psychologist's session) or by inventing a relationship in
plain words. The checks catch names, dates and numbers. That is why the model's
text a social worker may reach is kept as small as it can be.
"""
from assistant import evaluation

# The one sentence a person is shown when a draft is refused. It does not say
# what was found: the reason goes to the job's `error`, for whoever audits it.
REFUSAL_SENTENCE = ("The assistant's draft mentioned something that is not in the "
                    "source, so it is not shown. Try again, or write it yourself.")

NAMES, DATES, NUMBERS = "names", "dates", "numbers"

# Which checks apply, per job type that a Staff account can obtain text from
# (STAFF_DRAFTING, below, says which those are). A type that is not here has no
# decision and cannot be checked: checked_draft raises ValueError for it.
CHECKS = {
    # Figures in, a narrative out: it restates numbers and names nobody, so all
    # three apply and the figures it was sent are the whole source.
    "census_narrative": (NAMES, DATES, NUMBERS),
    # A SUMMARY of a document paraphrases, and a paraphrase says "two weeks
    # later" for a date and "a few" for a number, so a date or number check
    # against the document's text would refuse correct summaries and teach
    # people to ignore the sentence. A name the document never mentions is
    # still an invention whatever the wording, so names alone are checked here.
    "doc_intelligence": (NAMES,),
    # The model's reason for flagging a child's answer, in a few words, drawn
    # from the question and the answer it was shown.
    "self_report": (NAMES, DATES, NUMBERS),
    # A polish rewrites the writer's own words and nothing else, so any name,
    # date or number that is not in what they typed is an invention. Unlike a
    # summary it does not paraphrase dates ("two weeks later"), so all three
    # apply, against the text they sent.
    "record_polish": (NAMES, DATES, NUMBERS),
}


class DraftRefused(Exception):
    """The draft states something its source does not. `reason` says what, for
    the audit row; the person is shown REFUSAL_SENTENCE and nothing more."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _names_source(source_text):
    """The source as the name check should read it.

    The check clears a word the source already has, and reads "Foster" in
    "case_type_Foster Care" as no word at all (an underscore joins it to its
    neighbours), nor "JUAN" in an all-capitals form as a name (it wants a
    capital then lower case). So the underscores become spaces, and a copy in
    title case is added: the same words, in the dress the model will write
    them in.
    """
    spaced = source_text.replace("_", " ")
    return f"{spaced}\n{spaced.title()}"


def _one_sentence_per_line(text):
    """The draft with each line made its own sentence, for the name check.

    That check wants a lowercase word before a capital, which is how it leaves
    sentence starts alone - but it reads a line break as a space, so a line that
    ends in a word and is followed by a heading or a bullet's first word looks
    like the middle of a sentence ("...the home\\nBackground:"). A name at the
    start of a line is not seen either way, which is the check's stated limit.
    """
    return "\n".join(f"{line.strip()}." if line.strip() else "" for line in text.splitlines())


def checked_draft(job_type, text, source_text):
    """`text`, if it states no name, date or number (as `CHECKS` says for this
    job type) that `source_text` does not hold; otherwise DraftRefused.

    `source_text` is what the model was given to write from - the facts, the
    figures, the document - and not the instructions, whose own digits ("under
    150 words") would let a made-up number through.
    """
    try:
        checks = CHECKS[job_type]
    except KeyError:
        raise ValueError(
            f"No checks are decided for the job type {job_type!r}. Decide them in "
            "assistant/guards.py CHECKS, and say how a Staff account is treated in "
            "STAFF_DRAFTING.") from None

    found = []
    if NAMES in checks:
        names = evaluation.invented_names(
            _names_source(source_text), _one_sentence_per_line(text))
        if names:
            found.append(("names", names))
    if DATES in checks:
        dates = evaluation.invented_dates(source_text, text)
        if dates:
            found.append(("dates", dates))
    if NUMBERS in checks:
        numbers = evaluation.invented_numbers(source_text, text)
        if numbers:
            found.append(("numbers", numbers))
    if found:
        raise DraftRefused("; ".join(
            f"{label} not in the source: {', '.join(items[:6])}" for label, items in found))
    return text


# --- the rule that keeps this true for every kind of draft ----------------------
#
# How a Staff account (a social worker) is treated, for EVERY kind of model job
# there is: `AssistantJob.TYPE_CHOICES`. A new job type is a new way for model
# text to reach somebody, and the last one (the written case brief) shipped to
# social workers without anything asking whether it should. So a type missing
# from here fails `assistant/tests/test_staff_drafting.py`, which also runs
# every entry as a Staff account against a model that writes an invented name,
# date and number, and fails when the behaviour is not the one claimed.
#
#   REFUSED    the endpoint refuses a Staff account (403 or 404), or no endpoint
#              offers the type at all. Staff never receive the text.
#   GUARDED    a Staff account can obtain the text, and checked_draft() runs on
#              it first (CHECKS above says which checks). What fails is not
#              shown or stored.
#   TOOL_ONLY  the model's whole output is a tool name and its arguments, and
#              the words the person reads are fixed copy and rows from the
#              database. Only the validated arguments come back, in the
#              "Looking up: ..." line, so that the person can see what was
#              understood.
REFUSED, GUARDED, TOOL_ONLY = "refused", "guarded", "tool_only"

STAFF_DRAFTING = {
    # The psychologist's written pre-session brief, drafted from case notes a
    # social worker cannot write and has no case reason to have a model read.
    # PreSessionBriefView, LatestBriefView and PrefetchBriefsView answer 403
    # before anything reaches the model (views._written_brief_refused).
    "brief": REFUSED,
    # The social worker's written case brief, taken away on 12 Oct 2026: it
    # invented dates and relationships. No route serves it. The prompt and
    # `ai_eval --feature case_brief` remain only to measure it again.
    "case_brief": REFUSED,
    # A psychologist's case note, polished while they write it. A social worker
    # cannot write one, so RemarkPolishView answers 403 to any role that cannot.
    "remark_polish": REFUSED,
    # Summaries of uploaded documents. A social worker can ask for one on a
    # case referral (a psychologist's report is refused them), so the referral
    # draft is checked for names before it is stored or shown.
    "doc_intelligence": GUARDED,
    # The Agency Summary's narrative, which Staff may ask for: checked against
    # the figures it was sent.
    "census_narrative": GUARDED,
    # The chatbot. The model answers with a tool name and arguments and never
    # sees a result: the answer is the database's.
    "chat": TOOL_ONLY,
    # The model's second look at a child's own answers. It only adds a flag, but
    # its few words of reason are stored on that flag and read by whoever can
    # read the child's flags, a social worker included, so the reason is checked
    # against the question and answer it was drawn from and, when it fails,
    # replaced by a fixed one. The flag itself is never lost.
    "self_report": GUARDED,
    # A child's record text (referral reason, medical notes, recommendation,
    # closing summary), polished while a social worker, the ISA or the
    # psychologist writes it. Staff may ask, so the draft is checked against the
    # text they sent before it is shown; it is never stored by the assistant.
    "record_polish": GUARDED,
}
