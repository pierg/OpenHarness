"""Estimate LLM call cost using genai-prices, with a small fallback table."""

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

# Per-million-token (USD) pricing for models not yet shipped in
# genai_prices' database (typically brand-new preview models).  Keep
# this table small — the upstream package is the source of truth and
# anything in here should be removed once it's published there.  Prices
# below assume the closest non-preview sibling's rate as a placeholder
# until Google publishes preview pricing.
FALLBACK_PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
    # gemini-3.1-flash-lite-preview: priced in line with gemini-2.5-flash-lite
    # ($0.10 input / $0.40 output per million tokens) until Google
    # publishes the preview tier.
    "gemini-3.1-flash-lite-preview": (0.10, 0.40),
    # genai-prices may lag new Codex/OpenAI model IDs. Use the current
    # gpt-5.4/5.5 rate as an explicit API-equivalent placeholder until the
    # package grows first-class pricing for these Codex model ids.
    "gpt-5.4": (5.00, 15.00),
    "gpt-5.5": (5.00, 15.00),
}


def _fallback_estimate(*, model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimate cost using ``FALLBACK_PRICES_PER_MILLION``."""
    rates = FALLBACK_PRICES_PER_MILLION.get(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def estimate_cost(
    *,
    model: str | None,
    provider: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float | None:
    """Return estimated cost in USD, or None if pricing data is unavailable."""
    if not model or (input_tokens == 0 and output_tokens == 0 and cache_read_tokens == 0):
        return None

    try:
        from genai_prices import Usage, calc_price

        provider_id = PROVIDER_MAP.get(provider or "", None)
        uncached_input = max(0, input_tokens - cache_read_tokens)
        result = calc_price(
            Usage(
                input_tokens=uncached_input,
                cache_read_tokens=cache_read_tokens or None,
                output_tokens=output_tokens,
            ),
            model_ref=model,
            provider_id=provider_id,
        )
        if result.total_price is not None:
            return float(result.total_price)
    except Exception:
        _log.debug(
            "Cost estimation failed for model=%s provider=%s", model, provider, exc_info=True
        )

    return _fallback_estimate(
        model=model,
        input_tokens=max(0, input_tokens - cache_read_tokens),
        output_tokens=output_tokens,
    )
