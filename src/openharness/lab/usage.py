"""Token and cost telemetry helpers for lab model calls."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from openharness.lab.paths import REPO_ROOT
from openharness.observability.cost import estimate_cost

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def cost_output_tokens(self) -> int:
        return int(self.output_tokens or 0) + int(self.reasoning_output_tokens or 0)

    @property
    def computed_total_tokens(self) -> int | None:
        if self.total_tokens is not None:
            return self.total_tokens
        parts = [
            self.input_tokens,
            self.output_tokens,
            self.reasoning_output_tokens,
        ]
        if all(p is None for p in parts):
            return None
        return sum(int(p or 0) for p in parts)


def augment_spawn_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a spawn record with normalized usage/cost fields."""
    out = dict(record)
    provider = _provider_from_record(out)
    model = _model_from_record(out) or _model_from_log(out.get("log_path"))
    usage = _usage_from_record(out) or parse_usage_from_log(
        out.get("log_path"),
        provider=provider,
    )

    out["provider"] = provider
    out["model"] = model
    if usage is not None:
        out["input_tokens"] = usage.input_tokens
        out["cached_input_tokens"] = usage.cached_input_tokens
        out["output_tokens"] = usage.output_tokens
        out["reasoning_output_tokens"] = usage.reasoning_output_tokens
        out["total_tokens"] = usage.computed_total_tokens
        if out.get("cost_usd_estimate") is None:
            out["cost_usd_estimate"] = estimate_cost(
                model=model,
                provider=_cost_provider(provider),
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=usage.cost_output_tokens,
                cache_read_tokens=int(usage.cached_input_tokens or 0),
            )
    return out


def parse_usage_from_log(path: str | Path | None, *, provider: str | None) -> ModelUsage | None:
    if not path:
        return None
    log_path = _resolve_log_path(path)
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if (provider or "").startswith("gemini"):
        return parse_gemini_usage(text)
    return parse_codex_usage(text)


def parse_codex_usage(text: str) -> ModelUsage | None:
    totals: dict[str, int] = {}
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage") if isinstance(event, dict) else None
        if not isinstance(usage, dict):
            continue
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        ):
            value = _int_or_none(usage.get(key))
            if value is not None:
                totals[key] = totals.get(key, 0) + value
    if not totals:
        return None
    return ModelUsage(
        input_tokens=totals.get("input_tokens"),
        cached_input_tokens=totals.get("cached_input_tokens"),
        output_tokens=totals.get("output_tokens"),
        reasoning_output_tokens=totals.get("reasoning_output_tokens"),
    )


def parse_gemini_usage(text: str) -> ModelUsage | None:
    candidates: list[dict[str, int]] = []
    for match in re.finditer(r'"tokens"\s*:\s*(\{[^{}]+\})', text):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        parsed = {k: int(v) for k, v in payload.items() if isinstance(v, int | float)}
        if parsed:
            candidates.append(parsed)
    if not candidates:
        return None
    # Gemini CLI prints the same token block in aggregate and role
    # sections. Pick the largest total; this avoids double-counting.
    best = max(candidates, key=lambda x: int(x.get("total") or 0))
    return ModelUsage(
        input_tokens=_int_or_none(best.get("prompt") or best.get("input")),
        cached_input_tokens=_int_or_none(best.get("cached")),
        output_tokens=_int_or_none(best.get("candidates")),
        reasoning_output_tokens=_int_or_none(best.get("thoughts")),
        total_tokens=_int_or_none(best.get("total")),
    )


def _provider_from_record(record: Mapping[str, Any]) -> str:
    provider = record.get("provider")
    if isinstance(provider, str) and provider:
        return provider
    settings = record.get("effective_settings")
    if isinstance(settings, dict):
        provider = settings.get("provider")
        if isinstance(provider, str) and provider:
            return provider
    log_path = str(record.get("log_path") or "")
    if "__gemini-" in log_path:
        return "gemini-cli"
    return "codex-cli"


def _model_from_record(record: Mapping[str, Any]) -> str | None:
    model = record.get("model")
    if isinstance(model, str) and model:
        return model
    settings = record.get("effective_settings")
    if isinstance(settings, dict):
        model = settings.get("model")
        if isinstance(model, str) and model:
            return model
    return None


def _model_from_log(path: object) -> str | None:
    if not path:
        return None
    try:
        lines = (
            _resolve_log_path(path)
            .read_text(
                encoding="utf-8",
                errors="replace",
            )
            .splitlines()[:24]
        )
    except OSError:
        return None
    for line in lines:
        if line.startswith("# effective_settings:"):
            raw = line.split(":", 1)[1].strip()
            try:
                settings = json.loads(raw)
            except json.JSONDecodeError:
                continue
            model = settings.get("model") if isinstance(settings, dict) else None
            if isinstance(model, str) and model:
                return model
        if line.startswith("# command:"):
            match = re.search(r"(?:^|\s)-m\s+([^\s]+)", line)
            if match:
                return match.group(1)
    return None


def _usage_from_record(record: Mapping[str, Any]) -> ModelUsage | None:
    usage = ModelUsage(
        input_tokens=_int_or_none(record.get("input_tokens")),
        cached_input_tokens=_int_or_none(record.get("cached_input_tokens")),
        output_tokens=_int_or_none(record.get("output_tokens")),
        reasoning_output_tokens=_int_or_none(record.get("reasoning_output_tokens")),
        total_tokens=_int_or_none(record.get("total_tokens")),
    )
    if all(
        v is None
        for v in (
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.output_tokens,
            usage.reasoning_output_tokens,
            usage.total_tokens,
        )
    ):
        return None
    return usage


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cost_provider(provider: str | None) -> str | None:
    if provider == "codex-cli":
        return "openai"
    if provider == "gemini-cli":
        return "gemini"
    return provider


def _resolve_log_path(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_file():
        return candidate
    text = str(path)
    marker = "/runs/"
    if marker in text:
        rel = text.split(marker, 1)[1]
        mirrored = REPO_ROOT / "runs" / rel
        if mirrored.is_file():
            return mirrored
    return candidate
