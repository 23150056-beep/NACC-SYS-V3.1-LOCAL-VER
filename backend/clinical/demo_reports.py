"""Invented psychological reports, so the report features have something real
to work on.

Psychologists each keep their own report format, and the features built on
reports - reading them, checking them, summarising them - have to cope with
that. So these come in three layouts on purpose, one per child in turn:

* a Word file using Word's own heading styles and a details table;
* a Word file whose headings are just bold lines and whose details are
  "Label: value" lines, as a hand-built template often has them;
* a PDF with capitalised bold headings and long session notes - long enough
  that a summary has to choose what to read.

Each is written from the child's own invented record (problems, findings,
treatment plan, dated notes), goes through the real extractor, and is checked
by the real report check before it is saved - so what the demo shows is what
an upload would have produced.

One report, and only one, carries another child's full name in its background
section, the way a report started from someone else's does. It is there for
the check to find.

Each document says in its own first lines that it is invented.
"""
from types import SimpleNamespace

from django.core.files.base import ContentFile
from django.utils import timezone

from accounts.display import display_name
from children import intake
from clinical.demo_docx import build_docx
from clinical.models import PsychologicalReport
from clinical.report_check import check_for, name_parts
from clinical.reports import age_on
from clinical.services import extract_text

# How this module recognises its own work, as demo_referrals does.
DEMO_COVERAGE = "Demonstration report"

NOTICE = ("DEMONSTRATION DOCUMENT. This report is invented. It was generated for a "
          "fictional child so the demonstration system has reports to hold, and it "
          "records nothing about any real person. Do not treat it as a case record.")

# (layout, report type, title)
LAYOUTS = (
    ("styled", "initial", "Psychological Evaluation Report"),
    ("plain", "progress", "Psychological Progress Report"),
    ("pdf", "progress", "Case Progress Summary"),
)

# Invented, generic observations, to give the long layout its length.
OBSERVATIONS = (
    "Arrived on time with the house parent and separated without difficulty.",
    "Engaged in the drawing activity for most of the session and talked while drawing.",
    "Needed prompting to begin but stayed with the task once started.",
    "Spoke more freely toward the end of the session than at the start.",
    "Asked when the next visit would be before leaving.",
    "Eye contact was brief at first and improved as the session went on.",
    "Described the week at school in short answers and changed the subject twice.",
    "Chose the same board game as last time and explained the rules unprompted.",
    "Mood appeared settled; affect was congruent with what was being described.",
    "Talked about a friend from school and a disagreement that had since been resolved.",
    "Became quiet when home was mentioned and was not pressed on it.",
    "Completed the feelings chart and named two feelings from the past week.",
    "The house parent reported regular sleep and appetite during the week.",
    "Practised the breathing exercise introduced earlier without being reminded.",
    "Asked to finish the session a few minutes early and was allowed to.",
    "Brought a drawing from home and described each person in it.",
)


def install_reports(children, today=None):
    """Give each child one invented report, if they have none. Returns how
    many were made. Never overwrites: a report already there might be real."""
    today = today or timezone.localdate()
    children = list(children)
    leftovers = _plant_leftover(children)
    # Layouts turn over within each psychologist's own children. Turned over
    # across the list instead, they fell in step with the seeder's
    # round-robin of three psychologists, and each saw a single layout.
    turns = {}
    made = 0
    for index, child in enumerate(children):
        if PsychologicalReport.objects.filter(child=child).exists():
            continue
        turn = turns.get(child.assigned_psychologist_id, 0)
        turns[child.assigned_psychologist_id] = turn + 1
        layout, report_type, title = LAYOUTS[turn % len(LAYOUTS)]
        blocks = build_blocks(child, title, today, long_notes=layout == "pdf",
                              leftover=leftovers.get(child.pk), seed=index)
        if layout == "pdf":
            data, ext = build_pdf(blocks), "pdf"
        else:
            data, ext = build_docx(blocks, styled=layout == "styled"), "docx"
        slug = "".join(c if c.isalnum() else "-" for c in child.fullname).strip("-").lower()
        filename = f"{slug or child.pk}-{report_type}-report.{ext}"
        author = child.assigned_psychologist
        text = extract_text(ContentFile(data, name=filename))
        report = PsychologicalReport(
            child=child, author=author, original_filename=filename,
            report_type=report_type, coverage=DEMO_COVERAGE, extracted_text=text,
            check_findings=check_for(SimpleNamespace(user=author), text, child, today))
        report.file.save(filename, ContentFile(data), save=False)
        report.save()
        made += 1
    return made


def _plant_leftover(children):
    """{child pk: another child of the same psychologist}, for exactly one
    child - the first whose psychologist has a second child here."""
    by_psychologist = {}
    for child in children:
        by_psychologist.setdefault(child.assigned_psychologist_id, []).append(child)
    for child in children:
        mates = [c for c in by_psychologist.get(child.assigned_psychologist_id, [])
                 if c.pk != child.pk and all(name_parts(c))]
        if child.assigned_psychologist_id and mates:
            return {child.pk: mates[0]}
    return {}


