"""Tests for the cost estimation fallback table."""

from __future__ import annotations

import pytest

from openharness.observability import cost


def test_estimate_cost_returns_none_for_unknown_model_with_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cost, "FALLBACK_PRICES_PER_MILLION", {})
    assert (
        cost.estimate_cost(
            model="totally-made-up-model",
            provider="gemini",
            input_tokens=1_000,
            output_tokens=2_000,
        )
        is None
    )


def test_estimate_cost_returns_none_when_no_tokens() -> None:
    assert cost.estimate_cost(model="anything", input_tokens=0, output_tokens=0) is None


def test_estimate_cost_uses_fallback_for_gemini_3_1_flash_lite_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preview model isn't in genai_prices yet, so the fallback
    table must produce a non-None estimate matching its hardcoded rates."""

    def _genai_prices_returns_none(*_args, **_kwargs):  # noqa: ANN002, ANN003
        class _Result:
            total_price = None

        return _Result()

    # Force genai_prices to behave as if the model were unknown.
    import sys
    import types

    fake_module = types.SimpleNamespace(
        Usage=lambda input_tokens, output_tokens: None,
        calc_price=_genai_prices_returns_none,
    )
    monkeypatch.setitem(sys.modules, "genai_prices", fake_module)

    estimate = cost.estimate_cost(
        model="gemini-3.1-flash-lite-preview",
        provider="gemini",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    assert estimate is not None
    # Per the fallback table: $0.10 input + $0.40 output per million.
    assert estimate == pytest.approx(0.10 + 0.40)
