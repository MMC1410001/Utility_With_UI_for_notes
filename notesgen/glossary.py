"""Repair the systematic ASR errors in Udemy auto-captions.

Whisper-style captions mangle product names consistently: LangGraph becomes
"Landgraf" or "line graph", LangChain becomes "Lankin". Left alone, every note
we generate names the technology wrong. This runs before the model sees the
text, so the model never has to guess.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import yaml

DEFAULT_GLOSSARY = Path(__file__).resolve().parent.parent / "glossary.yml"


class Glossary:
    def __init__(self, mapping: dict[str, list[str]]):
        self.mapping = mapping
        # Longest variants first so "Lang graph" is consumed before any
        # shorter rule can bite into it.
        pairs: list[tuple[str, str]] = [
            (variant, correct)
            for correct, variants in mapping.items()
            for variant in variants
        ]
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        self._rules = [
            (
                # \b is wrong at a non-word edge, so anchor on lookarounds that
                # tolerate variants containing spaces or hyphens.
                re.compile(
                    r"(?<![\w-])" + r"[\s-]+".join(map(re.escape, variant.split())) + r"(?![\w-])",
                    re.IGNORECASE,
                ),
                correct,
            )
            for variant, correct in pairs
        ]

    @classmethod
    def load(cls, path: Path | None = None) -> "Glossary":
        path = path or DEFAULT_GLOSSARY
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls({k: list(v or []) for k, v in data.items()})

    def apply(self, text: str) -> tuple[str, Counter]:
        """Return the corrected text and a count of what was replaced."""
        hits: Counter = Counter()
        for pattern, correct in self._rules:

            def _sub(m: re.Match) -> str:
                if m.group(0) == correct:  # already right, don't count it
                    return correct
                hits[f"{m.group(0)} -> {correct}"] += 1
                return correct

            text = pattern.sub(_sub, text)
        return text, hits
