"""Render notes as HTML, plain text, or concatenated Markdown.

All local string formatting — no model calls, no cost. Output format has no
bearing on token usage; only `generate` spends anything.
"""

from __future__ import annotations

import base64
import html as _html
import re
from pathlib import Path

from . import events
from .assemble import MAX_WORDS_PER_DOC, Doc, collect
from .discover import Section
from .mdparse import CALLOUT, parse_blocks, parse_inline, strip_inline

FORMATS = ("html", "txt", "md")

EXT = {"html": ".html", "txt": ".txt", "md": ".md"}
SUBDIR = {"html": "html", "txt": "txt", "md": "export-md"}

CSS = """\
body{margin:0;padding:0;
     font:16px/1.65 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a}
main{max-width:52em;padding:2.5em 1.5em}
h1{font-size:1.9em;margin:1.6em 0 .4em;padding-bottom:.2em;border-bottom:2px solid #d8dde3}
h2{font-size:1.4em;margin:1.5em 0 .4em;color:#12263f}
h3{font-size:1.15em;margin:1.3em 0 .3em;color:#33475b}
h4{font-size:1em;margin:1.1em 0 .3em;color:#55637a}
p{margin:.6em 0}
ul,ol{margin:.5em 0 .8em;padding-left:1.6em}
li{margin:.25em 0}
code{font:.9em/1.4 Consolas,Menlo,monospace;background:#f2f3f5;
     padding:.12em .35em;border-radius:3px}
pre{background:#f2f3f5;border:1px solid #e2e5e9;border-radius:5px;
    padding:.85em 1em;overflow-x:auto;margin:.8em 0}
pre code{background:none;padding:0;font-size:.88em;line-height:1.5}
table{border-collapse:collapse;margin:.9em 0;font-size:.95em}
th,td{border:1px solid #d8dde3;padding:.4em .7em;text-align:left;vertical-align:top}
th{background:#f2f3f5;font-weight:600}
pre.mermaid{background:#fff;border:1px solid #e2e5e9;text-align:center;padding:1.2em}
figure.diagram{margin:1.2em 0;text-align:center}
figure.diagram img{max-width:100%;border:1px solid #e2e5e9;border-radius:6px;background:#fff}

/* Navigation rail. Collapses above the content on narrow screens so the page
   still reads on a phone and inside Drive's preview pane. */
#toc{position:fixed;top:0;left:0;bottom:0;width:17em;overflow-y:auto;
     padding:1.5em 1em;background:#f7f8fa;border-right:1px solid #e2e5e9;font-size:.87em}
#toc .toc-title{font-weight:700;text-transform:uppercase;letter-spacing:.06em;
     font-size:.8em;color:#667085;margin-bottom:.8em}
#toc ul{list-style:none;margin:0;padding:0}
#toc li{margin:.15em 0}
#toc a{color:#33475b;text-decoration:none;display:block;padding:.2em .4em;border-radius:4px}
#toc a:hover{background:#e8ebef;color:#12263f}
#toc li.l1>a{font-weight:600;margin-top:.7em;color:#12263f}
#toc li.l2>a{padding-left:1.2em;color:#55637a}
main{margin-left:19em}
@media (max-width:900px){
  #toc{position:static;width:auto;height:auto;border-right:0;
       border-bottom:1px solid #e2e5e9;margin-bottom:1.5em}
  main{margin-left:0}
}
blockquote{margin:.9em 0;padding:.7em 1em;background:#fff8e5;
           border-left:4px solid #e0a800;color:#6a4b00}
blockquote p{margin:.2em 0}
.subtitle{color:#667085;font-style:italic;margin:-.4em 0 2em}
.doc+.doc{margin-top:3.5em;padding-top:2em;border-top:3px double #d8dde3}
"""

MERMAID_SCRIPT = (
    '<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js">'
    "</script>\n<script>mermaid.initialize({startOnLoad:true,theme:'neutral',"
    "flowchart:{htmlLabels:false}});</script>\n"
)

PASTE_NOTE = {
    "html": """\
# Pasting these notes into Word or Google Docs

1. Double-click any `.html` file — it opens in your browser.
2. Select all (Cmd+A), copy (Cmd+C).
3. Paste into Word or Google Docs.

Headings, bold text and code blocks arrive as **real formatting**, not as
`###` and `**` symbols. No .docx step needed.

`00 - Complete Course.html` holds every section in one file.
""",
    "txt": """\
# Plain-text notes

No markup at all — paste these anywhere: email, Notes, a ticket, a chat box,
another AI tool. Headings are underlined, code is indented.

Lines are deliberately not hard-wrapped, so text reflows to whatever width
you paste it into.

`00 - Complete Course.txt` holds every section in one file.
""",
    "md": """\
# Markdown notes

One file per section, rather than the per-lecture files in `../md/`. Best for
Notion, Obsidian, GitHub, or pasting back into an AI tool.

Note: pasted into Word these show literal `###` and `**` — use `../html/`
for that.

`00 - Complete Course.md` holds every section in one file.
""",
}


