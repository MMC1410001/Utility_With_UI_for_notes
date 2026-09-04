"""One command that installs the optional extras, so you don't have to.

Everything core (notes, .docx, .html/.txt/.md) works with no extras. These are
only needed for the two paths that talk to the outside world.
"""

from __future__ import annotations

import subprocess
import sys

EXTRAS = {
    "gdocs": {
        "packages": ["google-api-python-client", "google-auth-oauthlib"],
        "why": "upload notes to Google Docs (`notesgen push`)",
    },
    "udemy": {
        "packages": ["playwright"],
        "why": "fetch transcripts from a Udemy URL (`notesgen fetch -i <url>`)",
        "post": [[sys.executable, "-m", "playwright", "install", "chromium"]],
    },
    "api": {
        "packages": ["anthropic", "openai", "google-genai"],
        "why": "use the Anthropic / OpenAI / Gemini APIs instead of the claude CLI",
    },
    "web": {
        "packages": ["fastapi", "uvicorn[standard]"],
        "why": "the local web UI and Chrome extension (`notesgen serve`)",
    },
}


def _installed(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


# status() iterates EXTRAS but indexes PROBE, so every extra needs an entry
# here too or `notesgen setup` raises KeyError.
PROBE = {
    "gdocs": "googleapiclient",
    "udemy": "playwright",
    "api": "anthropic",
    "web": "fastapi",
}

assert set(PROBE) == set(EXTRAS), "every extra needs a PROBE module"


def status() -> dict[str, bool]:
    return {name: _installed(PROBE[name]) for name in EXTRAS}


def run(which: list[str] | None = None, *, dry_run: bool = False) -> int:
    targets = which or list(EXTRAS)
    failed = 0

    for name in targets:
        extra = EXTRAS[name]
        if _installed(PROBE[name]):
            print(f"  {name}: already installed")
            continue

        cmds = [[sys.executable, "-m", "pip", "install", *extra["packages"]]]
        cmds += extra.get("post", [])

        print(f"\n  {name} - {extra['why']}")
        for cmd in cmds:
            print(f"    $ {' '.join(cmd)}")
            if dry_run:
                continue
            result = subprocess.run(cmd)
            if result.returncode != 0:
                print(f"    failed (exit {result.returncode})")
                failed += 1
                break

    if dry_run:
        print("\n  dry run - nothing installed")
    return 1 if failed else 0
