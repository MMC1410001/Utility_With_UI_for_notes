"""Run the pipeline without argparse in the way.

`cli.cmd_run` used to be the only orchestrator, and it worked by mutating the
argparse namespace between stages (`args.format = ...`, `args.publish = ...`)
and reporting by printing. That is fine for one caller and impossible for two,
so the sequence lives here instead and both the CLI and the web server drive
it through the same two functions:

    course = pipeline.resolve(source, input_dir, output_dir)
    result = pipeline.run(course, options, progress=..., should_cancel=...)

Nothing here talks to a terminal or an HTTP request. The stages still print as
they always did - the CLI's output is unchanged - but everything a caller
needs to render a UI comes back in `RunResult`.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from . import build as build_mod
from . import diagrams as diagrams_mod
from . import events
from . import export as export_mod
from . import gdocs
from . import generate as gen
from . import ingest
from . import links as links_mod
from . import outputs as outputs_mod
from . import pdf as pdf_mod
from .assemble import MAX_WORDS_PER_DOC
from .discover import course_name, discover, flatten
from .glossary import Glossary
from .manifest import Manifest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "output"
DEFAULT_INPUT = PROJECT_ROOT / "input"
DEFAULT_GDOCS_CONFIG = PROJECT_ROOT / ".gdocs"

# Stages that spend money, in the order they run. Everything else is free to
# redo, which is worth telling the user.
PAID_STAGES = ("lectures", "rollups", "index", "diagrams")


class PipelineError(RuntimeError):
    pass


# Outputs that need a working Google token. A plain `pdf` does not: it is
# rendered locally from the HTML by headless Chrome.
DRIVE_OUTPUTS = ("gdoc", "drive-html", "drive-pdf")


def check_drive_auth(wanted, config_dir: Path | None = None) -> None:
    """Fail before generating if the run ends in an upload we can't make.

    Generating a 183-lecture course takes the better part of an hour and costs
    real money. Discovering at the upload step that nobody ever authorised
    Drive is the worst possible time to find out.
    """
    if not any(o in wanted for o in DRIVE_OUTPUTS):
        return
    config_dir = Path(config_dir or DEFAULT_GDOCS_CONFIG).expanduser()
    state = gdocs.auth_status(config_dir)
    if state["valid"] or state["refreshable"]:
        return
    raise PipelineError(
        (state.get("help") or "Google Drive is not connected.")
        + "\n  Then run this again - nothing has been generated yet."
    )


@dataclass
class CourseRun:
    """Where one course's inputs and outputs live."""

    course_dir: Path
    name: str
    root: Path
    md_root: Path
    docx_root: Path
    manifest_path: Path

    @property
    def image_cache(self) -> Path:
        return self.root / "diagram-images"


@dataclass
class RunOptions:
    outputs: tuple[str, ...] = outputs_mod.DEFAULT
    model: str = "sonnet"
    workers: int = 3
    force: bool = False
    no_rollup: bool = False
    sections: list[str] | None = None
    only: str | None = None
    max_words: int = MAX_WORDS_PER_DOC
    no_images: bool = False
    split_sections: bool = False
    whole_course: bool = True
    glossary: Path | None = None
    config_dir: Path | None = None
    keep_going: bool = False

    @property
    def filtered(self) -> bool:
        """True when only part of the course is being processed."""
        return bool(self.sections or self.only)


@dataclass
class Artifact:
    kind: str          # html | docx | txt | md | pdf | gdoc | ...
    name: str
    path: Path | None = None
    size: int = 0
    url: str | None = None


@dataclass
class RunResult:
    course: str
    outputs: tuple[str, ...] = ()
    stats: dict = field(default_factory=dict)
    artifacts: list[Artifact] = field(default_factory=list)
    links: list = field(default_factory=list)
    cost_usd: float = 0.0        # spent by THIS run
    cost_total_usd: float = 0.0  # spent on this course, ever
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed and not self.errors and not self.cancelled


# ---------------------------------------------------------------------------
# resolving a course


