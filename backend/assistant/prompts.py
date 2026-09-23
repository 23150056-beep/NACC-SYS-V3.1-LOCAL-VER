"""Prompt templates.

Every builder returns STATIC_INSTRUCTIONS + dynamic_facts, in that order and
never the other way round. The static half is a module constant so it is
literally the same bytes on every call, which keeps the runtime's prefix cache
warm — the difference between a 0.37s and a 17s prefill.
"""
from django.utils import timezone

# --- systems -------------------------------------------------------------

REMARK_POLISH_SYSTEM = (
    "You rewrite clinical case notes for a child protection agency in clear, "
    "professional English. You keep every fact exactly as given. You never add "
    "information, never diagnose, and never estimate ages or dates."
)

BRIEF_SYSTEM = (
    "You prepare short factual briefs for a licensed psychologist before a "
    "session with a child. You use only the facts you are given."
)

SUMMARY_SYSTEM = (
    "You summarise case documents for a child protection agency. You report "
    "only what the document says."
)

CENSUS_SYSTEM = (
    "You write short factual narratives about agency caseload figures. You "
    "restate the figures you are given and never compute new ones."
)

# --- static instruction blocks -------------------------------------------

REMARK_INSTRUCTIONS = (
    "Rewrite the case note below in clear professional English.\n"
    "Keep every fact. Do not add anything that is not written.\n"
    "Return only the rewritten note, with no preamble.\n\n"
    "NOTE:\n"
)

BRIEF_INSTRUCTIONS = (
    "Write a short pre-session brief for the psychologist from the facts "
    "below.\n"
    "Cover, in this order: (1) where the case stands, (2) what has changed "
    "recently, (3) what to look for in this session.\n"
    "Use only the facts provided below. Do not state age, gender, or any other "
    "detail not given. Refer to the child by first name only.\n"
    "Do not diagnose and do not suggest a score or rating.\n"
    "Keep it under 200 words.\n\n"
    "FACTS:\n"
)

SUMMARY_INSTRUCTIONS = (
    "Summarise the document below.\n"
    "Cover: (1) background and family or social context, 3-5 bullets; "
    "(2) presenting concerns, 2-4 bullets; (3) recommendations the author "
    "noted, 1-3 bullets.\n"
    "Use only information present in the text. If a section has nothing, write "
    "'Not stated'.\n\n"
    "DOCUMENT:\n"
)

CENSUS_INSTRUCTIONS = (
    "Write a short narrative describing the caseload figures below.\n"
    "Restate the figures given. Do not calculate anything, do not infer trends "
    "that are not stated, and do not name any child.\n"
    "Two short paragraphs at most.\n\n"
    "FIGURES:\n"
)


# --- builders ------------------------------------------------------------

def child_age(child):
    """Age in years as a string, or the literal 'unknown'.

    The model guessed ages in V2 when it was not told one, so it is always
    told one — including being told that it is unknown.
    """
    if not child.birth_date:
        return "unknown"
    today = timezone.localdate()
    born = child.birth_date
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return str(years)


def build_brief_prompt(child, *, only_author=None):
    """`only_author` enforces the carry-history control (Child.assignee_sees_history):
    when set, only remarks written by that user are ever seen by the model."""
    facts = [
        f"First name: {(child.fullname or '').split(' ')[0]}",
        f"Age: {child_age(child)}",
        f"Gender: {child.gender or 'unspecified'}",
        f"Case status: {child.status or 'unknown'}",
    ]
    remarks = []
    if child.pk:
        qs = child.remarks.all()
        if only_author is not None:
            qs = qs.filter(author=only_author)
        remarks = qs[:5]
    if remarks:
        facts.append("Recent remarks (newest first):")
        facts.extend(f"- {r.date}: {r.text}" for r in remarks)
    else:
        facts.append("Recent remarks: none recorded.")
    return BRIEF_INSTRUCTIONS + "\n".join(facts)


def build_remark_prompt(raw_text):
    return REMARK_INSTRUCTIONS + raw_text


def build_summary_prompt(extracted_text, kind):
    # `kind` labels the document for the reader; it goes after the static block.
    return SUMMARY_INSTRUCTIONS + f"({kind})\n{fit_document(extracted_text)[0]}"


