"""Google Gemini backend, via the google-genai SDK."""

from __future__ import annotations

from .base import ProviderUnavailable, Result, require_key

NAME = "gemini"
ENV_VAR = "GEMINI_API_KEY"
DEFAULT_MODEL = "gemini-2.5-flash"

# USD per million tokens (input, output).
PRICING = {
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
}


def available() -> bool:
    import os

    return bool(os.environ.get(ENV_VAR))


def call(prompt: str, *, model: str, system: str, timeout: int) -> Result:
    key = require_key(NAME, ENV_VAR)
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:  # pragma: no cover
        raise ProviderUnavailable(NAME, "google-genai") from exc

    client = genai.Client(api_key=key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system),
    )

    text = (response.text or "").strip()
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    cost = 0.0
    usage = getattr(response, "usage_metadata", None)
    if usage:
        cost = (
            (usage.prompt_token_count or 0) * price_in
            + (usage.candidates_token_count or 0) * price_out
        ) / 1_000_000

    return Result(text=text, cost_usd=cost)
