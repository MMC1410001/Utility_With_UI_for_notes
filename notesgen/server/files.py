"""List and serve generated files, without handing out the whole filesystem."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import HTTPException
from starlette.responses import FileResponse

# Only things the pipeline actually produces. This keeps the internals of
# `diagram-images/` (a `.renderer-ready` marker, mermaid config) out of reach.
ALLOWED_SUFFIXES = {
    ".html", ".htm", ".md", ".txt", ".docx", ".pdf", ".png", ".json", ".zip",
}

# An exported page is Markdown that a language model wrote, rendered to HTML,
# and it legitimately loads mermaid from a CDN. Serving that inline from the
# UI's own origin would let generated content script the UI and read the API
# token. `sandbox allow-scripts` (without allow-same-origin) drops the page
# into an opaque origin: mermaid still runs, but it can touch nothing of ours.
SANDBOX_CSP = (
    "sandbox allow-scripts allow-popups; "
    "default-src 'none'; "
    "img-src data: blob:; "
    "style-src 'unsafe-inline'; "
    "font-src data:; "
    "script-src 'unsafe-inline' https://cdn.jsdelivr.net"
)


def course_root(output_root: Path, course: str) -> Path:
    """The output directory for one course, or 404."""
    root = output_root.resolve()
    target = (root / course).resolve()
    # `course` must name a direct child - not "..", not a nested path.
    if target.parent != root or not target.is_dir():
        raise HTTPException(status_code=404, detail="no such course")
    return target


def safe_file(output_root: Path, course: str, relative: str) -> Path:
    base = course_root(output_root, course)
    target = (base / relative).resolve()

    # Compare resolved paths by segment. A string prefix test would accept a
    # sibling directory whose name merely starts with the same characters.
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="path outside the course folder")

    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    if target.suffix.lower() not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=404, detail="not a downloadable file")
    return target


def serve(path: Path, *, inline: bool = False) -> FileResponse:
    media_type, _ = mimetypes.guess_type(path.name)
    headers = {"X-Content-Type-Options": "nosniff"}

    is_html = path.suffix.lower() in (".html", ".htm")
    if inline and is_html:
        headers["Content-Security-Policy"] = SANDBOX_CSP
        disposition = "inline"
    elif inline and path.suffix.lower() in (".pdf", ".png", ".txt", ".md"):
        disposition = "inline"
    else:
        disposition = "attachment"

    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        headers=headers,
        content_disposition_type=disposition,
        filename=path.name,
    )
