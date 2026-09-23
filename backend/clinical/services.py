"""Document helpers: text extraction from the psychologist's own uploaded
reports (never from published instruments — those are not in the system).

Every extractor returns plain text with one paragraph per line, and marks a
line it takes for a section heading with a leading "## " — the convention the
agency form templates already use for their own headings. A report's sections
are what a summary, a search or a check can be pointed at, and psychologists
each lay theirs out differently, so the headings are found from what the file
itself says rather than from any one template.

Extraction is best-effort and never fatal: a file that cannot be read gives
'' and the upload still succeeds. Old binary Word files (.doc) are one of those
- reading them needs a converter this project does not carry.
"""
import io
import re
import zipfile
from xml.etree import ElementTree

MAX_CHARS = 200_000
# A .docx is a zip, and the zip says how large each part will be once
# inflated. A real report's document.xml is a few hundred kilobytes; this is
# generous for a long one and small enough that a zip bomb stops here.
MAX_DOCX_XML_BYTES = 25 * 1024 * 1024

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"

HEADING_PREFIX = "## "


def extract_text(django_file, name=None):
    """Text of an uploaded report or referral, by file type. '' when unreadable."""
    name = (name or getattr(django_file, "name", "") or "").lower()
    if name.endswith(".pdf"):
        return extract_pdf_text(django_file)
    if name.endswith(".docx"):
        return extract_docx_text(django_file)
    return ""


def readable(name):
    """Whether text can be read from a file of this name at all."""
    return (name or "").lower().endswith((".pdf", ".docx"))


def ensure_text(doc):
    """A stored report's or referral's text, reading the file once if it was
    uploaded before its type could be read (every .docx, until now).

    Done on demand rather than only by a backfill command, because the hosted
    copies have no shell to run one in. Saves what it finds; '' when the file
    still cannot be read.
    """
    if doc.extracted_text:
        return doc.extracted_text
    name = doc.original_filename or doc.file.name
    if not doc.file or not readable(name):
        return ""
    try:
        with doc.file.open("rb") as handle:
            text = extract_text(handle, name)
    except Exception:
        return ""
    if text:
        doc.extracted_text = text
        doc.save(update_fields=["extracted_text"])
    return text


_ROMAN = re.compile(r"^[IVXLC]+[.)]\s+(\S.*)$")
_SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of",
                "on", "or", "the", "to", "with", "ng", "sa", "mga"}


def _title_case(phrase):
    phrase = re.sub(r"\([^)]*\)", "", phrase)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", phrase)
             if w.lower() not in _SMALL_WORDS]
    return bool(words) and sum(w[0].isupper() for w in words) / len(words) >= 0.7


def looks_like_heading(text, bold=False):
    """A short line that is all bold, all capitals, or a Roman-numbered title,
    and is not a "Label: value" line or a sentence.

    Measured against the agency's own Word forms: the consent form marks every
    heading as a plain capitalised line ("I. PURPOSE OF THE PSYCHOLOGICAL
    EVALUATION") and the pre-assessment questionnaire as a plain Roman-numbered
    one ("I. Background Information") - neither uses a heading style or bold.
    Roman numerals only: "2. Attend school regularly" is a recommendation in a
    list, not a section.
    """
    text = text.strip()
    if not 3 <= len(text) <= 90 or len(text.split()) > 12:
        return False
    head, sep, rest = text.partition(":")
    if sep and rest.strip():
        return False                      # "Name: Maria Santos" is a field
    if text.endswith(("?", "!")):
        return False
    roman = _ROMAN.match(text)
    if text.endswith(".") and not (roman is None and re.fullmatch(r"[IVXLC]+\.", text)):
        return False                      # a sentence
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return False
    if roman and _title_case(roman.group(1)):
        return True
    capitals = sum(c.isupper() for c in letters) / len(letters)
    return bold or capitals >= 0.8


def _mark(text, heading):
    text = " ".join(text.split())
    if not text:
        return ""
    return HEADING_PREFIX + text.rstrip(":").strip() if heading else text


# --- PDF -------------------------------------------------------------------

def extract_pdf_text(django_file, max_chars=MAX_CHARS):
    """Extract text from an uploaded PDF using PyMuPDF, one line per line of
    the page, headings marked. Returns '' for unreadable files."""
    try:
        import fitz  # PyMuPDF
        django_file.seek(0)
        data = django_file.read()
        django_file.seek(0)
        out, size = [], 0
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                for line in _pdf_lines(page):
                    out.append(line)
                    size += len(line) + 1
                if size > max_chars:
                    break
        return "\n".join(out)[:max_chars].strip()
    except Exception:
        return ""