def resolve(
    source: str,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
    *,
    cdp_port: int | None = None,
    refetch: bool = False,
) -> CourseRun:
    """Turn a zip, folder, or Udemy URL into the paths for that course."""
    input_dir = Path(input_dir or DEFAULT_INPUT).expanduser().resolve()
    output_dir = Path(output_dir or DEFAULT_OUTPUT).expanduser().resolve()

    course_dir = ingest.resolve_input(
        source, input_dir, cdp_port=cdp_port, refetch=refetch
    )
    root = output_dir / course_dir.name
    return CourseRun(
        course_dir=course_dir,
        name=course_name(course_dir),
        root=root,
        md_root=root / "md",
        docx_root=root / "docx",
        manifest_path=root / "manifest.json",
    )


def select_sections(course_dir: Path, sections=None, only=None):
    """The sections to work on, after --section / --only filtering."""
    found = discover(course_dir)
    if sections:
        wanted = {s.lstrip("0") for s in sections}
        found = [s for s in found if s.idx.lstrip("0") in wanted]
    if only:
        kept = []
        for s in found:
            lectures = [l for l in s.lectures if fnmatch.fnmatch(l.slug, only)]
            if lectures:
                s.lectures = lectures
                kept.append(s)
        found = kept
    return found


def whole_course_html(root: Path) -> Path | None:
    """The single-file web page for the course, or the first section's."""
    html_dir = root / "html"
    if not html_dir.is_dir():
        return None
    combined = html_dir / "00 - Complete Course.html"
    if combined.exists():
        return combined
    pages = sorted(html_dir.glob("*.html"))
    return pages[0] if pages else None


# ---------------------------------------------------------------------------
# the run


def run(
    course: CourseRun,
    opts: RunOptions,
    *,
    progress: events.Progress | None = None,
    should_cancel: events.ShouldCancel | None = None,
) -> RunResult:
    """Generate notes and produce everything in `opts.outputs`."""
    wanted = opts.outputs or outputs_mod.DEFAULT
    result = RunResult(course=course.name, outputs=wanted)

    sections = select_sections(course.course_dir, opts.sections, opts.only)
    if not sections:
        raise PipelineError("no lectures matched the section/only filter")

    check_drive_auth(wanted, opts.config_dir)

    course.root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(course.manifest_path)
    # The manifest accumulates across runs, so the lifetime total says nothing
    # about what this run cost. Re-exporting an already-generated course is
    # free, and the UI must be able to say so.
    spent_before = manifest.total_cost
    image_cache = None if opts.no_images else course.image_cache

    try:
        _generate(course, sections, manifest, opts, result, progress, should_cancel)

        if "diagrams" in wanted:
            events.check(should_cancel)
            _diagrams(course, sections, manifest, opts, result, progress, should_cancel)

        if "docx" in wanted:
            events.check(should_cancel)
            _docx(course, sections, opts, result, image_cache, progress)

        export_formats = [f for f in ("html", "txt", "md") if f in wanted]
        if export_formats:
            events.check(should_cancel)
            _export(course, sections, opts, result, image_cache, export_formats, progress)

        publish = [o for o in ("gdoc", "drive-html", "pdf", "drive-pdf") if o in wanted]
        if publish:
            events.check(should_cancel)
            _publish(course, sections, manifest, opts, result, image_cache,
                     publish, progress)
    except events.Cancelled:
        result.cancelled = True

    result.cost_total_usd = manifest.total_cost
    result.cost_usd = max(0.0, result.cost_total_usd - spent_before)
    result.links = links_mod.collect(manifest)
    result.artifacts = collect_artifacts(course, manifest)
    return result