# --- long documents -------------------------------------------------------
#
# The whole extracted text used to follow the instructions - up to 200,000
# characters. Nothing sets the model's context window on the agency machine
# (setting it per call measured 6x slower), so it is the runtime's default, a
# few thousand tokens. A real report overflows that, and what the runtime then
# drops to make room is not this code's choice - it may be the instructions.
# So the document is fitted here, on purpose, and the draft says what it read.
#
# 8,000 characters is roughly 2,000-2,700 tokens of English or Taglish: with
# the instructions and room for the answer, inside a 4,096-token window. That
# is arithmetic, not a measurement - `manage.py ai_eval --feature summary`
# measures it on the machine that runs the model.
SUMMARY_BUDGET_CHARS = 8000
# Below this, a section is not worth starting: a heading and one line.
_MIN_SECTION_CHARS = 400

# What a summary of (background, concerns, recommendations) reads first when a
# report is too long to read whole, by words in the section's own heading.
# Recommendations come first because they are usually last in the file - the
# part a cut from the end would lose.
_SUMMARY_PRIORITY = (
    ("recommend", "impression", "plan", "conclusion"),
    ("reason", "referral", "presenting", "concern", "problem"),
    ("background", "history", "family", "social", "identifying"),
    ("finding", "result", "observation", "assessment", "summary"),
)


def _sections(text):
    """[(heading or None, body)] from the "## " lines the extractor writes."""
    out, heading, lines = [], None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None or any(l.strip() for l in lines):
                out.append((heading, "\n".join(lines).strip()))
            heading, lines = line[3:].strip(), []
        else:
            lines.append(line)
    if heading is not None or any(l.strip() for l in lines):
        out.append((heading, "\n".join(lines).strip()))
    return out


