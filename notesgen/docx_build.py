"""Render the generated Markdown into .docx files for Google Docs.

Only the Markdown subset the prompts actually produce is supported: headings,
bullet/numbered lists, fenced code, blockquotes, rules, and inline
bold/italic/code. Headings map onto Word heading styles, which is what makes
the Google Docs outline pane work after upload.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH as _ALIGN
from docx.shared import Inches, Pt, RGBColor

from .mdparse import normalise_heading_levels, parse_blocks, parse_inline

CODE_FONT = "Consolas"
CODE_SHADE = "F2F3F5"
CODE_COLOR = RGBColor(0x1A, 0x1A, 0x1A)
QUOTE_COLOR = RGBColor(0x6A, 0x4B, 0x00)



def _shade(paragraph, fill: str) -> None:
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill)
    paragraph._p.get_or_add_pPr().append(shd)


def _add_inline(paragraph, text: str, *, base_italic: bool = False) -> None:
    """Render **bold**, *italic* and `code` spans into a paragraph."""
    for kind, span in parse_inline(text):
        run = paragraph.add_run(span)
        if kind in ("code", "boldcode"):
            run.font.name = CODE_FONT
            run.font.size = Pt(9.5)
            run.font.color.rgb = CODE_COLOR
            run.bold = kind == "boldcode"
        elif kind == "bold":
            run.bold = True
        elif kind == "italic":
            run.italic = True
        if base_italic:
            run.italic = True


def _add_code_block(doc, code_lines: list[str]) -> None:
    for line in code_lines or [""]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.left_indent = Pt(18)
        run = p.add_run(line if line.strip() else " ")
        run.font.name = CODE_FONT
        run.font.size = Pt(9.5)
        run.font.color.rgb = CODE_COLOR
        _shade(p, CODE_SHADE)


def render_markdown(
    doc, markdown: str, level_map: dict[int, int] | None = None,
    image_cache: "Path | None" = None,
) -> None:
    for b in parse_blocks(markdown, level_map):
        if b.kind == "mermaid":
            _add_diagram(doc, b.source, image_cache)

        elif b.kind == "code":
            _add_code_block(doc, b.lines or [])
            doc.add_paragraph().paragraph_format.space_after = Pt(4)

        elif b.kind == "heading":
            heading = doc.add_heading("", level=min(b.level, 4))
            _add_inline(heading, b.text)

        elif b.kind == "table":
            rows = b.rows or []
            if rows:
                t = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                t.style = _style_or_default(doc, "Table Grid", "Normal Table")
                for ri, row in enumerate(rows):
                    for ci, cell in enumerate(row):
                        para = t.cell(ri, ci).paragraphs[0]
                        _add_inline(para, cell)
                        if ri == 0:
                            for run in para.runs:
                                run.bold = True
                doc.add_paragraph().paragraph_format.space_after = Pt(4)

        elif b.kind == "quote":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(18)
            p.paragraph_format.space_before = Pt(6)
            _add_inline(p, b.text)
            for run in p.runs:
                run.font.color.rgb = QUOTE_COLOR
            _shade(p, "FFF8E5")

        elif b.kind in ("bullet", "number"):
            base = "List Bullet" if b.kind == "bullet" else "List Number"
            style = base if b.level == 0 else f"{base} {b.level + 1}"
            p = doc.add_paragraph(style=_style_or_default(doc, style, base))
            _add_inline(p, b.text)

        else:
            _add_inline(doc.add_paragraph(), b.text)


def _add_diagram(doc, source: str, image_cache) -> None:
    """Insert a rendered diagram, or its source when no renderer is installed."""
    png = None
    if image_cache is not None:
        from .diagrams import render_png

        png = render_png(source, image_cache)

    if png is not None:
        p = doc.add_paragraph()
        p.alignment = _ALIGN.CENTER
        # 6" fits the default page with margins; python-docx scales height.
        p.add_run().add_picture(str(png), width=Inches(6.0))
        doc.add_paragraph().paragraph_format.space_after = Pt(4)
        return

    note = doc.add_paragraph()
    run = note.add_run(
        "[diagram - install mermaid-cli (npx @mermaid-js/mermaid-cli) to render, "
        "or paste the source below into mermaid.live]"
    )
    run.italic = True
    run.font.size = Pt(9)
    run.font.color.rgb = QUOTE_COLOR
    _add_code_block(doc, source.splitlines())
    doc.add_paragraph().paragraph_format.space_after = Pt(4)


def _style_or_default(doc, wanted: str, fallback: str) -> str:
    try:
        doc.styles[wanted]
        return wanted
    except KeyError:
        return fallback


def new_document(title: str, subtitle: str) -> "Document":
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)

    h = doc.add_heading(title, level=0)
    h.alignment = WD_ALIGN_PARAGRAPH.LEFT
    sub = doc.add_paragraph()
    run = sub.add_run(subtitle)
    run.italic = True
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    return doc


def build_section_doc(
    title: str,
    subtitle: str,
    parts: list[str],
    out_path: Path,
    image_cache: Path | None = None,
) -> Path:
    """Assemble one .docx from a list of Markdown documents."""
    combined = "\n\n".join(parts)
    level_map = normalise_heading_levels(combined.splitlines())

    doc = new_document(title, subtitle)
    for n, part in enumerate(parts):
        if n:
            doc.add_page_break()
        render_markdown(doc, part, level_map, image_cache)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