def _generate(course, sections, manifest, opts, result, progress, should_cancel):
    events.stage(progress, "generate", "start")
    glossary = Glossary.load(Path(opts.glossary) if opts.glossary else None)

    print(f"\nGenerating notes for {course.name}")
    stats = gen.generate_lectures(
        course.name, sections, course.md_root, manifest, glossary,
        model=opts.model, workers=opts.workers, force=opts.force,
        progress=progress, should_cancel=should_cancel,
    )
    result.stats["lectures"] = stats
    result.failed += stats.get("failed", 0)
    print(f"\n  lectures: {stats}")

    if stats.get("failed") and not opts.keep_going:
        raise PipelineError(
            f"{stats['failed']} lecture(s) failed - re-run to retry only those"
        )

    if opts.no_rollup:
        return

    rollups = gen.generate_rollups(
        course.name, sections, course.md_root, manifest,
        model=opts.model, workers=opts.workers, force=opts.force,
        progress=progress, should_cancel=should_cancel,
    )
    result.stats["rollups"] = rollups
    result.failed += rollups.get("failed", 0)
    print(f"  rollups:  {rollups}")

    # A course index built over a filtered subset would be misleading.
    if not opts.filtered:
        gen.generate_course_index(
            course.name, sections, course.md_root, manifest,
            model=opts.model, force=opts.force, progress=progress,
        )
    events.stage(progress, "generate", "done")


def _diagrams(course, sections, manifest, opts, result, progress, should_cancel):
    if not course.md_root.exists():
        return
    print(f"\nGenerating diagrams for {course.name}")
    if not diagrams_mod.renderer_available():
        print("  note: no mermaid renderer found. Diagrams will render live in")
        print("  the HTML, but .docx will show their source instead.")
        print("  Fix with: npm install -g @mermaid-js/mermaid-cli\n")

    stats = diagrams_mod.generate(
        course.name, sections, course.md_root, manifest,
        model=opts.model, workers=opts.workers, force=opts.force,
        progress=progress, should_cancel=should_cancel,
    )
    result.stats["diagrams"] = stats
    result.failed += stats.get("failed", 0)
    print(f"\n  diagrams: {stats}")
    events.stage(progress, "diagrams", "done")


def _docx(course, sections, opts, result, image_cache, progress):
    written = build_mod.build(
        course.name, sections, course.md_root, course.docx_root,
        max_words=opts.max_words, image_cache=image_cache, progress=progress,
    )
    result.stats["docx"] = {"written": len(written)}
    print(f"\n  {len(written)} document(s) in {course.docx_root}")
    events.stage(progress, "docx", "done")


def _export(course, sections, opts, result, image_cache, formats, progress):
    written = export_mod.export(
        course.name, sections, course.md_root, course.root,
        formats=tuple(formats), max_words=opts.max_words,
        whole_course=opts.whole_course, image_cache=image_cache,
        progress=progress,
    )
    result.stats["export"] = {f: len(p) for f, p in written.items()}
    print()
    for fmt, paths in written.items():
        print(f"  {len(paths):>3} {fmt:<5} -> {course.root / export_mod.SUBDIR[fmt]}")
    events.stage(progress, "export", "done")