def build_blocks(child, title, today, long_notes=False, leftover=None, seed=0):
    """The report, as (kind, content) blocks - see demo_docx.build_docx."""
    first = name_parts(child)[0] or child.fullname
    born = child.birth_date
    age = age_on(born, today)
    # Whichever date the case records: an admission, or a placement with a
    # custodian (children/intake.py).
    dated = intake.intake_date_field(child)
    admitted = getattr(child, dated)
    date_label = "Date of placement" if dated == intake.PLACEMENT else "Date of admission"
    case_type = child.case_type or "child welfare"
    blocks = [
        ("title", title),
        ("para", NOTICE),
        ("heading", "Identifying Information"),
        ("fields", [
            ("Name", child.fullname),
            ("Case reference", f"C-{child.pk:04d}"),
            ("Date of birth", f"{born:%B} {born.day}, {born.year}" if born else "Not recorded"),
            ("Age", str(age) if age is not None else "Not recorded"),
            ("Sex", child.gender or "Not recorded"),
            ("Case type", case_type),
            (date_label,
             f"{admitted:%B} {admitted.day}, {admitted.year}" if admitted else "Not recorded"),
            ("Psychologist", display_name(child.assigned_psychologist) or "Not assigned"),
        ]),
        ("heading", "Reason for Referral"),
        ("para", f"{first} was referred by the Municipal Social Welfare and Development "
                 "Office for psychosocial assessment and counselling support in connection "
                 f"with a {case_type.lower()} case."),
        ("heading", "Background Information"),
    ]
    background = (f"{first} has been in the agency's care since "
                  + (f"{admitted:%B} {admitted.year}" if admitted else "admission")
                  + ". Placement and guardianship details are kept in the child's own file "
                    "and are not repeated here.")
    if leftover is not None:
        # Left over from the report this one was started from.
        background += (f" As noted in the earlier evaluation, {leftover.fullname} "
                       "settled well into the routine of the house.")
    blocks.append(("para", background))

    problems = [p.description for p in child.problems.order_by("identified_on")]
    blocks += [("heading", "Behavioral Observations"),
               ("bullets", problems or ["No specific concerns have been logged."])]

    entries = list(child.result_entries.select_related("instrument").order_by("date"))
    procedures = [e.instrument.title for e in entries if e.instrument]
    blocks += [("heading", "Assessment Procedures"),
               ("bullets", procedures + ["Clinical interview with the child and the house parent"])]
    blocks.append(("heading", "Findings"))
    if entries:
        for e in entries:
            label = e.instrument.title if e.instrument else "Assessment"
            line = f"{label}: {e.summary}"
            if e.classification:
                line += f" Psychologist's classification: {e.classification}."
            blocks.append(("para", line))
    else:
        blocks.append(("para", "No findings have been recorded yet."))

    notes = list(child.remarks.order_by("date"))
    blocks.append(("heading", "Session Notes"))
    for i, note in enumerate(notes):
        line = f"{note.date:%d %b %Y}. {note.text}"
        if long_notes:
            # Nine per session: long enough that the whole report no longer
            # fits what a summary can read at once, as a real one often won't.
            picks = [OBSERVATIONS[(seed + i * 5 + k) % len(OBSERVATIONS)] for k in range(9)]
            line += " " + " ".join(picks)
        blocks.append(("para", line))
    if not notes:
        blocks.append(("para", "No sessions recorded yet."))

    plan = child.treatment_plans.order_by("-created_at").first()
    blocks.append(("heading", "Impressions and Recommendations"))
    if plan:
        blocks.append(("para", plan.objectives))
        blocks.append(("bullets", [plan.interventions or "Continue sessions as planned.",
                                   "Review at the next case conference."]))
    else:
        blocks.append(("para", "To follow once the assessment is complete."))
    return blocks


# --- PDF -------------------------------------------------------------------

_PAGE = (595, 842)        # A4, points
_MARGIN = 56


def _latin(text):
    """The built-in PDF fonts cover Latin-1; say the rest in ASCII."""
    for a, b in (("—", "-"), ("–", "-"), ("’", "'"), ("‘", "'"),
                 ("“", '"'), ("”", '"'), ("•", "-")):
        text = text.replace(a, b)
    return text.encode("latin-1", "replace").decode("latin-1")


def _wrap(text, font, size, width):
    import fitz
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and fitz.get_text_length(candidate, fontname=font, fontsize=size) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    return lines + ([current] if current else [])


def build_pdf(blocks):
    """A multi-page PDF of `blocks`, headings in bold capitals, as bytes."""
    import fitz
    doc = fitz.open()
    width = _PAGE[0] - 2 * _MARGIN
    state = {"page": None, "y": 0}

    def put(text, font, size, gap=0):
        for line in _wrap(_latin(text), font, size, width):
            if state["page"] is None or state["y"] + size > _PAGE[1] - _MARGIN:
                state["page"] = doc.new_page(width=_PAGE[0], height=_PAGE[1])
                state["y"] = _MARGIN + size
            state["page"].insert_text((_MARGIN, state["y"]), line, fontname=font, fontsize=size)
            state["y"] += size * 1.45
        state["y"] += gap

    for kind, content in blocks:
        if kind in ("title", "heading"):
            put(content.upper(), "hebo", 14 if kind == "title" else 11, gap=4)
        elif kind == "fields":
            for label, value in content:
                put(f"{label}: {value}", "helv", 10)
            state["y"] += 6
        elif kind == "bullets":
            for item in content:
                put(f"- {item}", "helv", 10)
            state["y"] += 6
        else:
            put(content, "helv", 10, gap=6)
    data = doc.tobytes()
    doc.close()
    return data
