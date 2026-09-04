"""Command line entry point.

    python -m notesgen discover --course "<path>"
    python -m notesgen generate --course "<path>" --section 11
    python -m notesgen build    --course "<path>"
    python -m notesgen run      --course "<path>"
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from pathlib import Path

from . import build as build_mod
from . import diagrams as diagrams_mod
from . import engine
from . import export as export_mod
from . import gdocs
from . import generate as gen
from . import ingest
from . import links as links_mod
from . import outputs as outputs_mod
from . import pdf as pdf_mod
from . import pipeline
from .providers import base as providers_base
from . import setup_cmd
from .discover import STATUS_NO_TRANSCRIPT, course_name, discover, flatten
from .glossary import Glossary
from .manifest import Manifest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "output"
DEFAULT_INPUT = PROJECT_ROOT / "input"


def _select(sections, section_filter, only):
    if section_filter:
        wanted = {s.lstrip("0") for s in section_filter}
        sections = [s for s in sections if s.idx.lstrip("0") in wanted]
    if only:
        kept = []
        for s in sections:
            lectures = [l for l in s.lectures if fnmatch.fnmatch(l.slug, only)]
            if lectures:
                s.lectures = lectures
                kept.append(s)
        sections = kept
    return sections


def _paths(args):
    """Resolve the input (zip, folder or URL) into a course directory."""
    source = getattr(args, "input", None) or getattr(args, "course", None)
    if not source:
        raise SystemExit("--input is required (a .zip, a folder, or a Udemy course URL)")

    course_dir = ingest.resolve_input(
        source,
        Path(args.input_dir).expanduser().resolve(),
        cdp_port=getattr(args, "attach", None),
        refetch=getattr(args, "refetch", False),
    )
    name = course_name(course_dir)
    root = Path(args.output).expanduser().resolve() / course_dir.name
    return course_dir, name, root, root / "md", root / "docx", root / "manifest.json"


def cmd_discover(args) -> int:
    course_dir, name, _root, _md, _docx, manifest_path = _paths(args)
    sections = _select(discover(course_dir), args.section, args.only)
    lectures = flatten(sections)
    missing = [l for l in lectures if l.status == STATUS_NO_TRANSCRIPT]

    print(f"\n{name}")
    print(f"{course_dir}\n")
    for s in sections:
        bad = sum(1 for l in s.lectures if l.status == STATUS_NO_TRANSCRIPT)
        flag = f"   <-- {bad} without transcript" if bad else ""
        print(f"  {s.idx}  {s.title[:52]:<52} {len(s.lectures):>3} lec  {s.words:>7,} words{flag}")

    print(f"\n  {len(sections)} sections, {len(lectures)} lectures, "
          f"{sum(l.words for l in lectures):,} words")

    if manifest_path.exists():
        links_mod.show(links_mod.collect(Manifest(manifest_path)))
    if missing:
        print(f"\n  {len(missing)} lecture(s) without a usable transcript "
              f"(stubbed, never sent to the model):")
        for l in missing:
            print(f"    {l.slug}  ({l.words} words)")

    if manifest_path.exists():
        links_mod.show(links_mod.collect(Manifest(manifest_path)))
    return 0


def cmd_generate(args) -> int:
    course_dir, name, root, md_root, _, manifest_path = _paths(args)
    sections = _select(discover(course_dir), args.section, args.only)
    if not sections:
        print("no lectures matched", file=sys.stderr)
        return 1

    root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(manifest_path)
    glossary = Glossary.load(Path(args.glossary) if args.glossary else None)

    print(f"\nGenerating notes for {name}")
    stats = gen.generate_lectures(
        name, sections, md_root, manifest, glossary,
        model=args.model, workers=args.workers, force=args.force,
    )
    print(f"\n  lectures: {stats}")

    if not args.no_rollup:
        r = gen.generate_rollups(
            name, sections, md_root, manifest,
            model=args.model, workers=args.workers, force=args.force,
        )
        print(f"  rollups:  {r}")
        # A course index over a filtered subset would be misleading.
        if not (args.section or args.only):
            gen.generate_course_index(
                name, sections, md_root, manifest, model=args.model, force=args.force
            )

    print(f"\n  markdown in {md_root}")
    if stats.get("failed"):
        print(f"  {stats['failed']} failed - re-run the same command to retry only those")
        return 1
    return 0


def cmd_diagram(args) -> int:
    course_dir, name, root, md_root, _, manifest_path = _paths(args)
    if not md_root.exists():
        print(f"no generated notes at {md_root}; run `generate` first", file=sys.stderr)
        return 1
    sections = _select(discover(course_dir), args.section, args.only)
    manifest = Manifest(manifest_path)

    print(f"\nGenerating diagrams for {name}")
    if not diagrams_mod.renderer_available():
        print("  note: no mermaid renderer found. Diagrams will render live in")
        print("  the HTML, but .docx will show their source instead.")
        print("  Fix with: npm install -g @mermaid-js/mermaid-cli\n")

    stats = diagrams_mod.generate(
        name, sections, md_root, manifest,
        model=args.model, workers=args.workers, force=args.force,
    )
    print(f"\n  diagrams: {stats}")
    return 1 if stats.get("failed") else 0


def cmd_build(args) -> int:
    course_dir, name, root, md_root, docx_root, _ = _paths(args)
    if not md_root.exists():
        print(f"no generated notes at {md_root}; run `generate` first", file=sys.stderr)
        return 1
    sections = _select(discover(course_dir), args.section, args.only)
    written = build_mod.build(
        name, sections, md_root, docx_root,
        max_words=args.max_words,
        image_cache=None if args.no_images else root / "diagram-images",
    )
    print(f"\n  {len(written)} document(s) in {docx_root}")
    for p in written:
        print(f"    {p.name}")
    print(f"\n  Upload instructions: {docx_root / 'UPLOAD.md'}")
    return 0


def cmd_setup(args) -> int:
    print("\nOptional extras (the core pipeline needs none of these):\n")
    for name, ok in setup_cmd.status().items():
        print(f"  [{'x' if ok else ' '}] {name:<7} {setup_cmd.EXTRAS[name]['why']}")
    print()
    return setup_cmd.run(args.extra or None, dry_run=args.dry_run)


def cmd_fetch(args) -> int:
    """Download transcripts only, without generating notes."""
    course_dir = ingest.resolve_input(
        args.input or args.course,
        Path(args.input_dir).expanduser().resolve(),
        cdp_port=args.attach,
        refetch=args.refetch,
    )
    sections = discover(course_dir)
    lectures = flatten(sections)
    print(f"\n  {course_name(course_dir)}")
    print(f"  {len(sections)} sections, {len(lectures)} lectures -> {course_dir}")
    return 0


def cmd_links(args) -> int:
    """Print where a course's notes live, or every course's if none is named."""
    output_root = Path(args.output).expanduser().resolve()

    if not (args.input or args.course):
        courses = links_mod.all_courses(output_root)
        if not courses:
            print(f"\n  nothing generated yet under {output_root}")
            return 0
        print(f"\n  {len(courses)} course(s) under {output_root}\n")
        for name, course_dir, found in courses:
            print(f"  {name}")
            if found:
                for _label, url in found:
                    print(f"    {url}")
            else:
                print("    not published to Google Docs yet")
            print()
        return 0

    _course_dir, name, root, _md, _docx, manifest_path = _paths(args)
    if not manifest_path.exists():
        print(f"nothing generated yet for {name}", file=sys.stderr)
        return 1

    found = links_mod.collect(Manifest(manifest_path))
    print(f"\n  {name}")
    print(f"  files: {root}")
    if found:
        links_mod.show(found)
    else:
        print("\n  Not published to Google Docs yet - run `notesgen push`.")
    return 0


def cmd_export(args) -> int:
    course_dir, name, root, md_root, _, _ = _paths(args)
    if not md_root.exists():
        print(f"no generated notes at {md_root}; run `generate` first", file=sys.stderr)
        return 1
    sections = _select(discover(course_dir), args.section, args.only)
    formats = tuple(args.format) if args.format else export_mod.FORMATS

    written = export_mod.export(
        name, sections, md_root, root,
        formats=formats,
        max_words=args.max_words,
        whole_course=not args.no_whole_course,
        image_cache=None if getattr(args, "no_images", False) else root / "diagram-images",
    )
    print()
    for fmt, paths in written.items():
        print(f"  {len(paths):>3} {fmt:<5} -> {root / export_mod.SUBDIR[fmt]}")
    print(f"\n  These cost nothing to regenerate - only `generate` spends tokens.")
    return 0


def _whole_course_html(root: Path) -> Path | None:
    """The single-file web page for the course, or the first section's."""
    html_dir = root / "html"
    if not html_dir.is_dir():
        return None
    combined = html_dir / "00 - Complete Course.html"
    if combined.exists():
        return combined
    pages = sorted(html_dir.glob("*.html"))
    return pages[0] if pages else None


