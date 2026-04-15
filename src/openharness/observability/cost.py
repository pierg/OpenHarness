"""Estimate LLM call cost using genai-prices."""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

PROVIDER_MAP: dict[str, str] = {
    "gemini": "google",
    "openai": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "deepseek": "deepseek",
    "mistral": "mistral",
    "groq": "groq",
    "fireworks": "fireworks",
    "together": "together",
    "cohere": "cohere",
    "xai": "x-ai",
    "cerebras": "cerebras",
    "perplexity": "perplexity",
    "aws_bedrock": "aws-bedrock",
    "azure": "azure",
}


def estimate_cost(
    *,
    model: str | None,
    provider: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float | None:
    """Return estimated cost in USD, or None if pricing data is unavailable."""
    if not model or (input_tokens == 0 and output_tokens == 0):
        return None

    try:
        from genai_prices import Usage, calc_price

        provider_id = PROVIDER_MAP.get(provider or "", None)
        result = calc_price(
            Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            model_ref=model,
            provider_id=provider_id,
        )
        return float(result.total_price) if result.total_price is not None else None
    except Exception:
        _log.debug(
            "Cost estimation failed for model=%s provider=%s", model, provider, exc_info=True
        )
        return None
