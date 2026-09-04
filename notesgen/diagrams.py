"""Generate Mermaid diagrams for each section, and render them to images.

Runs over the notes that already exist rather than the transcripts, so adding
diagrams to a course costs ~27 calls instead of regenerating all 183 lectures.

Diagrams land in their own `_diagrams.md` per section; nothing already
generated is modified.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import engine, events, prompts
from .assemble import safe_name
from .discover import Section
from .generate import _collect_section_notes, _write
from .manifest import Manifest
from .mdparse import parse_blocks

NO_DIAGRAM = "_No diagram adds anything to this section._"

# Mermaid keywords that blow up the parser when used as a node id. The model
# reaches for `graph`, `end` and `state` naturally when describing these
# systems, so rename them rather than relying on the prompt alone.
RESERVED = {
    "graph", "end", "subgraph", "class", "classDef", "click", "style",
    "linkStyle", "direction", "flowchart", "state", "note", "call", "href",
}
QUOTED = re.compile(r'("[^"]*")')

# Lines that are Mermaid statements rather than node references. These keep
# their keywords: `flowchart LR`, a bare `end` closing a subgraph, `direction`.
DECLARATION = re.compile(
    r"^\s*(?:"
    r"(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b"
    r"|(?:sequenceDiagram|stateDiagram-v2|stateDiagram|classDiagram|erDiagram"
    r"|journey|gantt|pie|mindmap|timeline)\b"
    r"|(?:end|subgraph|direction|classDef|class|style|click|linkStyle|note"
    r"|state|participant|actor|autonumber)\b"
    r")",
    re.IGNORECASE,
)

MERMAID_CONFIG = '{"theme":"neutral","flowchart":{"htmlLabels":false}}'


def diagrams_path(md_root: Path, section: Section) -> Path:
    return md_root / f"{section.idx}-{safe_name(section.title)}" / "_diagrams.md"


def generate(
    course: str,
    sections: list[Section],
    md_root: Path,
    manifest: Manifest,
    *,
    model: str | None = None,
    workers: int = 3,
    force: bool = False,
    progress: events.Progress | None = None,
    should_cancel: events.ShouldCancel | None = None,
) -> dict:
    stats = {"generated": 0, "skipped": 0, "none": 0, "failed": 0}
    jobs = []

    for section in sections:
        notes, digest = _collect_section_notes(md_root, section)
        if not notes.strip():
            continue
        key = f"__diagrams__/{section.idx}"
        if not force and manifest.is_current(key, digest):
            stats["skipped"] += 1
            continue
        jobs.append((section, notes, digest, key))

    if not jobs:
        return stats

    print(f"  {len(jobs)} section(s) to diagram")
    events.stage(progress, "diagrams", "start", total=len(jobs))

    def work(job):
        section, notes, digest, key = job
        prompt = prompts.diagram_prompt(course, section, notes)
        return job, engine.call(prompt, model=model)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, j): j for j in jobs}
        done = 0
        for fut in as_completed(futures):
            section, _, digest, key = futures[fut]
            done += 1
            events.check(should_cancel)
            try:
                _, result = fut.result()
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                print(f"  FAILED diagrams {section.idx}: {exc}")
                events.unit(progress, "diagrams", done, len(jobs),
                            f"Section {section.idx}", failed=str(exc))
                continue

            text = _sanitize_document(result.text.strip())
            n = len(_mermaid_blocks(text))
            if not n:
                stats["none"] += 1
                text = NO_DIAGRAM

            out = diagrams_path(md_root, section)
            _write(out, f"### Diagrams\n\n{text}\n")
            manifest.record(
                key,
                hash=digest,
                output=str(out.relative_to(md_root.parent)),
                status="ok",
                diagrams=n,
                cost_usd=result.cost_usd,
            )
            stats["generated"] += 1
            print(f"  {section.idx}-{section.title[:44]}  ({n} diagram(s))")
            events.unit(progress, "diagrams", done, len(jobs),
                        f"{section.idx} {section.title}", diagrams=n,
                        cost_usd=result.cost_usd)

    return stats


def _sanitize_document(markdown: str) -> str:
    """Run `sanitize` over every mermaid fence in a generated document."""
    out, in_fence, buf = [], False, []
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            if in_fence:
                out.append(sanitize("\n".join(buf)))
                buf, in_fence = [], False
            else:
                in_fence = line.strip().lower().startswith("```mermaid")
            out.append(line)
            continue
        (buf if in_fence else out).append(line)
    if buf:
        out.append(sanitize("\n".join(buf)))
    return "\n".join(out)


def sanitize(source: str) -> str:
    """Rename reserved-word node ids, leaving declarations and labels alone.

    `graph["compiled graph"]` is a node id and breaks the parser; `flowchart LR`
    and a bare `end` closing a subgraph are statements and must survive
    untouched. The difference is whether the keyword heads a declaration.
    """
    out_lines = []
    for line in source.splitlines():
        if DECLARATION.match(line):
            out_lines.append(line)
            continue
        # Split on quoted spans so label text is never rewritten - only ids.
        parts = QUOTED.split(line)
        for i, part in enumerate(parts):
            if part.startswith('"'):
                continue
            for word in RESERVED:
                part = re.sub(rf"(?<![\w\"]){re.escape(word)}(?![\w\"])", f"{word}Node", part)
            parts[i] = part
        out_lines.append("".join(parts))
    return "\n".join(out_lines)


def _mermaid_blocks(markdown: str) -> list[str]:
    return [b.source for b in parse_blocks(markdown) if b.kind == "mermaid"]


# --------------------------------------------------------------------------
# Rendering to PNG, for .docx and Google Docs


def renderer_available() -> bool:
    return shutil.which("mmdc") is not None or shutil.which("npx") is not None


def warm_renderer(cache_dir: Path) -> bool:
    """Make sure mermaid-cli is installed before rendering a batch.

    `npx` downloads the package (and a Chromium) on first use, which can take
    several minutes. Without this, that download eats the per-diagram timeout
    and the first diagram of every fresh machine is silently dropped.
    """
    if shutil.which("mmdc") or not shutil.which("npx"):
        return renderer_available()

    cache_dir.mkdir(parents=True, exist_ok=True)
    if (cache_dir / ".renderer-ready").exists():
        return True

    print("  fetching mermaid-cli (first run only, may take a few minutes)...")
    try:
        proc = subprocess.run(
            ["npx", "-y", "@mermaid-js/mermaid-cli", "--version"],
            capture_output=True, text=True, timeout=900,
        )
    except (subprocess.TimeoutExpired, OSError):
        print("  could not install mermaid-cli; .docx will show diagram source")
        return False

    if proc.returncode != 0:
        print("  could not install mermaid-cli; .docx will show diagram source")
        return False

    (cache_dir / ".renderer-ready").write_text(proc.stdout.strip(), encoding="utf-8")
    print(f"  mermaid-cli ready ({proc.stdout.strip()})")
    return True


def render_png(source: str, cache_dir: Path) -> Path | None:
    """Render one Mermaid diagram to PNG, cached by content hash.

    Returns None when no renderer is installed, so callers can fall back to
    showing the diagram source rather than failing the build.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    out = cache_dir / f"{digest}.png"
    if out.exists():
        return out

    mmdc = shutil.which("mmdc")
    cmd = [mmdc] if mmdc else (["npx", "-y", "@mermaid-js/mermaid-cli"] if shutil.which("npx") else None)
    if cmd is None:
        return None

    src = cache_dir / f"{digest}.mmd"
    src.write_text(source, encoding="utf-8")
    config = cache_dir / "mermaid-config.json"
    if not config.exists():
        config.write_text(MERMAID_CONFIG, encoding="utf-8")

    try:
        proc = subprocess.run(
            [*cmd, "-i", str(src), "-o", str(out), "-c", str(config),
             "-b", "white", "-w", "1400"],
            capture_output=True, text=True, timeout=180,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if proc.returncode != 0 or not out.exists():
        # A diagram that fails to render must not abort the whole document.
        return None
    return out
