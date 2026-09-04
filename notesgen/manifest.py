"""Per-course record of what has been generated, so runs are resumable.

183 lectures will not finish inside one usage window. Every completed unit is
checkpointed immediately and keyed by a hash of its source text, so a re-run
skips finished work and re-does only what actually changed.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.entries: dict[str, dict] = {}
        if path.exists():
            try:
                self.entries = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # A half-written manifest must not block a re-run; the worst
                # case is that we regenerate everything.
                backup = path.with_suffix(".json.corrupt")
                path.replace(backup)
                print(f"  manifest was unreadable, moved to {backup.name}")

    def is_current(self, key: str, source_hash: str) -> bool:
        entry = self.entries.get(key)
        if not entry or entry.get("hash") != source_hash:
            return False
        out = entry.get("output")
        # A manifest entry with no surviving output file is stale.
        return bool(out) and (self.path.parent / out).exists()

    def record(self, key: str, **fields) -> None:
        with self._lock:
            self.entries[key] = fields
            self._flush()

    def _flush(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.entries, indent=2, sort_keys=True), encoding="utf-8"
        )
        tmp.replace(self.path)

    @property
    def total_cost(self) -> float:
        return sum(float(e.get("cost_usd") or 0) for e in self.entries.values())
