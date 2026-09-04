"""OpenAI Chat Completions backend."""

from __future__ import annotations

from .base import ProviderUnavailable, Result, require_key

NAME = "openai"
ENV_VAR = "OPENAI_API_KEY"
DEFAULT_MODEL = "gpt-4o"

# USD per million tokens (input, output). Update when OpenAI changes pricing;
# an unknown model simply reports 0.00 rather than guessing.
PRICING = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
}


def available() -> bool:
    import os

    return bool(os.environ.get(ENV_VAR))


def call(prompt: str, *, model: str, system: str, timeout: int) -> Result:
    key = require_key(NAME, ENV_VAR)
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise ProviderUnavailable(NAME, "openai") from exc

    client = OpenAI(api_key=key, timeout=timeout)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    )

    text = (response.choices[0].message.content or "").strip()
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    usage = response.usage
    cost = 0.0
    if usage:
        cost = (
            usage.prompt_tokens * price_in + usage.completion_tokens * price_out
        ) / 1_000_000

    return Result(text=text, cost_usd=cost)
