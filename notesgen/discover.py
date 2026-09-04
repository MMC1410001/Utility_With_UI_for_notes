"""Walk a course transcript tree and turn it into Lecture records.

Expected layout, as produced by the transcript extractor:

    <course>/
        NN-Section Title/
            NN-Lecture Title.txt
        _full-transcript.txt        (ignored)

Each lecture file opens with a header block we strip off and keep as metadata:

    Course: ...
    Chapter: ...
    Lecture: ...
    ----------------------------------------
    <blank>
    <body>
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

# Below this many words the transcript is missing or truncated, not just short.
# The smallest real lecture in the reference course is 304 words; the broken
# ones are all under 35.
MIN_USABLE_WORDS = 200

NO_TRANSCRIPT_MARKER = "[No transcript available"

# "11-Getting Started With LangGraph" -> ("11", "Getting Started With LangGraph")
NUMBERED = re.compile(r"^(\d+)[-_. ]+(.*)$")

STATUS_OK = "ok"
STATUS_NO_TRANSCRIPT = "no_transcript"


@dataclass
class Lecture:
    path: Path
    section_idx: str
    section_title: str
    lec_idx: str
    lec_title: str
    body: str
    header: dict = field(default_factory=dict)

    @property
    def words(self) -> int:
        return len(self.body.split())

    @property
    def status(self) -> str:
        if NO_TRANSCRIPT_MARKER in self.body:
            return STATUS_NO_TRANSCRIPT
        if self.words < MIN_USABLE_WORDS:
            return STATUS_NO_TRANSCRIPT
        return STATUS_OK

    @property
    def slug(self) -> str:
        """Stable identity for the manifest, e.g. '11-Getting.../01-Intro...'."""
        return f"{self.path.parent.name}/{self.path.stem}"

    @property
    def label(self) -> str:
        """Human-facing reference, e.g. '11.1 Introduction To LangGraph'."""
        return f"{int(self.section_idx)}.{int(self.lec_idx)} {self.lec_title}"

    def body_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


@dataclass
class Section:
    idx: str
    title: str
    lectures: list[Lecture]

    @property
    def dir_name(self) -> str:
        return f"{self.idx}-{self.title}"

    @property
    def words(self) -> int:
        return sum(lec.words for lec in self.lectures)


def _split_numbered(name: str) -> tuple[str, str]:
    m = NUMBERED.match(name)
    if m:
        return m.group(1), m.group(2).strip()
    return "", name.strip()


def parse_lecture(path: Path) -> Lecture:
    raw = path.read_text(encoding="utf-8", errors="replace")

    header: dict[str, str] = {}
    body = raw
    # The header ends at the dashed rule; anything before it is `Key: value`.
    if "\n---" in raw[:600]:
        head, _, rest = raw.partition("\n---")
        for line in head.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                header[key.strip().lower()] = value.strip()
        # Drop the remainder of the dashed rule line, then leading blanks.
        body = rest.split("\n", 1)[1] if "\n" in rest else ""

    section_idx, section_dir_title = _split_numbered(path.parent.name)
    lec_idx, lec_file_title = _split_numbered(path.stem)

    return Lecture(
        path=path,
        section_idx=section_idx,
        # Prefer the header's titles; they keep punctuation the filename lost.
        section_title=header.get("chapter") or section_dir_title,
        lec_idx=lec_idx,
        lec_title=header.get("lecture") or lec_file_title,
        body=body.strip(),
        header=header,
    )


def discover(course_dir: Path) -> list[Section]:
    """Return the course's sections, ordered, each with ordered lectures."""
    if not course_dir.is_dir():
        raise NotADirectoryError(f"course directory not found: {course_dir}")

    sections: list[Section] = []
    for section_dir in sorted(p for p in course_dir.iterdir() if p.is_dir()):
        files = sorted(
            p
            for p in section_dir.glob("*.txt")
            # Files starting with "_" are aggregates, not lectures.
            if not p.name.startswith("_")
        )
        if not files:
            continue
        lectures = [parse_lecture(p) for p in files]
        idx, title = _split_numbered(section_dir.name)
        sections.append(
            Section(idx=idx, title=lectures[0].section_title or title, lectures=lectures)
        )
    return sections


def course_name(course_dir: Path) -> str:
    """The course title, taken from a lecture header when available."""
    for section_dir in sorted(p for p in course_dir.iterdir() if p.is_dir()):
        for p in sorted(section_dir.glob("*.txt")):
            if p.name.startswith("_"):
                continue
            name = parse_lecture(p).header.get("course")
            if name:
                return name
    return course_dir.name


def flatten(sections: list[Section]) -> list[Lecture]:
    return [lec for s in sections for lec in s.lectures]