def cmd_push(args) -> int:
    course_dir, name, root, md_root, docx_root, manifest_path = _paths(args)
    if not md_root.exists():
        print(f"no generated notes at {md_root}; run `generate` first", file=sys.stderr)
        return 1
    sections = _select(discover(course_dir), args.section, args.only)
    manifest = Manifest(manifest_path)
    config_dir = Path(args.config_dir).expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)
    image_cache = None if args.no_images else root / "diagram-images"

    publish = getattr(args, "publish", None) or ["gdoc"]

    # The library refuses to open a browser on its own now, because that would
    # hang a queued web job. On the command line there is a human present, so
    # offer the flow before doing any work.
    if any(o in publish for o in pipeline.DRIVE_OUTPUTS) and not gdocs.connected(config_dir):
        try:
            gdocs.connect(config_dir)
        except gdocs.GDocsError as exc:
            print(f"\n{exc}", file=sys.stderr)
            return 1

    try:
        if "drive-html" in publish:
            page = _whole_course_html(root)
            if page is None:
                print("  no HTML to upload; run `export` first", file=sys.stderr)
            else:
                print(f"\n  Uploading {page.name} ({page.stat().st_size // 1024} KB)...")
                url = gdocs.push_file(
                    page, f"{name} (web page)", config_dir, manifest,
                    mime=gdocs.HTML_MIME, convert=False,
                    folder_name=name, key=f"__gdoc__/{name} (web page)",
                )
                print(f"  {url}")

        if "pdf" in publish:
            page = _whole_course_html(root)
            pdf_path = root / "pdf" / f"{name}.pdf"
            if page is None:
                print("  no HTML to render from; run `export` first", file=sys.stderr)
            else:
                print(f"\n  Rendering PDF from {page.name}...")
                try:
                    pdf_mod.from_html(page, pdf_path)
                    print(f"  {pdf_path}  ({pdf_path.stat().st_size // 1024} KB)")
                except pdf_mod.PdfError as exc:
                    print(f"  {exc}", file=sys.stderr)
                    pdf_path = None

                if pdf_path and "drive-pdf" in publish:
                    url = gdocs.push_file(
                        pdf_path, f"{name} (PDF)", config_dir, manifest,
                        mime=gdocs.PDF_MIME, convert=False,
                        folder_name=name, key=f"__gdoc__/{name} (PDF)",
                    )
                    print(f"  {url}")

        if "gdoc" not in publish:
            found = links_mod.collect(manifest)
            written = links_mod.write_file(root, name, found)
            if written:
                print(f"\n  Saved these links to {written}")
            return 0

        if args.split_sections:
            print(f"\nPushing {len(sections)} section documents to Google Docs")
            urls = []
            for doc in build_mod.collect(name, sections, md_root, max_words=args.max_words):
                path = docx_root / (doc.basename + ".docx")
                if not path.exists():
                    path = build_mod.build_section_doc(
                        doc.title, doc.subtitle, doc.parts, path, image_cache
                    )
                url = gdocs.push(path, f"{name} - {doc.title}", config_dir, manifest,
                                 folder_name=name, key=f"__gdoc__/{doc.basename}")
                print(f"  {doc.basename}\n    {url}")
                urls.append(url)
            print(f"\n  {len(urls)} document(s) in your Drive folder '{name}'")
        else:
            print(f"\nBuilding one document for the whole course...")
            combined = root / "gdocs" / f"{name}.docx"
            combined.parent.mkdir(parents=True, exist_ok=True)
            build_mod.build_single(name, sections, md_root, combined, image_cache=image_cache)
            print(f"  {combined.stat().st_size // 1024} KB, uploading...")
            url = gdocs.push(combined, name, config_dir, manifest, folder_name=name)
            print(f"\n  {url}")
            print("  Navigate it with View > Show outline.")

        found = links_mod.collect(manifest)
        written = links_mod.write_file(root, name, found)
        if written:
            print(f"\n  Saved these links to {written}")
    except gdocs.GDocsError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    return 0


