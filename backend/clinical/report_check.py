"""Things to check before a report is filed against a child.

Psychologists write each report in their own format, and the quickest way to
write the next one is to open the last one and type over it. What survives
that is the failure this looks for: another child's name in the background
section, last month's case number in the header, an age or a birthday that
belongs to someone else. Filed against the wrong child, that is somebody's
personal information in another child's case file.

Deterministic on purpose - no model. Every finding points at text that is
actually in the document, so it can be checked in seconds, and it can be wrong
only by flagging something that turns out to be fine (a sibling named on
purpose). It never blocks: the psychologist decides.

Scope matters. The names compared against are the children the uploader can
already see - their own caseload, or every child for an administrator. Anything
wider would turn this into a way of asking "does the agency have a record for
this name?", which a psychologist has no business being told.
"""
import re
from datetime import datetime

from clinical.reports import age_on

MIN_TEXT = 300          # shorter than this, a missing name says nothing

# What each finding is, for the screen and for tests. Stable strings.
OTHER_CHILD = "other_child"
NAME_MISSING = "name_missing"
CASE_REFERENCE = "case_reference"
AGE = "age"
BIRTH_DATE = "birth_date"
SEX = "sex"

# A label at the start of a line or of a table cell ("Age: 9", "Age | 9").
_LABEL = r"(?im)(?:^|\|)[ \t]*{label}[ \t]*[:|\-–]?[ \t]*"
_AGE_LABEL = re.compile(_LABEL.format(label=r"(?:age|edad)") + r"(\d{1,2})\b")
_AGE_PHRASE = re.compile(
    r"(?i)\b(\d{1,2})[- ]years?[- ]old[ \t]+(?:girl|boy|child|female|male|minor)\b")
_BIRTH_LABEL = re.compile(_LABEL.format(
    label=r"(?:date[ \t]+of[ \t]+birth|birth[ \t]*date|birthday|dob|"
          r"petsa[ \t]+ng[ \t]+kapanganakan)") + r"([^\n|]+)")
_SEX_LABEL = re.compile(_LABEL.format(label=r"(?:sex|gender|kasarian)")
                        + r"(male|female|lalaki|babae)\b")
_CASE_REF = re.compile(r"\bC-(\d{1,7})\b")

_DATE_PATTERNS = (
    (re.compile(r"([A-Za-z]{3,9})\.?[ \t]+(\d{1,2}),?[ \t]+(\d{4})"), "mdy_name"),
    (re.compile(r"(\d{1,2})[ \t]+([A-Za-z]{3,9})\.?,?[ \t]+(\d{4})"), "dmy_name"),
    (re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"), "iso"),
    (re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})"), "numeric"),
)
_SEXES = {"male": "Male", "lalaki": "Male", "female": "Female", "babae": "Female"}


def check_for(request, text, child, today=None):
    """check_report against the other children `request.user` can see. The
    one place that scope is decided, for the upload form and the backfill
    alike; `request` only needs a `.user`."""
    from django.utils import timezone

    from accounts.scoping import scope_to_visible
    from children.models import Child

    if not text:
        return []
    others = scope_to_visible(Child.objects.exclude(pk=child.pk), request, path=None)
    return check_report(text, child, others.only("id", "fullname", "first_name", "last_name"),
                        today or timezone.localdate())


def check_report(text, child, others, today):
    """[{kind, message}] for `text` filed against `child`. `others` are the
    other children the uploader can see. Empty when nothing needs a look."""
    if not text:
        return []
    findings = []
    findings += _other_children(text, child, others)
    findings += _name_missing(text, child)
    findings += _case_references(text, child)
    findings += _age(text, child, today)
    findings += _birth_date(text, child)
    findings += _sex(text, child)
    return findings


def name_parts(child):
    """(first, last) from the separate fields, else from the full name."""
    words = (child.fullname or "").split()
    first = (child.first_name or "").strip() or (words[0] if words else "")
    last = (child.last_name or "").strip() or (words[-1] if len(words) > 1 else "")
    return first, last


def _full_name_pattern(first, last):
    """First ... Last with up to two middle names or initials between them, or
    "Last, First". Case-insensitive for the names themselves; the middle words
    must be capitalised, so "Maria went to Santos" is not a match."""
    f, l = re.escape(first), re.escape(last)
    middle = r"(?:\s+[A-Z][A-Za-z'.-]*){0,2}"
    return re.compile(rf"\b(?i:{f}){middle}\s+(?i:{l})\b|\b(?i:{l}),\s+(?i:{f})\b")


