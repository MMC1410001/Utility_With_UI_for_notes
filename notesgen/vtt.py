"""Convert Udemy's WebVTT captions into the clean prose the notes need.

Udemy's auto-captions repeat each cue as it scrolls, so a naive concatenation
roughly doubles the text and confuses the model. This drops timing entirely
and de-duplicates, matching what the browser extension produces.
"""

from __future__ import annotations

import re

TIMING = re.compile(r"^\s*[\d:.]+\s*-->\s*[\d:.]+")
CUE_ID = re.compile(r"^\s*\d+\s*$")
TAGS = re.compile(r"</?[cvbi][^>]*>|<\d{2}:\d{2}:\d{2}[.\d]*>")


def vtt_to_text(vtt: str) -> str:
    lines: list[str] = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if (
            not line
            or line.upper().startswith("WEBVTT")
            or line.startswith(("NOTE", "STYLE", "REGION"))
            or TIMING.match(line)
            or CUE_ID.match(line)
        ):
            continue
        line = TAGS.sub("", line).strip()
        if not line:
            continue
        # Rolling captions repeat the previous cue verbatim; keep only new text.
        if lines and line == lines[-1]:
            continue
        lines.append(line)

    text = " ".join(lines)
    text = re.sub(r"\s+", " ", text).strip()
    # Break into paragraphs at sentence ends so the notes prompt sees structure
    # rather than one 5000-word line.
    return _paragraphs(text)


def _paragraphs(text: str, sentences_per_para: int = 5) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text)
    out, buf = [], []
    for part in parts:
        buf.append(part)
        if len(buf) >= sentences_per_para:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return "\n\n".join(out)
