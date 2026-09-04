"""One Markdown parser, shared by every output format.

Only the subset the note prompts actually emit is supported: headings,
bullet/numbered lists, fenced code, blockquotes, rules, and inline
bold/italic/code.

The .docx, HTML and plain-text renderers all consume `parse_blocks` and
`parse_inline` so they cannot drift apart — a fix to list handling or callout
detection lands in all three at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Literal

FENCE = re.compile(r"^\s*```(\w*)\s*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
NUMBERED = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
RULE = re.compile(r"^\s*([-*_])\1{2,}\s*$")
TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
# Order matters: a bold run may wrap code spans that themselves contain
# asterisks (`**`*args`/`**kwargs` are conventions**`), so the bold branch has
# to be able to swallow whole code spans rather than stopping at the first `*`.
CODE_SPAN = r"`[^`]+`"
INLINE = re.compile(
    r"("
    rf"\*\*(?=\S)(?:[^*`]|{CODE_SPAN})+?(?<=\S)\*\*"   # **bold**, may contain `code`
    rf"|__(?=\S)(?:[^_`]|{CODE_SPAN})+?(?<=\S)__"        # __bold__
    rf"|{CODE_SPAN}"                              # `code`
    rf"|(?<!\w)\*(?=\S)(?:[^*`\n]|{CODE_SPAN})+?(?<=\S)\*(?!\w)"  # *italic*
    r")"
)

# The grounding markers the prompts emit. Renderers highlight these rather
# than letting an honest "this was not captured" note read as body text.
CALLOUT = "[!]"

BlockKind = Literal[
    "heading", "code", "bullet", "number", "quote", "para", "table", "mermaid"
]
InlineKind = Literal["text", "bold", "italic", "code", "boldcode"]


@dataclass
class Block:
    kind: BlockKind
    text: str = ""
    level: int = 0          # heading level, or list depth
    lang: str = ""          # code fence language
    lines: list[str] | None = None  # code block contents
    rows: list[list[str]] | None = None  # table rows; first is the header

    @property
    def is_callout(self) -> bool:
        return CALLOUT in self.text

    @property
    def source(self) -> str:
        return "\n".join(self.lines or [])


def normalise_heading_levels(lines: list[str]) -> dict[int, int]:
    """Map the heading levels actually used onto a contiguous 1..n range.

    The lecture notes use `#` for the title then `###` for the four sections,
    which would leave a hole at level 2 and make document outlines look
    broken. Collapsing the observed levels fixes that without the prompts
    having to know about it.
    """
    used = sorted({len(m.group(1)) for line in lines if (m := HEADING.match(line))})
    return {level: i + 1 for i, level in enumerate(used)}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_blocks(markdown: str, level_map: dict[int, int] | None = None) -> Iterator[Block]:
    lines = markdown.splitlines()
    level_map = level_map if level_map is not None else normalise_heading_levels(lines)

    i = 0
    while i < len(lines):
        line = lines[i]

        fence = FENCE.match(line)
        if fence:
            body: list[str] = []
            i += 1
            while i < len(lines) and not FENCE.match(lines[i]):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence
            lang = fence.group(1)
            # Mermaid gets its own kind so renderers can draw it rather than
            # print its source: live in HTML, a rendered PNG in .docx.
            kind = "mermaid" if lang.lower() == "mermaid" else "code"
            yield Block(kind=kind, lang=lang, lines=body)
            continue

        if not line.strip() or RULE.match(line):
            i += 1
            continue

        m = HEADING.match(line)
        if m:
            depth = len(m.group(1))
            yield Block(kind="heading", text=m.group(2), level=level_map.get(depth, depth))
            i += 1
            continue

        m = QUOTE.match(line)
        if m:
            quoted = [m.group(1)]
            i += 1
            while i < len(lines) and (q := QUOTE.match(lines[i])):
                quoted.append(q.group(1))
                i += 1
            yield Block(kind="quote", text=" ".join(x for x in quoted if x.strip()))
            continue

        if TABLE_ROW.match(line) and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            rows = [_cells(line)]
            i += 2  # header and its separator
            while i < len(lines) and TABLE_ROW.match(lines[i]) and not TABLE_SEP.match(lines[i]):
                rows.append(_cells(lines[i]))
                i += 1
            yield Block(kind="table", rows=rows)
            continue

        m = BULLET.match(line)
        if m:
            yield Block(kind="bullet", text=m.group(2), level=min(len(m.group(1)) // 2, 2))
            i += 1
            continue

        m = NUMBERED.match(line)
        if m:
            yield Block(kind="number", text=m.group(3), level=min(len(m.group(1)) // 2, 2))
            i += 1
            continue

        yield Block(kind="para", text=line.strip())
        i += 1


def parse_inline(text: str) -> list[tuple[InlineKind, str]]:
    """Split a line into (kind, text) spans for bold, italic, code and plain."""
    spans: list[tuple[InlineKind, str]] = []
    for token in INLINE.split(text):
        if not token:
            continue
        if token.startswith("`") and token.endswith("`") and len(token) > 1:
            spans.append(("code", token[1:-1]))
        elif (token.startswith("**") and token.endswith("**")) or (
            token.startswith("__") and token.endswith("__")
        ):
            spans.extend(_nested(token[2:-2], "bold"))
        elif token.startswith("*") and token.endswith("*") and len(token) > 2:
            spans.extend(_nested(token[1:-1], "italic"))
        else:
            spans.append(("text", token))
    return spans


def _nested(inner: str, emphasis: InlineKind) -> list[tuple[InlineKind, str]]:
    """Emphasised text that may contain code spans, e.g. **`import *`**."""
    out: list[tuple[InlineKind, str]] = []
    for part in re.split(f"({CODE_SPAN})", inner):
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) > 1:
            out.append(("boldcode" if emphasis == "bold" else "code", part[1:-1]))
        else:
            out.append((emphasis, part))
    return out


def strip_inline(text: str) -> str:
    """The line with all inline markup removed — for plain-text output."""
    return "".join(t for _, t in parse_inline(text))
