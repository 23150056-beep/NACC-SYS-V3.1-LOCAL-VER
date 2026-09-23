"""A small, valid .docx, assembled by hand - the Word counterpart of demo_pdf.

A .docx is a zip holding a handful of XML parts. Five are enough for Word and
LibreOffice to open one: the content-type map, the package relationships, the
document, its relationships, and the styles it names. Nothing here is fancy,
because the point is to look like what psychologists actually send: headings,
paragraphs, a "Label | value" table and a few bullets.

`styled=True` marks headings with Word's own "heading 1" style. `styled=False`
writes them as plain bold paragraphs, which is how a hand-built template often
does it - and exactly what the text extractor has to recognise without help.
"""
import io
import zipfile
from xml.sax.saxutils import escape

_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

_PACKAGE_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {_NS}>
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/><w:sz w:val="22"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
</w:styles>"""


def _run(text, bold=False):
    props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(text, style=None, bold=False):
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{props}{_run(text, bold)}</w:p>"


def _table(rows):
    border = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
    borders = "".join(border.format(side) for side in
                      ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = [f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>{borders}'
           '</w:tblBorders></w:tblPr><w:tblGrid><w:gridCol w:w="3200"/>'
           '<w:gridCol w:w="6200"/></w:tblGrid>']
    for label, value in rows:
        out.append("<w:tr>"
                   f'<w:tc><w:tcPr><w:tcW w:w="3200" w:type="dxa"/></w:tcPr>{_para(label, bold=True)}</w:tc>'
                   f'<w:tc><w:tcPr><w:tcW w:w="6200" w:type="dxa"/></w:tcPr>{_para(value)}</w:tc>'
                   "</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def build_docx(blocks, styled=True):
    """A .docx of `blocks`, as bytes. Each block is (kind, content):
    ("title", text), ("heading", text), ("para", text), ("fields", [(label,
    value), ...]) or ("bullets", [text, ...])."""
    body = []
    for kind, content in blocks:
        if kind == "title":
            body.append(_para(content, style="Title" if styled else None, bold=not styled))
        elif kind == "heading":
            body.append(_para(content, style="Heading1") if styled
                        else _para(content, bold=True))
        elif kind == "fields":
            if styled:
                body.append(_table(content))
            else:
                body.extend(_para(f"{label}: {value}") for label, value in content)
        elif kind == "bullets":
            body.extend(_para(f"• {item}") for item in content)
        else:
            body.append(_para(content))
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                f"<w:document {_NS}><w:body>{''.join(body)}"
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>'
                "</w:sectPr></w:body></w:document>")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _PACKAGE_RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS)
        zf.writestr("word/styles.xml", _STYLES)
        zf.writestr("word/document.xml", document)
    return out.getvalue()