# --------------------------------------------------------------------------
# HTML


def _inline_html(text: str) -> str:
    out = []
    for kind, span in parse_inline(text):
        esc = _html.escape(span)
        if kind == "bold":
            out.append(f"<strong>{esc}</strong>")
        elif kind == "boldcode":
            out.append(f"<strong><code>{esc}</code></strong>")
        elif kind == "italic":
            out.append(f"<em>{esc}</em>")
        elif kind == "code":
            out.append(f"<code>{esc}</code>")
        else:
            out.append(esc)
    return "".join(out)


def _diagram_png(source: str, image_cache) -> str | None:
    """Return a data: URI for a rendered diagram, or None.

    Inlining the image keeps the page self-contained - no CDN, no JavaScript -
    which is what makes it render in Google Drive's preview and in any offline
    viewer.
    """
    if image_cache is None:
        return None
    from .diagrams import render_png

    path = render_png(source, Path(image_cache))
    if path is None:
        return None
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


def _slugify(text: str, used: set[str]) -> str:
    base = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    base = re.sub(r"\s+", "-", base) or "section"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


def _blocks_html(markdown: str, level_map, image_cache=None, toc=None, used=None) -> str:
    out: list[str] = []
    open_list: str | None = None

    def close_list():
        nonlocal open_list
        if open_list:
            out.append(f"</{open_list}>")
            open_list = None

    for b in parse_blocks(markdown, level_map):
        if b.kind in ("bullet", "number"):
            want = "ul" if b.kind == "bullet" else "ol"
            if open_list != want:
                close_list()
                out.append(f"<{want}>")
                open_list = want
            indent = f' style="margin-left:{b.level * 1.2}em"' if b.level else ""
            out.append(f"<li{indent}>{_inline_html(b.text)}</li>")
            continue

        close_list()
        if b.kind == "mermaid":
            png = _diagram_png(b.source, image_cache)
            if png:
                out.append(
                    f'<figure class="diagram"><img alt="diagram" src="{png}"></figure>'
                )
            else:
                out.append(f'<pre class="mermaid">{_html.escape(b.source)}</pre>')
        elif b.kind == "table":
            head, *body = b.rows or [[]]
            cells = "".join(f"<th>{_inline_html(c)}</th>" for c in head)
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline_html(c)}</td>" for c in r) + "</tr>"
                for r in body
            )
            out.append(f"<table><thead><tr>{cells}</tr></thead><tbody>{rows}</tbody></table>")
        elif b.kind == "heading":
            lvl = min(b.level, 4)
            if toc is not None and lvl <= 2:
                slug = _slugify(b.text, used if used is not None else set())
                toc.append((lvl, b.text, slug))
                out.append(f'<h{lvl} id="{slug}">{_inline_html(b.text)}</h{lvl}>')
            else:
                out.append(f"<h{lvl}>{_inline_html(b.text)}</h{lvl}>")
        elif b.kind == "code":
            body = _html.escape("\n".join(b.lines or []))
            out.append(f"<pre><code>{body}</code></pre>")
        elif b.kind == "quote":
            out.append(f"<blockquote><p>{_inline_html(b.text)}</p></blockquote>")
        else:
            out.append(f"<p>{_inline_html(b.text)}</p>")

    close_list()
    return "\n".join(out)


def render_html(doc: Doc, image_cache=None) -> str:
    from .mdparse import normalise_heading_levels

    level_map = normalise_heading_levels(doc.markdown.splitlines())
    toc: list[tuple[int, str, str]] = []
    used: set[str] = set()
    body = "\n".join(
        f'<div class="doc">{_blocks_html(part, level_map, image_cache, toc, used)}</div>'
        for part in doc.parts
    )

    nav = ""
    if len(toc) > 1:
        items = "".join(
            f'<li class="l{lvl}"><a href="#{slug}">{_html.escape(text)}</a></li>'
            for lvl, text, slug in toc
        )
        nav = f'<nav id="toc"><div class="toc-title">Contents</div><ul>{items}</ul></nav>'

    # mermaid.js is only pulled in when a diagram could not be pre-rendered.
    # With images inlined the page is fully self-contained, which is what lets
    # it display in Google Drive and offline.
    script = MERMAID_SCRIPT if 'class="mermaid"' in body else ""

    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{_html.escape(doc.title)}</title>\n<style>\n{CSS}</style>\n"
        f"</head><body>\n{nav}\n<main>\n<h1>{_html.escape(doc.title)}</h1>\n"
        f'<p class="subtitle">{_html.escape(doc.subtitle)}</p>\n{body}\n</main>\n'
        f"{script}</body></html>\n"
    )


