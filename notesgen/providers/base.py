"""Shared types for every note-generation backend."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class EngineError(RuntimeError):
    pass


class ProviderUnavailable(EngineError):
    """The provider's SDK is not installed. Retrying will not help."""

    def __init__(self, provider: str, package: str):
        super().__init__(f"provider '{provider}' needs the {package} package: pip install {package}")


class MissingCredential(EngineError):
    """Raised when a provider is selected but its API key is not set.

    Carries the variable name so the CLI can tell the user exactly what to
    set, rather than surfacing a library traceback.
    """

    def __init__(self, provider: str, env_var: str):
        super().__init__(
            f"provider '{provider}' needs {env_var}. Set it in your environment "
            f"or add `{env_var}=...` to a .env file in the project root."
        )
        self.provider = provider
        self.env_var = env_var


@dataclass
class Result:
    text: str
    cost_usd: float = 0.0
    duration_ms: int = 0


def load_dotenv(start: Path | None = None) -> None:
    """Read a .env from the project root into os.environ.

    Deliberately does not overwrite variables already set, so an explicit
    export always beats the file. Kept tiny rather than taking a dependency
    on python-dotenv for what is a dozen lines.
    """
    root = (start or Path(__file__).resolve().parent.parent.parent) / ".env"
    if not root.exists():
        return
    for line in root.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def require_key(provider: str, env_var: str) -> str:
    load_dotenv()
    key = os.environ.get(env_var, "").strip()
    if not key:
        raise MissingCredential(provider, env_var)
    return key
