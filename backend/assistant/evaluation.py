"""Detectors for scoring what the model actually wrote.

Pure functions over strings — no Django, no database, no model call — so they
are unit-testable on their own. That matters: the first attempt at a
hallucinated-name detector was swamped by markdown headings and looked like
evidence when it was noise. These have tests built from real captured output.

Used by `manage.py ai_eval`, which is a measuring instrument, not a test gate.
"""
import re
from collections import Counter

# A capitalised word is only interesting when the model set it *inside* a
# sentence. Every false positive in the first attempt — "Stands", "Has",
# "Changed", "Check" — sat at a line start or inside a **Title Case Heading:**.
# Requiring a lowercase word immediately before it removes that entire class,
# and keeps the real cases: "of Nakayuki's", "particularly Nakikisalamuha".
_MID_SENTENCE_CAP = re.compile(r"\b[a-z]+\s+([A-Z][a-z]{2,})\b")

_CAP_ANY = re.compile(r"\b([A-Z][a-z]{2,})\b")

# Capitalised in ordinary prose without being anybody's name.
_CALENDAR = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
}

# Tagalog function words and verb prefixes with no ordinary English meaning.
# "may" and "para" are deliberately excluded — both are common English, and a
# detector that flags them reports drift on clean output.
_TAGALOG_WORDS = {
    "ang", "ng", "mga", "sa", "hindi", "ako", "siya", "niya", "kanya",
    "kanila", "ito", "tungkol", "ngayon", "masyado", "kasi", "wala", "rin",
}
_TAGALOG_PREFIXES = ("nag-", "naka-", "naki-", "pag-", "nakiki")

# English reduplication reads as emphasis, not as a defect.
_REDUPLICATION_LINKS = {"and", "by", "after", "to", "upon", "for", "on"}


def invented_names(prompt, output):
    """Capitalised words the model introduced that the prompt never mentioned.

    A word already present in the prompt is cleared here even when the model
    has misused it — turning a Tagalog verb into a person, say. That misuse is
    a language-drift and readability problem, not an invented fact, and the
    other two detectors are what surface it.
    """
    known = {w.lower() for w in _CAP_ANY.findall(prompt)}
    known |= {w.lower() for w in re.findall(r"\b[a-z]{3,}\b", prompt)}

    found = []
    for word in _MID_SENTENCE_CAP.findall(output):
        low = word.lower()
        if low in known or low in _CALENDAR:
            continue
        if word not in found:
            found.append(word)
    return found


def repeated_lines(output):
    """Non-blank lines the model emitted more than once.

    Observed live: a brief that printed "**What Has Changed Recently:**" three
    times in one draft. Harmless to the record, but it reads as broken.
    """
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return [line for line, n in Counter(lines).items() if n > 1]


def repeated_phrases(output, window=2, min_length=4):
    """Words the model repeated inside a single sentence.

    `repeated_lines` compares whole lines, so it could not see
    "Nakikisalamuha na Nakikisalamuha" — a defect that appeared in this
    module's own evaluation output and went unflagged.

    Only words of `min_length` or more count: "at the end of the day" repeats
    "the" two words apart and is ordinary English. The window is deliberately
    tight — a word legitimately reused later in a sentence ("settling in well
    and mixing well") is not a stutter.
    """
    # Repetition only counts inside one segment. A word appearing in two
    # adjacent headings — "**Percival's Case Brief** **Case Status:**" — is
    # document structure, not a stutter, and counting across that boundary
    # reported a false 13% defect rate across 60 runs.
    found = []
    for segment in re.split(r"[.!?;:]|\*\*|\n", output):
        _scan_segment(segment, window, min_length, found)
    return found


def _scan_segment(segment, window, min_length, found):
    words = re.findall(r"\b[\w'-]+\b", segment)
    for i, word in enumerate(words):
        if len(word) < min_length:
            continue
        ahead = words[i + 1:i + 1 + window]
        lowered = [w.lower() for w in ahead]
        if word.lower() not in lowered:
            continue
        # "more and more", "step by step", "side by side" are idiomatic English
        # emphasis, not stutters. The first version of this detector flagged all
        # of them and put a false 13% defect rate into an evaluation run. A
        # foreign linker ("na") is deliberately not on the list, so a genuine
        # repeat across one is still caught.
        if lowered.index(word.lower()) == 1 and ahead[0].lower() in _REDUPLICATION_LINKS:
            continue
        if word not in found:
            found.append(word)
    return found


