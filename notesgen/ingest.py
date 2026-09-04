"""Turn whatever the user points at into a course directory.

Accepts three shapes:

- a `.zip` from the Udemy Transcript Extractor extension
- an already-extracted folder (at either the course level or its parent)
- a Udemy course URL, which is fetched via `udemy_fetch`

All three end up as a directory laid out the way `discover.py` expects:

    <course>/NN-Section Title/NN-Lecture Title.txt
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

UDEMY_URL = re.compile(r"^https?://(www\.)?udemy\.com/course/", re.IGNORECASE)
SECTION_DIR = re.compile(r"^\d+[-_. ]")


class IngestError(RuntimeError):
    pass


def is_url(source: str) -> bool:
    return source.lower().startswith(("http://", "https://"))


def looks_like_course(path: Path) -> bool:
    """True when `path` holds numbered section folders containing .txt files."""
    if not path.is_dir():
        return False
    for child in path.iterdir():
        if child.is_dir() and SECTION_DIR.match(child.name) and any(child.glob("*.txt")):
            return True
    return False


def _describe(path: Path) -> str:
    if not path.exists():
        return "it does not exist"
    if not path.is_dir():
        return "it is a file, not a directory"
    kids = sorted(p.name for p in path.iterdir() if not p.name.startswith("."))
    if not kids:
        return "it is empty"
    return "it contains: " + ", ".join(kids[:6]) + ("..." if len(kids) > 6 else "")


def descend_to_course(path: Path) -> Path:
    """Find the course root at `path`, or one level down.

    The extension's zip nests everything under a single folder named for the
    course, and users naturally point at either level. Accept both rather than
    making them guess.
    """
    if looks_like_course(path):
        return path

    subdirs = [p for p in path.iterdir() if p.is_dir()] if path.is_dir() else []
    candidates = [p for p in subdirs if looks_like_course(p)]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise IngestError(
            f"{path} contains {len(candidates)} course folders "
            f"({', '.join(c.name for c in candidates[:3])}...). "
            "Point --input at the one you want."
        )
    raise IngestError(
        f"no course transcripts found in {path} - {_describe(path)}.\n"
        "Expected numbered section folders holding .txt files, e.g. "
        "'01-Introduction/01-Welcome.txt'."
    )


def extract_zip(archive: Path, dest_root: Path) -> Path:
    if not zipfile.is_zipfile(archive):
        raise IngestError(f"{archive} is not a valid zip file")

    dest = dest_root / archive.stem
    # A stale partial extraction would silently mix old and new lectures.
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    root = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            # Refuse absolute paths and ../ traversal before writing anything.
            # Compare by path segments, not by string prefix: "/tmp/out-evil"
            # startswith "/tmp/out" but is a different directory.
            target = (dest / member).resolve()
            if target != root and root not in target.parents:
                raise IngestError(f"zip contains an unsafe path: {member}")
        zf.extractall(dest)

    return descend_to_course(dest)


def already_fetched(url: str, workdir: Path) -> Path | None:
    """A previously downloaded copy of this course, if there is one.

    Without this, a course URL in NOTESGEN_INPUT means every command -
    including `discover` - opens a browser and re-downloads 183 lectures.
    """
    from .udemy_fetch import SOURCE_MARKER, _slug

    slug = _slug(url)
    if not slug or not workdir.is_dir():
        return None
    for marker in workdir.glob(f"*/{SOURCE_MARKER}"):
        if marker.read_text(encoding="utf-8").strip() == slug:
            course = marker.parent
            if looks_like_course(course):
                return course
    return None


def resolve_input(
    source: str,
    workdir: Path,
    *,
    cdp_port: int | None = None,
    refetch: bool = False,
) -> Path:
    """Return the course directory for a zip, folder, or Udemy URL."""
    workdir.mkdir(parents=True, exist_ok=True)

    if is_url(source):
        if not UDEMY_URL.match(source):
            raise IngestError(
                f"{source} is not a Udemy course URL. "
                "Expected https://www.udemy.com/course/..."
            )

        if not refetch:
            existing = already_fetched(source, workdir)
            if existing is not None:
                return existing

        from . import udemy_fetch  # imported lazily: needs playwright

        return udemy_fetch.fetch(source, workdir, cdp_port=cdp_port)

    path = Path(source).expanduser()
    if not path.exists():
        raise IngestError(f"{path} does not exist")

    if path.suffix.lower() == ".zip":
        return extract_zip(path, workdir)

    return descend_to_course(path.resolve())
