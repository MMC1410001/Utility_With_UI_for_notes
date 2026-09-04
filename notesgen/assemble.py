"""Decide what goes into each output document, once, for every format.

Both the .docx builder and the html/txt/md exporter consume `collect()`, so
all formats split the course identically — section 03 becomes Part 1 / Part 2
everywhere or nowhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .discover import Section
from .generate import md_path, rollup_path

# Google Docs gets sluggish on very long documents, and a 48k-word section is
# unwieldy to paste anywhere. Section 03 of the reference course is 40
# lectures, so oversized sections are split.
MAX_WORDS_PER_DOC = 25_000


@dataclass
class Doc:
    """One output document, in whatever format the renderer produces."""

    title: str
    subtitle: str
    parts: list[str]                 # the source Markdown of each lecture
    basename: str                    # filename without extension
    section: Section | None = None
    meta: dict = field(default_factory=dict)

    @property
    def markdown(self) -> str:
        return "\n\n".join(self.parts)

    @property
    def words(self) -> int:
        return sum(len(p.split()) for p in self.parts)


def safe_name(name: str) -> str:
    return "".join(c for c in name if c not in '/\\:*?"<>|').strip()


def chunk_parts(parts: list[str], max_words: int) -> list[list[str]]:
    """Split a section's parts into documents of at most max_words each."""
    total = sum(len(p.split()) for p in parts)
    if total <= max_words or len(parts) < 2:
        return [parts]

    chunks: list[list[str]] = [[]]
    running = 0
    for part in parts:
        words = len(part.split())
        if running and running + words > max_words:
            chunks.append([])
            running = 0
        chunks[-1].append(part)
        running += words
    return [c for c in chunks if c]


def strip_leading_h1(text: str, title: str) -> str:
    """Drop a part's opening `# Title` when it just repeats the document title.

    The section overview opens with its own H1 naming the section, which would
    otherwise appear immediately under the document heading in every format.
    """
    stripped = text.lstrip()
    lines = stripped.splitlines()
    if lines and lines[0].startswith("# ") and lines[0][2:].strip() == title.strip():
        return stripped.split("\n", 1)[1].lstrip() if "\n" in stripped else ""
    return text


def diagrams_path(md_root: Path, section: Section) -> Path:
    return md_root / f"{section.idx}-{safe_name(section.title)}" / "_diagrams.md"


def section_parts(md_root: Path, section: Section) -> list[str]:
    """Overview, then diagrams, then each lecture in order."""
    parts: list[str] = []
    overview = rollup_path(md_root, section)
    if overview.exists():
        parts.append(overview.read_text(encoding="utf-8"))

    # Diagrams live in their own file so generating them never rewrites notes
    # that have already been paid for.
    diagrams = diagrams_path(md_root, section)
    if diagrams.exists():
        parts.append(diagrams.read_text(encoding="utf-8"))
    for lec in section.lectures:
        p = md_path(md_root, lec)
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return parts


def collect(
    course: str,
    sections: list[Section],
    md_root: Path,
    *,
    max_words: int = MAX_WORDS_PER_DOC,
    whole_course: bool = False,
) -> list[Doc]:
    """Every document to emit, in order: index, then sections."""
    docs: list[Doc] = []

    index_md = md_root / "_course-index.md"
    if index_md.exists():
        docs.append(
            Doc(
                title=course,
                subtitle="Master index",
                parts=[strip_leading_h1(index_md.read_text(encoding="utf-8"), course)],
                basename="00 - Course Index",
            )
        )

    everything: list[str] = []
    for section in sections:
        parts = section_parts(md_root, section)
        if not parts:
            continue
        everything.extend(parts)

        title = f"Section {section.idx}: {section.title}"
        subtitle = f"{course} - {len(section.lectures)} lectures"
        chunks = chunk_parts(parts, max_words)
        for n, chunk in enumerate(chunks, start=1):
            suffix = f" (Part {n} of {len(chunks)})" if len(chunks) > 1 else ""
            chunk = list(chunk)
            if chunk:
                # `everything` above kept the original, so the whole-course
                # file still gets its section dividers.
                chunk[0] = strip_leading_h1(chunk[0], title)
            docs.append(
                Doc(
                    title=title + suffix,
                    subtitle=subtitle,
                    parts=chunk,
                    basename=safe_name(f"{section.idx} - {section.title}{suffix}"),
                    section=section,
                )
            )

    if whole_course and everything:
        # Deliberately not chunked: this one exists to be searched as a single
        # file, and splitting it would defeat that.
        docs.append(
            Doc(
                title=course,
                subtitle=f"Complete course - {len(sections)} sections",
                parts=everything,
                basename="00 - Complete Course",
            )
        )

    return docs