def _cut(body, limit):
    """At most `limit` characters, ending at a line break where there is one."""
    if len(body) <= limit:
        return body
    part = body[:limit]
    end = part.rfind("\n")
    return (part[:end] if end > limit // 2 else part).rstrip() + " [...]"


def _priority(heading):
    words = (heading or "").lower()
    for rank, keys in enumerate(_SUMMARY_PRIORITY):
        if any(k in words for k in keys):
            return rank
    return len(_SUMMARY_PRIORITY)


def fit_document(text, budget=SUMMARY_BUDGET_CHARS):
    """(what the model is given, what was left out - or None if nothing was).

    With headings, whole sections are kept in the order a summary needs them
    and put back in the document's own order. Without them, the beginning and
    the end are kept - where reports put who, why and what next.
    """
    text = (text or "").strip()
    if len(text) <= budget:
        return text, None
    sections = [s for s in _sections(text) if s[0]]
    if len(sections) >= 2:
        order = sorted(range(len(sections)), key=lambda i: (_priority(sections[i][0]), i))
        kept, left = {}, budget
        for i in order:
            heading, body = sections[i]
            size = len(heading) + 4 + len(body)
            if size <= left:
                kept[i] = body
                left -= size
            elif left >= _MIN_SECTION_CHARS:
                kept[i] = _cut(body, left - len(heading) - 4)
                left = 0
            if left < _MIN_SECTION_CHARS:
                break
        fitted = "\n".join(f"## {sections[i][0]}\n{kept[i]}" for i in sorted(kept))
        # Name what the draft could NOT have seen - that is what the person
        # confirming it needs to know - rather than everything it did.
        left_out = [sections[i][0] for i in range(len(sections)) if i not in kept]
        partial = [sections[i][0] for i in sorted(kept) if kept[i] != sections[i][1]]
        note = ["This report is too long to read in one go."]
        if left_out:
            note.append(f"Not read: {', '.join(left_out)}.")
        if partial:
            note.append(f"Only the first part of {', '.join(partial)} was read.")
        return fitted, " ".join(note)
    head, tail = int(budget * 0.6), int(budget * 0.4)
    start = text[:head]
    start = start[:start.rfind("\n")] if "\n" in start else start
    end = text[-tail:]
    end = end[end.find("\n") + 1:] if "\n" in end else end
    return (f"{start.rstrip()}\n[...]\n{end.lstrip()}",
            "This report is too long to read in one go, and has no headings to choose "
            "by. Only its beginning and its end were read.")


def build_census_prompt(figures):
    lines = [f"{key}: {value}" for key, value in sorted(figures.items())]
    return CENSUS_INSTRUCTIONS + "\n".join(lines)


# --- chatbot --------------------------------------------------------------
# One static block, byte-identical on every call, so the runtime's prefix cache
# stays warm — the question is the only thing that varies and it goes last.
#
# Each tool carries its own description; what belongs here is the framing and
# the examples. The examples are worth 9 points of measured accuracy and cost
# nothing at runtime once the prefix is cached. Half of them are Tagalog or
# Taglish, which is how the notes are actually written.
#
# It names all three roles and stays one fixed string. It used to say the user
# "is a psychologist", which was false for staff and administrators on every
# turn; naming the caller's actual role would make this differ per request and
# throw away the prefix cache. Listing all three is true and still constant.

CHAT_SYSTEM = """You are the assistant inside NACC SYS, a child psychological \
assessment system used by a child protection agency in the Philippines. The \
signed-in user is a psychologist, a staff member or an administrator, and may \
write in English, Tagalog, or a mix of both. Every tool is already scoped to what this user may see - never ask \
about permissions or ownership. Call exactly one tool.

Examples:
  "What have I got on Friday?"        -> list_my_appointments(when="this_week")
  "Who am I seeing tomorrow?"         -> list_my_appointments(when="tomorrow")
  "Ano ang schedule ko bukas?"        -> list_my_appointments(when="tomorrow")
  "How many kids am I handling?"      -> get_statistics(measure="children")
  "Ilan ang mga bata ko?"             -> get_statistics(measure="children")
  "Active children by case type?"     -> get_statistics(measure="children", by="case_type")
  "How many cases closed this year?"  -> get_statistics(measure="closures", period="this_year")
  "What was the no-show rate last month?" -> get_statistics(measure="sessions", period="last_month")
  "How long do children wait for a first session?" -> get_statistics(measure="first_session_wait")
  "How long do pre-assessments take?" -> get_statistics(measure="pre_assessment_duration")
  "Any children with sleep problems?" -> search_children_by_concern(concern="sleep problems")
  "Sino ang mga bata na ayaw pumasok sa eskwela?" -> search_children_by_concern(concern="school")
  "Sino ang mga batang may problema sa tulog?" -> search_children_by_concern(concern="sleep")
  "Tell me about Ana Reyes"           -> get_child_summary(name="Ana Reyes")
  "Sino si Ana Reyes?"                -> get_child_summary(name="Ana Reyes")
  "Who still needs a report?"         -> list_care_gaps()
  "Sino ang kailangan ng follow-up?"  -> list_care_gaps()
  "Who is free tomorrow?"             -> find_availability(when="tomorrow")
  "Sino ang bakante sa Friday?"       -> find_availability(when="this_week")
  "How many psychologists are there?" -> count_people(role="psychologist")
  "Ilan ang staff dito?"              -> count_people(role="staff")
  "Which children have no psychologist?" -> list_unassigned_children()
  "Who flagged something worrying?"   -> list_self_report_flags()
  "Sino ang may nakakabahala?"        -> list_self_report_flags()
  "Who did I see kahapon?"            -> list_my_appointments(when="yesterday")
  "Ilan ang appointments ko ngayong buwan?" -> list_my_appointments(when="this_month")
  "Good morning!"                     -> answer_directly(reason="greeting_or_closing")
"""


# --- self-report concerns -------------------------------------------------
# The second detector. It reads the same (question, answer) pair the lexicon
# does, and exists to catch phrasing nobody thought to list — which is where
# the Ilocano entries are weakest. Static block first, the exchange last.

SELF_REPORT_SYSTEM = (
    "You read short self-reports written by children in a child protection "
    "agency in the Philippines. They write in English, Tagalog, Ilocano, or a "
    "mix. You judge only whether the child expresses distress."
)

SELF_REPORT_INSTRUCTIONS = (
    "Does this child's answer express distress, fear, sadness, pain, or being "
    "alone?\n"
    "Answer with one word, YES or NO, then a dash and at most eight words "
    "saying why.\n"
    "Judge only the answer given. Do not infer anything not written.\n\n"
    "EXCHANGE:\n"
)


def build_self_report_prompt(question, answer):
    return SELF_REPORT_INSTRUCTIONS + f"Q: {question}\nA: {answer}"
