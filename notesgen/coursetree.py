"""Write a course's transcripts to disk in the layout `discover.py` expects.

Two callers produce the same tree from the same Udemy curriculum payload:

- `udemy_fetch`, which resolves each lecture's captions through a Playwright
  page as it walks the list;
- the local server, which receives captions already downloaded by the Chrome
  extension and only has to look them up.

They differ solely in *where the text comes from*, so that is the one thing
this module takes as an argument. Everything else - the numbering, the
filename sanitising, the header block, the combined file - has to stay
byte-identical between the two, because `discover.py` parses the result and a
course captured one way must be indistinguishable from the same course
captured the other way.

    <course>/NN-Section Title/NN-Lecture Title.txt
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

NO_TRANSCRIPT = "[No transcript available for this lecture]"

# Written beside a fetched course so later runs can recognise it and reuse it
# instead of opening a browser again.
SOURCE_MARKER = ".source"
HEADER = "Course: {course}\nChapter: {chapter}\nLecture: {lecture}\n" + "-" * 40 + "\n\n"

# The separator `HEADER` ends with, used to strip it back off again.
_HEADER_RULE = "-" * 40


def safe(name: str) -> str:
    name = "".join(c for c in name if c not in '/\\:*?"<>|').strip()
    return re.sub(r"\s+", " ", name) or "untitled"


def pick_caption(captions: list[dict]) -> str | None:
    """Prefer English, then anything - matching the extension's behaviour."""
    if not captions:
        return None
    for cap in captions:
        if str(cap.get("locale_id", "")).lower().startswith("en"):
            return cap.get("url")
    return captions[0].get("url")


def course_slug(url: str) -> str | None:
    m = re.search(r"/course/([^/?#]+)", url)
    return m.group(1) if m else None


def write_tree(
    course: str,
    items: Iterable[dict],
    workdir: Path,
    resolve_body: Callable[[dict], str | None],
    *,
    on_lecture: Callable[[int, int, str, bool], None] | None = None,
) -> tuple[Path, int, int]:
    """Lay out `items` (a Udemy curriculum list) as a course directory.

    `resolve_body` turns one lecture item into its transcript text, or None
    when there isn't one. `on_lecture(chapter_no, lecture_no, title, missing)`
    is called after each lecture is written, for progress reporting.

    Returns the course root, and how many lectures had and lacked transcripts.
    """
    root = workdir / safe(course)
    root.mkdir(parents=True, exist_ok=True)

    chapter_no = 0
    lecture_no = 0
    chapter_dir: Path | None = None
    chapter_title = "Course Content"
    written = missing = 0

    for item in items:
        kind = item.get("_class")
        if kind == "chapter":
            chapter_no += 1
            lecture_no = 0
            chapter_title = item.get("title") or f"Section {chapter_no}"
            chapter_dir = root / f"{chapter_no:02d}-{safe(chapter_title)}"
            chapter_dir.mkdir(parents=True, exist_ok=True)
            continue

        if kind != "lecture":
            continue  # quizzes, practice tests, assignments

        if chapter_dir is None:  # lectures before any chapter header
            chapter_no = 1
            chapter_dir = root / "01-Course Content"
            chapter_dir.mkdir(parents=True, exist_ok=True)

        lecture_no += 1
        title = item.get("title") or f"Lecture {lecture_no}"
        body = resolve_body(item)
        if body is None:
            body, absent = NO_TRANSCRIPT, True
            missing += 1
        else:
            absent = False
            written += 1

        path = chapter_dir / f"{lecture_no:02d}-{safe(title)}.txt"
        path.write_text(
            HEADER.format(course=course, chapter=chapter_title, lecture=title) + body + "\n",
            encoding="utf-8",
        )
        if on_lecture is not None:
            on_lecture(chapter_no, lecture_no, title, absent)

    write_combined(root, course)
    return root, written, missing


def write_combined(root: Path, course: str) -> None:
    """The extension also ships a single searchable file; match that."""
    parts = [
        f"TRANSCRIPT: {course}",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "=" * 80,
        "",
    ]
    for section in sorted(p for p in root.iterdir() if p.is_dir()):
        parts.append(f"## {section.name.split('-', 1)[-1]}\n")
        for lec in sorted(section.glob("*.txt")):
            body = lec.read_text(encoding="utf-8").split(_HEADER_RULE, 1)[-1].strip()
            parts.append(f"### {lec.stem.split('-', 1)[-1]}\n\n{body}\n")
    (root / "_full-transcript.txt").write_text("\n".join(parts), encoding="utf-8")


def mark_source(root: Path, url_or_slug: str) -> None:
    """Record which course this directory came from, so it is reused later."""
    (root / SOURCE_MARKER).write_text(
        course_slug(url_or_slug) or url_or_slug, encoding="utf-8"
    )