def cmd_run(args) -> int:
    """One command: fetch if needed, generate, and produce what was asked for."""
    try:
        wanted = outputs_mod.parse(args.outputs)
    except outputs_mod.OutputError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print("\nWill produce:")
    print(outputs_mod.describe(wanted))

    source = getattr(args, "input", None) or getattr(args, "course", None)
    if not source:
        raise SystemExit("--input is required (a .zip, a folder, or a Udemy course URL)")

    try:
        # Resolved once for the whole run. Doing it per stage, as this used to,
        # re-extracted the zip (after deleting it) for every output format.
        course = pipeline.resolve(
            source,
            Path(args.input_dir).expanduser().resolve(),
            Path(args.output).expanduser().resolve(),
            cdp_port=getattr(args, "attach", None),
            refetch=getattr(args, "refetch", False),
        )
        result = pipeline.run(course, _run_options(args, wanted))
    except pipeline.PipelineError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print("\n" + "=" * 60)
    print(f"  {result.course}")
    print("=" * 60)
    print(f"  Files: {course.root}")
    for folder, blurb in pipeline.FOLDERS.items():
        if (course.root / folder).is_dir():
            print(f"    {folder + '/':<12} {blurb}")
    if result.links:
        links_mod.show(result.links)
    elif "gdoc" in wanted:
        print("\n  Google Docs publish did not complete - see above.")
    else:
        print("\n  Publish to Google Drive with:  python3 -m notesgen run --outputs gdoc")

    for problem in result.errors:
        print(f"\n  {problem}", file=sys.stderr)
    if result.cancelled:
        print("\n  cancelled", file=sys.stderr)
    return 1 if (result.failed or result.errors) else 0


