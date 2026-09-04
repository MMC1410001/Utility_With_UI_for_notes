"""Backwards-compatible facade over the provider registry.

The real backends live in `notesgen.providers`. This keeps `engine.call(...)`
working for existing callers and holds the process-wide provider/model choice
the CLI sets once at startup.
"""

from __future__ import annotations

from . import providers
from .providers import EngineError, MissingCredential, Result  # noqa: F401

# Set by the CLI; None means "resolve automatically".
_provider: str | None = None
_model: str | None = None


def configure(provider: str | None = None, model: str | None = None) -> None:
    global _provider, _model
    _provider = provider
    _model = model


def active() -> tuple[str, str]:
    """The provider name and model that would be used, for display."""
    module = providers.resolve(_provider)
    return module.NAME, _model or module.DEFAULT_MODEL


def ensure_available() -> str:
    module = providers.resolve(_provider)
    return module.NAME


def call(prompt: str, *, model: str | None = None, timeout: int = 600, attempts: int = 3) -> Result:
    return providers.call(
        prompt,
        provider=_provider,
        # An explicit per-call model wins, then the CLI's, then the provider
        # default. "sonnet" is a claude-cli alias, so it must not leak into an
        # API provider that has never heard of it.
        model=_resolve_model(model),
        timeout=timeout,
        attempts=attempts,
    )


def _resolve_model(model: str | None) -> str | None:
    module = providers.resolve(_provider)
    chosen = model or _model
    if chosen is None:
        return None
    # generate.py passes the CLI default of "sonnet" unconditionally; treat it
    # as "use this provider's default" for providers that don't know the alias.
    if chosen == "sonnet" and module is not providers.PROVIDERS["claude-cli"]:
        aliases = getattr(module, "ALIASES", {})
        return aliases.get("sonnet", module.DEFAULT_MODEL)
    return chosen
