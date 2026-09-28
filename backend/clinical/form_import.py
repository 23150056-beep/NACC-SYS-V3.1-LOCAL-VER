"""An uploaded interview form, turned into a draft form template (28 Sep 2026).

The Clinical interview step offers the psychologist's form templates, and a
template is a list of fields. The only way to make one was to type every
question into its own row on another screen - and the agency's own interview
form (docs/agency-forms/Pre-assessment.docx) has two questionnaires and about
a hundred questions. This reads the file instead, with the reader reports
already use (clinical/services.py), and proposes the fields.

It saves nothing. The draft goes back to a review dialog, and the save is the
ordinary POST /form-templates/, attestation and all.

Design: docs/superpowers/specs/2026-09-28-interview-upload-and-closure-reasons-design.md
"""
import re

from clinical.services import HEADING_PREFIX

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_FIELDS = 300
# Longer than this, a line is an instruction to the interviewer, not a
# question to answer.
MAX_QUESTION_CHARS = 300

_BRACKETED = re.compile(r"\([^)]*\)")
_BLANKS = re.compile(r"[_…]{2,}|\.{4,}")


def _looks_like_title(line):
    """All capitals once any bracketed note is set aside. "ADOPTION
    PRE-ASSESSMENT QUESTIONNAIRE FOR THE CHILD (Some questions may not be
    answered depending on the child's age.)" is how the agency form starts its
    second questionnaire, and the note makes it too long for the report
    reader's heading rule."""
    bare = _BRACKETED.sub("", line).strip()
    letters = [c for c in bare if c.isalpha()]
    if len(letters) < 6 or len(bare.split()) > 14 or bare.endswith("?"):
        return False
    return sum(c.isupper() for c in letters) / len(letters) >= 0.8


def _field(label):
    """A question line as a field: "Name: ____" is short text (a date when it
    says so); anything else is answered in sentences."""
    blanked = _BLANKS.sub("", label).strip()
    if blanked.endswith(":") or blanked != label.strip():
        text = blanked.rstrip(":").strip()
        kind = "date" if "date" in text.lower() else "text"
        return {"label": text, "field_type": kind, "options": []}
    return {"label": label.strip(), "field_type": "long_text", "options": []}


def draft_from_text(text, fallback_title=""):
    """{title, body, fields, warnings} from extracted text - see the module
    docstring. `fields` is empty when nothing in the text reads as a question."""
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]

    def is_section(line):
        return line.startswith(HEADING_PREFIX) or _looks_like_title(line)

    has_sections = any(is_section(line) for line in lines)
    title, intro, fields, warnings = "", [], [], []
    seen = {}
    in_section = not has_sections       # no headings at all: every line is a question

    for line in lines:
        if is_section(line):
            label = line[len(HEADING_PREFIX):].strip() if line.startswith(HEADING_PREFIX) else line
            title = title or label
            fields.append({"label": label, "field_type": "section", "options": []})
            in_section = True
            continue
        if not in_section:
            intro.append(line)
            continue
        cells = [c.strip() for c in line.split(" | ")] if " | " in line else [line]
        for cell in cells:
            if not cell:
                continue
            if len(cell) > MAX_QUESTION_CHARS:
                intro.append(cell)
                continue
            field = _field(cell)
            if not field["label"]:
                continue
            # Answers are stored against the question's wording, so two
            # identical questions would share one answer.
            count = seen.get(field["label"], 0) + 1
            seen[field["label"]] = count
            if count > 1:
                field["label"] = f"{field['label']} ({count})"
            fields.append(field)

    if len(fields) > MAX_FIELDS:
        warnings.append(f"Only the first {MAX_FIELDS} fields were kept.")
        fields = fields[:MAX_FIELDS]
    # Sections alone are not a form: nothing to answer.
    if not any(f["field_type"] != "section" for f in fields):
        fields = []
    return {
        "title": (title or fallback_title)[:200],
        "body": "\n".join(intro),
        "fields": fields,
        "warnings": warnings,
    }
