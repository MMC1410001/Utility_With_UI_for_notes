"""Drive the `claude` CLI in headless mode.

Uses the existing Claude Code subscription rather than an API key, so there is
no per-token bill. Deliberately does NOT pass --bare: that flag forces
ANTHROPIC_API_KEY auth and would bypass the subscription entirely.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from .base import EngineError, Result

NAME = "claude-cli"
DEFAULT_MODEL = "sonnet"
NEEDS_KEY = None  # the subscription is the credential


def available() -> bool:
    return shutil.which("claude") is not None


def ensure_available() -> str:
    exe = shutil.which("claude")
    if not exe:
        raise EngineError(
            "the `claude` CLI is not on PATH. Install Claude Code, or pick an "
            "API provider with --provider anthropic|openai|gemini."
        )
    return exe


def call(prompt: str, *, model: str, system: str, timeout: int) -> Result:
    cmd = [
        ensure_available(),
        "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        # Pure text transformation: no tools, and no chance of the model
        # wandering off to read or write files mid-run.
        "--allowedTools", "",
        "--max-turns", "1",
        "--append-system-prompt", system,
    ]
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        raise EngineError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:400]}")

    payload = json.loads(proc.stdout)
    if payload.get("is_error"):
        raise EngineError(f"claude reported an error: {str(payload)[:400]}")
    text = (payload.get("result") or "").strip()
    if not text:
        raise EngineError("claude returned an empty result")
    return Result(
        text=text,
        cost_usd=float(payload.get("total_cost_usd") or 0.0),
        duration_ms=int(payload.get("duration_ms") or 0),
    )
