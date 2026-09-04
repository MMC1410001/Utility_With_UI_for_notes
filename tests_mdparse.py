"""Regression tests for the shared Markdown parser.

Run: python3 tests_mdparse.py

The emphasis cases matter more than they look. These notes cover a Python
course, so `**kwargs`, `x**2` and `{**d1, **d2}` appear constantly in prose --
a loose bold regex silently eats them.
"""

from notesgen.diagrams import sanitize
from notesgen.mdparse import parse_blocks, strip_inline

INLINE_CASES = [
    # (source, expected plain text)
    ("**Objective:** learn `StateGraph`", "Objective: learn StateGraph"),
    ("*italic* and **bold** together", "italic and bold together"),
    # Emphasis must not open or close beside a space -- this is what protects
    # Python syntax appearing in prose.
    ("merged = {**d1, **d2} wins", "merged = {**d1, **d2} wins"),
    ("use **kwargs and a ** b", "use **kwargs and a ** b"),
    ("def fn(a, *args, **kwargs):", "def fn(a, *args, **kwargs):"),
    ("list comp [x**2 for x in xs]", "list comp [x**2 for x in xs]"),
    # Bold wrapping code spans that themselves contain asterisks.
    ("- **`import *`** - wildcard", "- import * - wildcard"),
    ("**`*args`/`**kwargs` are conventions**", "*args/**kwargs are conventions"),
]

TABLE_MD = "| A | B |\n|---|---|\n| 1 | 2 |\n"

MERMAID_MD = "```mermaid\nflowchart LR\n  a --> b\n```\n"

# Mermaid keywords used as node ids break the parser; keywords heading a
# statement must survive. One un-renderable diagram is worse than none.
SANITIZE_CASES = [
    ('flowchart LR\n  graph["compiled graph"] --> x',
     'flowchart LR\n  graphNode["compiled graph"] --> x'),
    ('flowchart TB\n  subgraph one\n    direction TB\n    a --> b\n  end',
     'flowchart TB\n  subgraph one\n    direction TB\n    a --> b\n  end'),
    ('flowchart LR\n  ok["this graph label stays"] --> y',
     'flowchart LR\n  ok["this graph label stays"] --> y'),
    ('sequenceDiagram\n  participant A\n  A->>B: hi',
     'sequenceDiagram\n  participant A\n  A->>B: hi'),
]

HEADING_MD = "# Title\n\n### Summary\n\n#### Detail\n"


def main() -> int:
    failures = 0

    for src, want in INLINE_CASES:
        got = strip_inline(src)
        if got != want:
            failures += 1
            print(f"FAIL inline: {src!r}\n  want {want!r}\n  got  {got!r}")

    blocks = list(parse_blocks(TABLE_MD))
    if len(blocks) != 1 or blocks[0].kind != "table":
        failures += 1
        print(f"FAIL table: got {[b.kind for b in blocks]}")
    elif blocks[0].rows != [["A", "B"], ["1", "2"]]:
        failures += 1
        print(f"FAIL table rows: {blocks[0].rows}")

    # #/###/#### must collapse to a contiguous 1/2/3 so outlines are not gappy.
    levels = [b.level for b in parse_blocks(HEADING_MD) if b.kind == "heading"]
    if levels != [1, 2, 3]:
        failures += 1
        print(f"FAIL heading levels: {levels}")

    blocks = list(parse_blocks(MERMAID_MD))
    if len(blocks) != 1 or blocks[0].kind != "mermaid":
        failures += 1
        print(f"FAIL mermaid block: got {[b.kind for b in blocks]}")

    for src, want in SANITIZE_CASES:
        got = sanitize(src)
        if got != want:
            failures += 1
            print(f"FAIL sanitize:\n  want {want!r}\n  got  {got!r}")

    callouts = [b for b in parse_blocks("> [!] Code shown on screen.\n") if b.is_callout]
    if not callouts:
        failures += 1
        print("FAIL callout not detected")

    print(f"\n{'all tests passed' if not failures else f'{failures} failure(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