def _run_options(args, wanted) -> "pipeline.RunOptions":
    """Build the pipeline options from whatever flags this subcommand has.

    `run` deliberately does not carry every `push` flag, so read them defensively
    and let RunOptions supply the defaults.
    """
    config_dir = getattr(args, "config_dir", None)
    return pipeline.RunOptions(
        outputs=wanted,
        model=getattr(args, "model", "sonnet"),
        workers=getattr(args, "workers", 3),
        force=getattr(args, "force", False),
        no_rollup=getattr(args, "no_rollup", False),
        sections=getattr(args, "section", None),
        only=getattr(args, "only", None),
        max_words=getattr(args, "max_words", pipeline.MAX_WORDS_PER_DOC),
        no_images=getattr(args, "no_images", False),
        split_sections=getattr(args, "split_sections", False),
        whole_course=not getattr(args, "no_whole_course", False),
        glossary=getattr(args, "glossary", None),
        config_dir=Path(config_dir) if config_dir else None,
        keep_going=getattr(args, "keep_going", False),
    )


def cmd_gdocs_auth(args) -> int:
    """Authorise Google Drive up front, so a long run never blocks on it."""
    config_dir = Path(args.config_dir).expanduser()
    state = gdocs.auth_status(config_dir)
    if state["valid"] or state["refreshable"]:
        print(f"\n  Google Drive is already connected ({config_dir}).")
        print("  Re-authorise by deleting google-token.json first.")
        return 0
    if not state["sdk"] or not state["client"]:
        print(f"\n{state['help']}", file=sys.stderr)
        return 1
    try:
        gdocs.connect(config_dir)
    except gdocs.GDocsError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    print("\n  Connected. Google Docs uploads will work from now on.")
    return 0


