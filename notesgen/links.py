"""Keep track of where a course's notes ended up.

A Google Doc URL printed once at the end of a long run is effectively lost.
These links are recorded in the manifest when `push` runs, and surfaced again
by `discover`, by `links`, and in a LINKS.md sitting beside the notes.
"""

from __future__ import annotations

from pathlib import Path

GDOC_PREFIX = "__gdoc__/"
LINKS_FILE = "LINKS.md"


def collect(manifest) -> list[tuple[str, str]]:
    """Every Google Doc for this course, as (label, url), index first."""
    found: list[tuple[str, str]] = []
    for key, entry in manifest.entries.items():
        if not key.startswith(GDOC_PREFIX):
            continue
        url = entry.get("url")
        if url:
            found.append((key[len(GDOC_PREFIX):], url))
    # "00 - Course Index" and the whole-course doc sort to the top naturally.
    return sorted(found)


def write_file(root: Path, course: str, links: list[tuple[str, str]]) -> Path | None:
    """Drop a LINKS.md next to the notes so the URL survives the terminal."""
    if not links:
        return None

    lines = [f"# {course}", "", "Your notes in Google Docs:", ""]
    for label, url in links:
        lines.append(f"- [{label}]({url})")
    lines += [
        "",
        "Navigate a document with **View > Show outline**.",
        "",
        "Re-running `notesgen push` updates these same documents rather than",
        "creating new ones, so these links stay valid.",
        "",
    ]
    path = root / LINKS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def all_courses(output_root: Path) -> list[tuple[str, Path, list[tuple[str, str]]]]:
    """Every course processed so far, with its links.

    Each course keeps its own directory and manifest under `output/`, so
    running several courses never mixes them up.
    """
    from .manifest import Manifest

    found = []
    if not output_root.is_dir():
        return found
    for course_dir in sorted(p for p in output_root.iterdir() if p.is_dir()):
        manifest_path = course_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        found.append((course_dir.name, course_dir, collect(Manifest(manifest_path))))
    return found


def show(links: list[tuple[str, str]], *, indent: str = "  ") -> None:
    if not links:
        return
    print(f"\n{indent}Google Docs:")
    for label, url in links:
        print(f"{indent}  {label}")
        print(f"{indent}    {url}")