def _other_children(text, child, others):
    mine = tuple(p.lower() for p in name_parts(child))
    seen, out = set(), []
    for other in others:
        first, last = name_parts(other)
        if not first or not last or (first.lower(), last.lower()) == mine:
            continue    # no full name to look for, or indistinguishable
        if (first.lower(), last.lower()) in seen:
            continue
        if _full_name_pattern(first, last).search(text):
            seen.add((first.lower(), last.lower()))
            out.append({
                "kind": OTHER_CHILD,
                # Whose name it is, so a later reader who cannot see that
                # child is not shown it - see PsychologicalReportSerializer.
                "child": other.pk,
                "message": (f"Mentions {first} {last}, another child on record. If this "
                            "report began as someone else's, check nothing of theirs "
                            "was left in. A sibling named on purpose is fine."),
            })
    return out


def _name_missing(text, child):
    first, last = name_parts(child)
    if len(text) < MIN_TEXT or not (first or last):
        return []
    for word in (first, last):
        if word and re.search(rf"\b{re.escape(word)}\b", text, re.IGNORECASE):
            return []
    if first and last and re.search(
            rf"\b{re.escape(first[0])}\.\s?(?:[A-Z]\.\s?)?{re.escape(last[0])}\.",
            text):
        return []       # initials, as a confidential report may use
    return [{"kind": NAME_MISSING,
             "message": (f"{child.fullname}'s name does not appear anywhere in this "
                         "report. Check it is the right child's.")}]


def _case_references(text, child):
    wrong = sorted({int(n) for n in _CASE_REF.findall(text)} - {child.pk})
    if not wrong:
        return []
    refs = ", ".join(f"C-{n:04d}" for n in wrong)
    return [{"kind": CASE_REFERENCE,
             "message": (f"Mentions case {refs}, but it is being filed for "
                         f"C-{child.pk:04d}.")}]


def _age(text, child, today):
    age = age_on(child.birth_date, today)
    if age is None:
        return []
    stated = {int(n) for n in _AGE_LABEL.findall(text) + _AGE_PHRASE.findall(text)}
    # One year either way: a report may be filed after a birthday it predates.
    wrong = sorted(n for n in stated if abs(n - age) > 1)
    if not wrong:
        return []
    return [{"kind": AGE,
             "message": (f"Gives the age as {', '.join(map(str, wrong))}, but "
                         f"{child.fullname} is {age}.")}]


def _parse_dates(value):
    """Every date `value` could mean. Numeric dates are read both ways round,
    because both orders are written here, and a date is only wrong if it is
    wrong under every reading."""
    out = set()
    for pattern, kind in _DATE_PATTERNS:
        for m in pattern.finditer(value):
            a, b, c = m.groups()
            candidates = []
            if kind == "mdy_name":
                candidates = [(f"{a} {b} {c}", ("%B %d %Y", "%b %d %Y"))]
            elif kind == "dmy_name":
                candidates = [(f"{b} {a} {c}", ("%B %d %Y", "%b %d %Y"))]
            elif kind == "iso":
                candidates = [(f"{a}-{b}-{c}", ("%Y-%m-%d",))]
            else:
                candidates = [(f"{a}/{b}/{c}", ("%m/%d/%Y", "%d/%m/%Y"))]
            for raw, formats in candidates:
                for fmt in formats:
                    try:
                        out.add(datetime.strptime(raw, fmt).date())
                    except ValueError:
                        pass
    return out


def _birth_date(text, child):
    if not child.birth_date:
        return []
    for value in _BIRTH_LABEL.findall(text):
        dates = _parse_dates(value)
        if dates and child.birth_date not in dates:
            written = value.strip()[:40]
            return [{"kind": BIRTH_DATE,
                     "message": (f"Gives the date of birth as {written}, but "
                                 f"{child.fullname}'s is "
                                 f"{child.birth_date:%B} {child.birth_date.day}, "
                                 f"{child.birth_date.year}.")}]
    return []


def _sex(text, child):
    recorded = child.gender if child.gender in ("Male", "Female") else None
    if not recorded:
        return []
    stated = {_SEXES[s.lower()] for s in _SEX_LABEL.findall(text)}
    if stated and recorded not in stated:
        return [{"kind": SEX,
                 "message": (f"Gives the sex as {', '.join(sorted(stated))}, but "
                             f"{child.fullname} is recorded as {recorded}.")}]
    return []
