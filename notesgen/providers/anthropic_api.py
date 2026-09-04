"""Anthropic Messages API backend.

Unlike the claude_cli backend this bills per token against ANTHROPIC_API_KEY,
so `cost_usd` here is a real charge rather than a notional figure.
"""

from __future__ import annotations

from .base import ProviderUnavailable, Result, require_key

NAME = "anthropic"
ENV_VAR = "ANTHROPIC_API_KEY"
# Matches the pipeline's existing `--model sonnet` default. Override with
# --model claude-opus-5 for the most capable model.
DEFAULT_MODEL = "claude-sonnet-5"

ALIASES = {
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
    "haiku": "claude-haiku-4-5",
}

# USD per million tokens (input, output).
PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def available() -> bool:
    import os

    return bool(os.environ.get(ENV_VAR))


def call(prompt: str, *, model: str, system: str, timeout: int) -> Result:
    # Check the key first: naming the env var is more actionable than an
    # import error when the user has neither.
    key = require_key(NAME, ENV_VAR)
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ProviderUnavailable(NAME, "anthropic") from exc

    model = ALIASES.get(model, model)
    client = anthropic.Anthropic(api_key=key, timeout=timeout)

    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    usage = response.usage
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    cost = (
        usage.input_tokens * price_in + usage.output_tokens * price_out
    ) / 1_000_000

    return Result(text=text, cost_usd=cost)