def cmd_serve(args) -> int:
    """Run the local web UI."""
    try:
        from .server import app as server_app
    except ImportError as exc:
        print(
            "\nThe web UI needs:\n"
            "    python3 -m notesgen setup --extra web\n"
            f"  ({exc})",
            file=sys.stderr,
        )
        return 1

    return server_app.serve(
        host=args.host,
        port=args.port,
        output_dir=Path(args.output).expanduser().resolve(),
        input_dir=Path(args.input_dir).expanduser().resolve(),
        config_dir=Path(args.config_dir).expanduser(),
        open_browser=not args.no_browser,
    )


def main(argv=None) -> int:
    # Load .env first so every default below can come from it.
    providers_base.load_dotenv()

    parser = argparse.ArgumentParser(
        prog="notesgen",
        description="Turn Udemy course transcripts into revision notes as .docx",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, *, generating=False, building=False, exporting=False):
        p.add_argument(
            "--input", "-i",
            default=os.environ.get("NOTESGEN_INPUT") or None,
            help="a .zip from the Udemy Transcript Extractor, an extracted "
                 "folder, or a Udemy course URL. Defaults to $NOTESGEN_INPUT.",
        )
        p.add_argument("--course", help=argparse.SUPPRESS)  # old name, still works
        p.add_argument(
            "--output",
            default=os.environ.get("NOTESGEN_OUTPUT") or str(DEFAULT_OUTPUT),
        )
        p.add_argument(
            "--input-dir",
            default=os.environ.get("NOTESGEN_INPUT_DIR") or str(DEFAULT_INPUT),
            help="where zips are extracted and URL fetches are saved",
        )
        p.add_argument(
            "--refetch", action="store_true",
            help="re-download from Udemy even if this course was fetched before",
        )
        p.add_argument(
            "--attach", type=int, metavar="PORT", nargs="?", const=9222,
            help="attach to a Chrome you started with --remote-debugging-port "
                 "(default 9222). Use this if Cloudflare keeps challenging.",
        )
        p.add_argument("--section", action="append", help="limit to section number(s), repeatable")
        p.add_argument("--only", help="glob over '<section-dir>/<lecture-file>'")
        if generating:
            p.add_argument(
                "--provider", choices=["claude-cli", "anthropic", "openai", "gemini"],
                default=os.environ.get("NOTESGEN_PROVIDER") or None,
                help="default: $NOTESGEN_PROVIDER, else the claude CLI if "
                     "installed, else whichever API key is set",
            )
            p.add_argument(
                "--model", default=os.environ.get("NOTESGEN_MODEL") or "sonnet"
            )
            p.add_argument("--workers", type=int, default=3)
            p.add_argument("--glossary", help="override glossary.yml")
            p.add_argument("--force", action="store_true", help="regenerate even if current")
            p.add_argument("--no-rollup", action="store_true")
        if building:
            p.add_argument("--max-words", type=int, default=build_mod.MAX_WORDS_PER_DOC)
            p.add_argument("--no-images", action="store_true",
                           help="skip rendering diagrams into .docx")
        if exporting:
            p.add_argument(
                "--format", action="append", choices=list(export_mod.FORMATS),
                help="repeatable; default is all three",
            )
            p.add_argument("--no-whole-course", action="store_true",
                           help="skip the single combined all-sections file")

    p = sub.add_parser("setup", help="install the optional extras (gdocs, udemy, api)")
    p.add_argument("--extra", action="append", choices=list(setup_cmd.EXTRAS),
                   help="install just one; default is all missing")
    p.add_argument("--dry-run", action="store_true", help="print the commands only")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("discover", help="list the course tree and flag missing transcripts")
    common(p)
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("generate", help="generate Markdown notes")
    common(p, generating=True)
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser(
        "diagram", help="generate Mermaid diagrams per section from existing notes"
    )
    common(p, generating=True)
    p.set_defaults(func=cmd_diagram)

    p = sub.add_parser("build", help="assemble .docx from generated Markdown")
    common(p, building=True)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser(
        "fetch",
        help="download transcripts from a Udemy URL (or unpack a zip) and stop",
    )
    common(p)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("links", help="show where this course's notes live")
    common(p)
    p.set_defaults(func=cmd_links)

    p = sub.add_parser("export", help="write notes as .html, .txt and .md (no model calls)")
    common(p, building=True, exporting=True)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("push", help="upload the notes to Google Drive")
    common(p, building=True)
    p.add_argument(
        "--publish", action="append",
        choices=["gdoc", "drive-html", "pdf", "drive-pdf"],
        help="gdoc = a Google Doc (default); drive-html = the web page; "
             "pdf = export the Doc as PDF; drive-pdf = that PDF on Drive. "
             "Repeatable.",
    )
    p.add_argument("--split-sections", action="store_true",
                   help="one Doc per section instead of one for the whole course")
    p.add_argument("--config-dir", default=str(PROJECT_ROOT / ".gdocs"),
                   help="where the OAuth client and cached token live")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("run", help="generate, then build and export every format")
    common(p, generating=True, building=True, exporting=True)
    p.add_argument("--keep-going", action="store_true", help="build even if some lectures failed")
    p.add_argument("--no-docx", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--no-diagrams", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--outputs", action="append",
        default=[os.environ.get("NOTESGEN_OUTPUTS")] if os.environ.get("NOTESGEN_OUTPUTS") else None,
        help="what to produce, comma-separated: "
             + ", ".join(outputs_mod.ALL)
             + ". Shortcuts: all, drive, web, word, markdown, text. "
               "Defaults to $NOTESGEN_OUTPUTS, else everything local.",
    )
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("gdocs-auth", help="authorise Google Drive access")
    p.add_argument("--config-dir", default=str(PROJECT_ROOT / ".gdocs"),
                   help="where the OAuth client and cached token live")
    p.set_defaults(func=cmd_gdocs_auth)

    p = sub.add_parser("serve", help="run the local web UI")
    p.add_argument("--host", default="127.0.0.1",
                   help="default 127.0.0.1; only change this if you know why")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--no-browser", action="store_true",
                   help="do not open a browser tab on start")
    p.add_argument("--output", default=os.environ.get("NOTESGEN_OUTPUT") or str(DEFAULT_OUTPUT))
    p.add_argument("--input-dir",
                   default=os.environ.get("NOTESGEN_INPUT_DIR") or str(DEFAULT_INPUT))
    p.add_argument("--config-dir", default=str(PROJECT_ROOT / ".gdocs"))
    p.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    engine.configure(
        provider=getattr(args, "provider", None),
        model=getattr(args, "model", None),
    )
    try:
        return args.func(args)
    except ingest.IngestError as exc:
        print(f"\ninput error: {exc}", file=sys.stderr)
        return 1
    except engine.EngineError as exc:
        print(f"\nprovider error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
