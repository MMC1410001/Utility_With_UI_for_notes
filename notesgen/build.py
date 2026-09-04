"""Assemble generated Markdown into the .docx files you upload to Drive."""

from __future__ import annotations

from pathlib import Path

from . import events
from .assemble import MAX_WORDS_PER_DOC, collect, section_parts
from .discover import Section
from .docx_build import build_section_doc

UPLOAD_NOTE = """\
# Uploading these notes to Google Docs

1. Open <https://drive.google.com> and create a folder for the course.
2. Drag every `.docx` file in this folder into it.
3. Double-click a file, then **File > Save as Google Docs** (or right-click >
   *Open with* > *Google Docs*). The heading hierarchy becomes the outline
   pane: **View > Show outline**.

Read `00 - Course Index.docx` first; it links the sections together.

Files marked with `[!]` inside the notes flag places where the lecture's
audio did not cover what was on screen, or where captions were missing
entirely. Those gaps are deliberate - nothing there was inferred.

Prefer to paste rather than upload? See `../html/` - open one in a browser,
select all, copy, and paste straight into Word or Google Docs.
"""


def build(
    course: str,
    sections: list[Section],
    md_root: Path,
    docx_root: Path,
    *,
    max_words: int = MAX_WORDS_PER_DOC,
    image_cache: Path | None = None,
    progress: events.Progress | None = None,
) -> list[Path]:
    docx_root.mkdir(parents=True, exist_ok=True)

    if image_cache is not None:
        from .diagrams import warm_renderer

        if not warm_renderer(image_cache):
            image_cache = None  # fall back to printing diagram source

    docs = collect(course, sections, md_root, max_words=max_words)
    events.stage(progress, "docx", "start", total=len(docs))
    written = []
    for n, doc in enumerate(docs, start=1):
        written.append(
            build_section_doc(
                doc.title,
                doc.subtitle,
                doc.parts,
                docx_root / (doc.basename + ".docx"),
                image_cache,
            )
        )
        events.unit(progress, "docx", n, len(docs), doc.basename)
    (docx_root / "UPLOAD.md").write_text(UPLOAD_NOTE, encoding="utf-8")
    return written


def build_single(
    course: str,
    sections: list[Section],
    md_root: Path,
    out_path: Path,
    *,
    image_cache: Path | None = None,
) -> Path:
    """One .docx containing the whole course, for the Google Docs upload.

    Deliberately not chunked: Docs navigation is the heading outline, so the
    sections need to live in one document to be one outline.
    """
    if image_cache is not None:
        from .diagrams import warm_renderer

        if not warm_renderer(image_cache):
            image_cache = None

    parts: list[str] = []
    index = md_root / "_course-index.md"
    if index.exists():
        parts.append(index.read_text(encoding="utf-8"))
    for section in sections:
        parts.extend(section_parts(md_root, section))

    if not parts:
        raise FileNotFoundError(f"no generated notes under {md_root}")

    return build_section_doc(
        course,
        f"Complete course - {len(sections)} sections",
        parts,
        out_path,
        image_cache,
    )
