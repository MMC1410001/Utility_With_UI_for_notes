"""Provider registry and the retry loop shared by every backend."""

from __future__ import annotations

import os
import random
import time

from . import anthropic_api, claude_cli, gemini_api, openai_api
from .base import EngineError, MissingCredential, ProviderUnavailable, Result, load_dotenv

__all__ = [
    "EngineError", "MissingCredential", "ProviderUnavailable",
    "Result", "call", "resolve", "PROVIDERS",
]

PROVIDERS = {
    "claude-cli": claude_cli,
    "anthropic": anthropic_api,
    "openai": openai_api,
    "gemini": gemini_api,
}

# Accepted spellings for --provider / NOTESGEN_PROVIDER.
ALIASES = {
    "claude": "claude-cli",
    "cli": "claude-cli",
    "claude_cli": "claude-cli",
    "claude-code": "claude-cli",
    "chatgpt": "openai",
    "gpt": "openai",
    "google": "gemini",
}


def resolve(name: str | None = None):
    """Pick a provider module from an explicit name, the env, or what works.

    Order: the --provider flag, then NOTESGEN_PROVIDER, then the claude CLI if
    it is installed (it needs no key), then whichever API key happens to be set.
    """
    load_dotenv()
    requested = (name or os.environ.get("NOTESGEN_PROVIDER") or "").strip().lower()

    if requested:
        key = ALIASES.get(requested, requested)
        if key not in PROVIDERS:
            raise EngineError(
                f"unknown provider '{requested}'. "
                f"Choose one of: {', '.join(PROVIDERS)}."
            )
        return PROVIDERS[key]

    if claude_cli.available():
        return claude_cli
    for module in (anthropic_api, openai_api, gemini_api):
        if module.available():
            return module

    raise EngineError(
        "no provider available. Install the `claude` CLI, or set one of "
        "ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY "
        "(environment or a .env in the project root)."
    )


def call(
    prompt: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    system: str | None = None,
    timeout: int = 600,
    attempts: int = 3,
) -> Result:
    from .. import prompts

    module = resolve(provider)
    model = model or module.DEFAULT_MODEL
    system = system if system is not None else prompts.SYSTEM

    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return module.call(prompt, model=model, system=system, timeout=timeout)
        except (MissingCredential, ProviderUnavailable):
            # A missing key or an uninstalled SDK will not fix itself on retry.
            raise
        except Exception as exc:  # noqa: BLE001 - providers raise their own types
            last = exc
            if attempt == attempts:
                break
            # Usage limits and transient overload both benefit from backing
            # off rather than hammering.
            time.sleep(min(60, 5 * 2 ** (attempt - 1)) + random.uniform(0, 3))

    raise EngineError(f"{module.NAME} failed after {attempts} attempts: {last}")