def _pdf_lines(page):
    """Lines of one page. A line whose text spans are all bold, or that is
    short and capitalised, is a heading."""
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in line["spans"])
            bold = all((s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower()
                       for s in spans)
            marked = _mark(text, looks_like_heading(text, bold))
            if marked:
                yield marked


# --- Word (.docx) ----------------------------------------------------------

def extract_docx_text(django_file, max_chars=MAX_CHARS):
    """Paragraphs and table rows of a .docx, in document order, headings
    marked. Standard library only: a .docx is a zip of XML.

    Refuses rather than parses anything unusual. A document.xml never carries
    a DOCTYPE, so one that does is not a Word file and is not read — which
    also rules out every entity-expansion trick, whatever the XML parser's own
    defences are on the machine this runs on.
    """
    try:
        django_file.seek(0)
        data = django_file.read()
        django_file.seek(0)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            body_xml = _read_part(zf, "word/document.xml")
            styles_xml = _read_part(zf, "word/styles.xml")
        if body_xml is None:
            return ""
        headings = _heading_styles(styles_xml)
        body = ElementTree.fromstring(body_xml).find(f"{_W}body")
        if body is None:
            return ""
        out, size = [], 0
        for line in _blocks(body, headings):
            out.append(line)
            size += len(line) + 1
            if size > max_chars:
                break
        return "\n".join(out)[:max_chars].strip()
    except Exception:
        return ""


_DECLARATION = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)


def _read_part(zf, name):
    """One part's bytes, or None when absent. Raises on anything oversized or
    carrying a declaration - the whole part is scanned, because padding can
    push a DOCTYPE past any fixed prefix, and Word escapes "<" in text, so a
    genuine part never contains one."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_DOCX_XML_BYTES:
        raise ValueError("part too large")
    with zf.open(info) as handle:
        data = handle.read(MAX_DOCX_XML_BYTES + 1)
    if len(data) > MAX_DOCX_XML_BYTES or _DECLARATION.search(data):
        raise ValueError("refused")
    return data


def _heading_styles(styles_xml):
    """Style ids that are headings. Word stores the id, which is localised
    ("berschrift1" in German Word), and the name, which is not ("heading 1"),
    so the name decides — or an outline level, which is what a heading is."""
    ids = set()
    if not styles_xml:
        return ids
    for style in ElementTree.fromstring(styles_xml).iter(f"{_W}style"):
        style_id = style.get(f"{_W}styleId")
        name = style.find(f"{_W}name")
        name = (name.get(f"{_W}val") if name is not None else "") or ""
        outline = style.find(f"{_W}pPr/{_W}outlineLvl")
        if (name.lower().startswith("heading") or name.lower() in ("title", "subtitle")
                or (outline is not None and outline.get(f"{_W}val", "9") != "9")):
            ids.add(style_id)
    return ids


def _blocks(element, headings):
    """Paragraphs and tables in document order, into content controls."""
    for child in element:
        if child.tag == f"{_W}p":
            line = _paragraph(child, headings)
            if line:
                yield line
        elif child.tag == f"{_W}tbl":
            for row in child.iter(f"{_W}tr"):
                cells = [" ".join(filter(None, (_paragraph(p, set(), plain=True)
                                                for p in cell.iter(f"{_W}p"))))
                         for cell in row.findall(f"{_W}tc")]
                cells = [c for c in cells if c]
                if cells:
                    yield " | ".join(cells)
        elif child.tag == f"{_W}sdt":
            content = child.find(f"{_W}sdtContent")
            if content is not None:
                yield from _blocks(content, headings)


def _paragraph(p, headings, plain=False):
    parts, bold_runs, runs_with_text = [], 0, 0
    for run in _runs(p):
        text = "".join(_run_text(run))
        if not text.strip():
            parts.append(text)
            continue
        runs_with_text += 1
        if _bold(run):
            bold_runs += 1
        parts.append(text)
    text = "".join(parts)
    if plain:
        return " ".join(text.split())
    ppr = p.find(f"{_W}pPr")
    styled = False
    if ppr is not None:
        style = ppr.find(f"{_W}pStyle")
        outline = ppr.find(f"{_W}outlineLvl")
        styled = ((style is not None and style.get(f"{_W}val") in headings)
                  or (outline is not None and outline.get(f"{_W}val", "9") != "9"))
    heading = styled or looks_like_heading(
        text, bold=runs_with_text > 0 and bold_runs == runs_with_text)
    return _mark(text, heading)


def _runs(p):
    """Runs in order, skipping the fallback copy Word writes beside some
    content — reading both would print the same words twice."""
    stack = list(p)[::-1]
    while stack:
        node = stack.pop()
        if node.tag == _MC_FALLBACK:
            continue
        if node.tag == f"{_W}r":
            yield node
            continue
        stack.extend(list(node)[::-1])


def _run_text(run):
    for node in run:
        if node.tag == f"{_W}t":
            yield node.text or ""
        elif node.tag == f"{_W}tab":
            yield " "
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            yield " "


def _bold(run):
    rpr = run.find(f"{_W}rPr")
    b = rpr.find(f"{_W}b") if rpr is not None else None
    return b is not None and b.get(f"{_W}val", "true").lower() not in ("0", "false", "off")
