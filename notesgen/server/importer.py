"""Take transcripts captured by the Chrome extension and write a course tree.

The extension runs inside the user's logged-in browser, so it can read the
Udemy API without any of the Cloudflare and sign-in trouble that the Playwright
path exists to survive. What it sends is the same curriculum payload
`udemy_fetch` works from, with each lecture's caption file attached.

Two rules make the result interchangeable with a `notesgen fetch`:

- the tree is written by `coursetree.write_tree`, the same function the
  Playwright path uses, so the layout cannot drift;
- captions arrive as raw WebVTT and are converted here by `vtt.vtt_to_text`.
  A JavaScript reimplementation would be a second source of truth, and since
  `Lecture.body_hash()` is what the manifest resumes on, one differing space
  would silently invalidate every note already generated for that course.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .. import coursetree
from ..vtt import vtt_to_text

# A whole course of WebVTT is a few tens of megabytes. Well past that is a
# mistake or an attack, not a course.
MAX_PAYLOAD_BYTES = 200 * 1024 * 1024
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)


class ImportError_(ValueError):
    """Bad import payload."""


@dataclass
class ImportResult:
    course_dir: Path
    name: str
    lectures: int
    missing: int
    sections: int


def _body_for(item: dict, fmt: str) -> str | None:
    """The transcript text for one lecture item, or None if it has none."""
    if fmt == "text":
        text = item.get("text")
        return text.strip() or None if isinstance(text, str) else None

    raw = item.get("vtt")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return vtt_to_text(raw) or None


def import_course(payload: dict, input_dir: Path) -> ImportResult:
    """Write the captured transcripts into `input_dir` as a course directory."""
    title = (payload.get("title") or "").strip()
    if not title:
        raise ImportError_("payload is missing the course title")

    slug = (payload.get("slug") or "").strip()
    url = (payload.get("url") or "").strip()
    if slug and not SLUG.match(slug):
        raise ImportError_(f"implausible course slug: {slug!r}")

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ImportError_("payload has no curriculum items")

    fmt = (payload.get("format") or "vtt").lower()
    if fmt not in ("vtt", "text"):
        raise ImportError_(f"unknown transcript format: {fmt!r}")

    size = sum(len(i.get("vtt") or i.get("text") or "") for i in items if isinstance(i, dict))
    if size > MAX_PAYLOAD_BYTES:
        raise ImportError_(
            f"transcripts are {size // (1024 * 1024)} MB, over the "
            f"{MAX_PAYLOAD_BYTES // (1024 * 1024)} MB limit"
        )

    lectures = sum(1 for i in items if isinstance(i, dict) and i.get("_class") == "lecture")
    if not lectures:
        raise ImportError_("payload contains no lectures")

    input_dir.mkdir(parents=True, exist_ok=True)
    root, written, missing = coursetree.write_tree(
        title,
        [i for i in items if isinstance(i, dict)],
        input_dir,
        lambda item: _body_for(item, fmt),
    )

    # Without this marker every later command re-opens a browser to fetch a
    # course we already have.
    coursetree.mark_source(root, slug or url or title)

    sections = sum(1 for p in root.iterdir() if p.is_dir())
    return ImportResult(
        course_dir=root,
        name=title,
        lectures=written,
        missing=missing,
        sections=sections,
    )
