"""What the user wants produced, from one setting.

`run` reads this so a single command can mean "just a Google Doc", "just
HTML", or "everything", without a pile of --no-this --no-that flags.
"""

from __future__ import annotations

# name -> (what it produces, which stage makes it)
CHOICES: dict[str, str] = {
    "notes": "Markdown notes per lecture (always produced; everything else builds on it)",
    "diagrams": "system and flow diagrams for each section",
    "html": "a self-contained web page per section, with a navigation sidebar",
    "txt": "plain text, pastes anywhere",
    "md": "one Markdown file per section",
    "docx": "Word documents",
    "gdoc": "a Google Doc in your Drive",
    "drive-html": "the web page uploaded to Drive, shareable by link",
    "pdf": "a PDF, rendered from the web page so diagrams come through",
    "drive-pdf": "that PDF on Drive, shareable by link",
}

ALL = tuple(CHOICES)
DEFAULT = ("notes", "diagrams", "html", "txt", "md", "docx")

ALIASES = {
    "all": ALL,
    "pdfs": ("notes", "diagrams", "html", "pdf"),
    "everything": ALL,
    "google": ("notes", "diagrams", "docx", "gdoc"),
    "gdocs": ("notes", "diagrams", "docx", "gdoc"),
    "drive": ("notes", "diagrams", "docx", "gdoc"),
    "local": DEFAULT,
    "web": ("notes", "diagrams", "html"),
    "markdown": ("notes", "md"),
    "text": ("notes", "txt"),
    "word": ("notes", "diagrams", "docx"),
}


class OutputError(ValueError):
    pass


def parse(value: str | list[str] | None) -> tuple[str, ...]:
    """Turn `--outputs gdoc,html` or NOTESGEN_OUTPUTS into a set of stages."""
    if not value:
        return DEFAULT

    if isinstance(value, str):
        value = [value]
    tokens = [t.strip().lower() for v in value for t in v.split(",") if t.strip()]
    if not tokens:
        return DEFAULT

    wanted: list[str] = []
    for token in tokens:
        if token in ALIASES:
            wanted.extend(ALIASES[token])
        elif token in CHOICES:
            wanted.append(token)
        else:
            raise OutputError(
                f"unknown output '{token}'.\n"
                f"  Choose from: {', '.join(ALL)}\n"
                f"  Or a shortcut: {', '.join(sorted(ALIASES))}"
            )

    # `notes` underpins everything, and both Google outputs need a built file.
    if "gdoc" in wanted and "docx" not in wanted:
        wanted.append("docx")
    if "drive-html" in wanted and "html" not in wanted:
        wanted.append("html")
    # A PDF is exported from the Google Doc, so that has to exist first.
    if "drive-pdf" in wanted and "pdf" not in wanted:
        wanted.append("pdf")
    if "pdf" in wanted and "html" not in wanted:
        wanted.append("html")
    if "notes" not in wanted:
        wanted.insert(0, "notes")

    return tuple(dict.fromkeys(o for o in ALL if o in wanted))


def describe(selected: tuple[str, ...]) -> str:
    return "\n".join(f"    {name:<11} {CHOICES[name]}" for name in selected)