def language_drift(output):
    """Tagalog markers in output that was asked for in English.

    Remark polish is instructed to return clear professional English. Against
    Taglish case notes it has returned Tagalog instead — once garbled badly
    enough to lose the original meaning.
    """
    low = output.lower()
    hits = [w for w in sorted(_TAGALOG_WORDS)
            if re.search(rf"\b{re.escape(w)}\b", low)]
    hits += [p for p in _TAGALOG_PREFIXES if p in low]
    return hits


# --- dates, numbers and length (the case brief) -------------------------------
#
# The case brief is written from facts that carry every date in words and every
# "n days ago" already worked out, so the strongest check available is also the
# simplest: a date or a number in the draft that is not in the facts the model
# was given was made up. Both are string checks - no model, no database - and
# both compare against the FACTS, not the instructions: the instructions have
# digits of their own ("1 to 3", "150 words"), and counting those as given
# would let "2 sessions" through whenever the facts said three.

WORD_LIMIT = 150

_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december")
_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
                  "saturday", "sunday")

# Full names in any case, except May, which is also a verb: "may 5" is not a
# date. The short forms must be capitalised for the same reason ("sat 3 hours"),
# and only a short form takes a full stop ("Oct. 12"): "...in October. 2 more
# visits" is two sentences, not a date.
_MONTH_WORD = (r"(?:(?i:january|february|march|april|june|july|august|september|"
               r"october|november|december)|May|(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|"
               r"Oct|Nov|Dec)\.?)")
_WEEKDAY_WORD = (r"(?:(?i:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|"
                 r"(?:Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun)\.?)")
_ORDINAL = r"(?:st|nd|rd|th)?"
# Spaces and tabs only. A line break is the end of a thought, and "October.\n2."
# is a list marker on the next line, not the 2nd of October.
_SP = r"[ \t]+"

# "12 Oct", "12th of October 2026"
_DAY_MONTH = re.compile(
    rf"\b(?P<d>\d{{1,2}}){_ORDINAL}(?:{_SP}of)?{_SP}(?P<m>{_MONTH_WORD})(?!\w)"
    rf"(?:,?{_SP}(?P<y>\d{{4}})\b)?")
# "October 12", "Oct. 12th, 2026"
_MONTH_DAY = re.compile(
    rf"\b(?P<m>{_MONTH_WORD})(?!\w){_SP}(?P<d>\d{{1,2}}){_ORDINAL}\b"
    rf"(?:,?{_SP}(?P<y>\d{{4}})\b)?")
_ISO_DATE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
# "12/10/2026". Only with a year: "3/4" is as likely a fraction as a date.
_NUMERIC_DATE = re.compile(
    r"\b(?P<a>\d{1,2})[/.-](?P<b>\d{1,2})[/.-](?P<y>\d{4}|\d{2})\b")
# "Tuesday 14", "Tue, the 14th". Not "Tuesday 9 AM" or "Tuesday, 9:30".
_WEEKDAY_DAY = re.compile(
    rf"\b(?P<w>{_WEEKDAY_WORD})(?!\w),?{_SP}(?:the{_SP})?(?P<d>\d{{1,2}}){_ORDINAL}\b"
    r"(?![:\d]|\s*[AaPp]\.?[Mm]\b)")
_WEEKDAY_ALONE = re.compile(
    r"\b(?i:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b")


def _month_number(word):
    low = word.lower().rstrip(".")
    if low == "sept":
        return 9
    for number, name in enumerate(_MONTH_NAMES, 1):
        if low == name or (len(low) == 3 and name.startswith(low)):
            return number
    return None


def _weekday_number(word):
    low = word.lower()[:3]
    return next(i for i, name in enumerate(_WEEKDAY_NAMES) if name.startswith(low))