# --------------------------------------------------------------------------
# Plain text


def render_txt(doc: Doc) -> str:
    from .mdparse import normalise_heading_levels

    level_map = normalise_heading_levels(doc.markdown.splitlines())
    out: list[str] = [doc.title.upper(), "=" * len(doc.title), doc.subtitle, ""]

    for part in doc.parts:
        for b in parse_blocks(part, level_map):
            if b.kind == "heading":
                text = strip_inline(b.text)
                if b.level == 1:
                    out += ["", text.upper(), "=" * len(text)]
                elif b.level == 2:
                    out += ["", text, "-" * len(text)]
                else:
                    out += ["", f"{text}:"]
            elif b.kind == "code":
                out.append("")
                out += [f"    {line}" for line in (b.lines or [])]
                out.append("")
            elif b.kind == "bullet":
                out.append(f"{'  ' * b.level}  - {strip_inline(b.text)}")
            elif b.kind == "number":
                out.append(f"{'  ' * b.level}  {strip_inline(b.text)}")
            elif b.kind == "mermaid":
                # Plain text cannot draw; show the source, which is readable
                # and can be pasted into any Mermaid renderer.
                out += ["", "  [diagram - paste into mermaid.live to view]", ""]
                out += [f"    {line}" for line in (b.lines or [])]
                out.append("")
            elif b.kind == "table":
                rows = [[strip_inline(c) for c in r] for r in (b.rows or [])]
                widths = [
                    max(len(r[i]) for r in rows if i < len(r))
                    for i in range(max((len(r) for r in rows), default=0))
                ]
                out.append("")
                for n, r in enumerate(rows):
                    out.append("  " + "  ".join(
                        c.ljust(widths[i]) for i, c in enumerate(r) if i < len(widths)
                    ).rstrip())
                    if n == 0:  # underline the header
                        out.append("  " + "  ".join("-" * w for w in widths))
                out.append("")
            elif b.kind == "quote":
                # Keep the [!] marker: it is the honest-gap signal.
                out += ["", f"  {CALLOUT if b.is_callout else '>'} "
                            f"{strip_inline(b.text).replace(CALLOUT, '').strip()}", ""]
            else:
                out += ["", strip_inline(b.text)]
        out += ["", "-" * 70, ""]

    # Collapse runs of blank lines the block loop can produce.
    cleaned: list[str] = []
    for line in out:
        if line.strip() or (cleaned and cleaned[-1].strip()):
            cleaned.append(line.rstrip())
    return "\n".join(cleaned).strip() + "\n"


# --------------------------------------------------------------------------
# Markdown


def render_md(doc: Doc) -> str:
    header = f"# {doc.title}\n\n*{doc.subtitle}*\n\n"
    return header + "\n\n---\n\n".join(p.strip() for p in doc.parts) + "\n"


RENDERERS = {"html": render_html, "txt": render_txt, "md": render_md}


def export(
    course: str,
    sections: list[Section],
    md_root: Path,
    out_root: Path,
    *,
    formats=FORMATS,
    max_words: int = MAX_WORDS_PER_DOC,
    whole_course: bool = True,
    image_cache: Path | None = None,
    progress: events.Progress | None = None,
) -> dict[str, list[Path]]:
    docs = collect(
        course, sections, md_root, max_words=max_words, whole_course=whole_course
    )
    written: dict[str, list[Path]] = {}

    total = len(docs) * len(tuple(formats))
    events.stage(progress, "export", "start", total=total)
    n = 0
    for fmt in formats:
        target = out_root / SUBDIR[fmt]
        target.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for doc in docs:
            path = target / (doc.basename + EXT[fmt])
            content = (
                render_html(doc, image_cache) if fmt == "html" else RENDERERS[fmt](doc)
            )
            path.write_text(content, encoding="utf-8")
            paths.append(path)
            n += 1
            events.unit(progress, "export", n, total, f"{fmt}: {doc.basename}")
        (target / "PASTE.md").write_text(PASTE_NOTE[fmt], encoding="utf-8")
        written[fmt] = paths

    return written