def _publish(course, sections, manifest, opts, result, image_cache, publish, progress):
    """Upload to Drive and render the PDF - mirrors the old `cmd_push`."""
    config_dir = Path(opts.config_dir or DEFAULT_GDOCS_CONFIG).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    name = course.name
    events.stage(progress, "publish", "start", total=len(publish))
    step = 0

    try:
        if "drive-html" in publish:
            step += 1
            page = whole_course_html(course.root)
            if page is None:
                result.errors.append("no HTML to upload; export it first")
            else:
                print(f"\n  Uploading {page.name} ({page.stat().st_size // 1024} KB)...")
                url = gdocs.push_file(
                    page, f"{name} (web page)", config_dir, manifest,
                    mime=gdocs.HTML_MIME, convert=False,
                    folder_name=name, key=f"__gdoc__/{name} (web page)",
                )
                print(f"  {url}")
                events.unit(progress, "publish", step, len(publish), "web page", url=url)

        if "pdf" in publish:
            step += 1
            page = whole_course_html(course.root)
            pdf_path = course.root / "pdf" / f"{name}.pdf"
            if page is None:
                result.errors.append("no HTML to render a PDF from; export it first")
            else:
                print(f"\n  Rendering PDF from {page.name}...")
                try:
                    pdf_mod.from_html(page, pdf_path)
                    print(f"  {pdf_path}  ({pdf_path.stat().st_size // 1024} KB)")
                    events.unit(progress, "publish", step, len(publish), "PDF")
                except pdf_mod.PdfError as exc:
                    result.errors.append(str(exc))
                    pdf_path = None

                if pdf_path and "drive-pdf" in publish:
                    url = gdocs.push_file(
                        pdf_path, f"{name} (PDF)", config_dir, manifest,
                        mime=gdocs.PDF_MIME, convert=False,
                        folder_name=name, key=f"__gdoc__/{name} (PDF)",
                    )
                    print(f"  {url}")

        if "gdoc" in publish:
            step += 1
            if opts.split_sections:
                print(f"\nPushing {len(sections)} section documents to Google Docs")
                for doc in build_mod.collect(
                    name, sections, course.md_root, max_words=opts.max_words
                ):
                    path = course.docx_root / (doc.basename + ".docx")
                    if not path.exists():
                        path = build_mod.build_section_doc(
                            doc.title, doc.subtitle, doc.parts, path, image_cache
                        )
                    url = gdocs.push(
                        path, f"{name} - {doc.title}", config_dir, manifest,
                        folder_name=name, key=f"__gdoc__/{doc.basename}",
                    )
                    print(f"  {doc.basename}\n    {url}")
            else:
                print("\nBuilding one document for the whole course...")
                combined = course.root / "gdocs" / f"{name}.docx"
                combined.parent.mkdir(parents=True, exist_ok=True)
                build_mod.build_single(
                    name, sections, course.md_root, combined, image_cache=image_cache
                )
                print(f"  {combined.stat().st_size // 1024} KB, uploading...")
                url = gdocs.push(combined, name, config_dir, manifest, folder_name=name)
                print(f"\n  {url}")
                print("  Navigate it with View > Show outline.")
                events.unit(progress, "publish", step, len(publish), "Google Doc", url=url)
    except gdocs.GDocsError as exc:
        result.errors.append(str(exc))

    found = links_mod.collect(manifest)
    written = links_mod.write_file(course.root, name, found)
    if written:
        print(f"\n  Saved these links to {written}")
    events.stage(progress, "publish", "done")


# ---------------------------------------------------------------------------
# what came out


# folder -> what a reader should expect to find in it
FOLDERS = {
    "html": "open in a browser; also copies into Word with formatting",
    "docx": "Word documents",
    "txt": "plain text, pastes anywhere",
    "export-md": "Markdown per section",
    "pdf": "PDF, rendered from the web page",
    "gdocs": "the single document uploaded to Drive",
}

# Files that explain a folder rather than being part of the notes.
_NOTES_ONLY = {"PASTE.md", "UPLOAD.md", "LINKS.md"}


def collect_artifacts(course: CourseRun, manifest: Manifest | None = None) -> list[Artifact]:
    """Every produced file, plus any Drive links, for a UI to list."""
    found: list[Artifact] = []
    for folder in FOLDERS:
        directory = course.root / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.name in _NOTES_ONLY:
                continue
            found.append(
                Artifact(
                    kind=folder,
                    name=path.name,
                    path=path,
                    size=path.stat().st_size,
                )
            )

    if manifest is not None:
        for label, url in links_mod.collect(manifest):
            found.append(Artifact(kind="gdoc", name=label, url=url))
    return found


def stats_summary(result: RunResult) -> str:
    """One line per stage, for a terminal or a log pane."""
    lines = []
    for stage_name, stats in result.stats.items():
        if isinstance(stats, dict):
            body = ", ".join(f"{k} {v}" for k, v in stats.items() if v)
            lines.append(f"  {stage_name:<10} {body or 'nothing to do'}")
    if result.cost_usd:
        lines.append(f"  {'cost':<10} ${result.cost_usd:.2f}")
    return "\n".join(lines)