def _month_day_matches(text):
    """[(match, month, day)] for every "12 Oct", "October 12" and ISO date in
    `text` whose month and day can be a real date."""
    found = []
    for pattern in (_DAY_MONTH, _MONTH_DAY, _ISO_DATE):
        for m in pattern.finditer(text):
            month = int(m["m"]) if m["m"].isdigit() else _month_number(m["m"])
            day = int(m["d"])
            if month and 1 <= month <= 12 and 1 <= day <= 31:
                found.append((m, month, day))
    return found


def invented_dates(facts, output):
    """Dates the draft states that the facts do not contain.

    Compares what a date MEANS, not how it is written: "14 October 2026" in the
    facts clears "Oct 14", "October 14th" and "2026-10-14". A year, where the
    draft gives one and the facts gave one for that day, has to agree. A weekday
    with a day ("Tuesday the 14th") has to be that weekday and that day in the
    facts, and a weekday on its own has to be one the facts mention. A bare
    month is not checked ("may" and "march" are ordinary English), nor
    "tomorrow" or "next week": the facts give the number of days, and the
    draft is checked for numbers separately.

    Returns the offending strings, in the order they appear.
    """
    known, known_weekday_days, known_weekdays = {}, set(), set()
    for m, month, day in _month_day_matches(facts):
        years = known.setdefault((month, day), set())
        if m["y"]:
            years.add(int(m["y"]))
    for m in _WEEKDAY_DAY.finditer(facts):
        known_weekday_days.add((_weekday_number(m["w"]), int(m["d"])))
    for m in _WEEKDAY_ALONE.finditer(facts):
        known_weekdays.add(_weekday_number(m.group(0)))

    found = []

    def note(text):
        if text not in found:
            found.append(text)

    ordered = []
    for m, month, day in _month_day_matches(output):
        years = known.get((month, day))
        if years is None or (m["y"] and years and int(m["y"]) not in years):
            ordered.append((m.start(), m.group(0).rstrip(".")))
    for m in _NUMERIC_DATE.finditer(output):
        a, b = int(m["a"]), int(m["b"])
        readings = [(mo, dy) for mo, dy in ((a, b), (b, a))
                    if 1 <= mo <= 12 and 1 <= dy <= 31]
        if readings and m.group(0) not in facts and not any(r in known for r in readings):
            ordered.append((m.start(), m.group(0)))
    taken = []
    for m in _WEEKDAY_DAY.finditer(output):
        day = int(m["d"])
        if not 1 <= day <= 31:
            continue
        taken.append(m.span())
        if (_weekday_number(m["w"]), day) not in known_weekday_days:
            ordered.append((m.start(), m.group(0)))
    for m in _WEEKDAY_ALONE.finditer(output):
        inside = any(start <= m.start() < end for start, end in taken)
        if not inside and _weekday_number(m.group(0)) not in known_weekdays:
            ordered.append((m.start(), m.group(0)))
    for _, text in sorted(ordered):
        note(text)
    return found


# A numbered part of the draft: "1.", "2)", "**3.**" at the start of a line.
_LIST_MARKER = re.compile(r"(?m)^[ \t]*(?:[*_#>-]+[ \t]*)*\(?\d{1,2}[.)](?=[\s*])")
_DIGITS = re.compile(r"\d+")


def invented_numbers(facts, output):
    """Digit groups in the draft that are not in the facts.

    Compared as numbers, so "09" is the "9" of "9:30 AM". A list marker at the
    start of a line is not a number the draft states, and the digits of a date
    are judged by invented_dates() rather than twice. Returns the offending
    strings, in the order they appear.
    """
    known = {int(n) for n in _DIGITS.findall(facts)}
    text = _LIST_MARKER.sub(lambda m: " " * len(m.group(0)), output)
    for pattern in (_ISO_DATE, _NUMERIC_DATE):
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    found = []
    for digits in _DIGITS.findall(text):
        if int(digits) not in known and digits not in found:
            found.append(digits)
    return found


def words_over(output, limit=WORD_LIMIT):
    """How many words the draft has when it has more than `limit`, else 0."""
    n = len(output.split())
    return n if n > limit else 0
