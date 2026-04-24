from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch

from openharness.lab.usage import (
    augment_spawn_record,
    parse_codex_usage,
    parse_usage_from_log,
    parse_gemini_usage,
)


def test_parse_codex_usage_sums_turn_usage() -> None:
    text = "\n".join(
        [
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 10,
                        "reasoning_output_tokens": 3,
                    },
                }
            ),
            "non-json progress line",
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 25,
                        "cached_input_tokens": 10,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 2,
                    },
                }
            ),
        ]
    )

    usage = parse_codex_usage(text)

    assert usage is not None
    assert usage.input_tokens == 125
    assert usage.cached_input_tokens == 50
    assert usage.output_tokens == 15
    assert usage.reasoning_output_tokens == 5
    assert usage.computed_total_tokens == 145


def test_parse_gemini_usage_uses_largest_token_block() -> None:
    text = """
    partial stats {"tokens":{"prompt":100,"candidates":4,"total":108,"cached":40,"thoughts":4}}
    final stats {"tokens":{"prompt":120,"candidates":6,"total":132,"cached":50,"thoughts":6}}
    """

    usage = parse_gemini_usage(text)

    assert usage is not None
    assert usage.input_tokens == 120
    assert usage.cached_input_tokens == 50
    assert usage.output_tokens == 6
    assert usage.reasoning_output_tokens == 6
    assert usage.total_tokens == 132


def test_augment_spawn_record_backfills_provider_model_tokens_and_cost(tmp_path: Path) -> None:
    log_path = tmp_path / "codex.log"
    log_path.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1_000,
                    "cached_input_tokens": 250,
                    "output_tokens": 100,
                    "reasoning_output_tokens": 20,
                },
            }
        )
    )

    record = augment_spawn_record(
        {
            "spawn_id": "s1",
            "skill": "lab-finalize-pr",
            "effective_settings": {"model": "gpt-5.4"},
            "log_path": str(log_path),
        }
    )

    assert record["provider"] == "codex-cli"
    assert record["model"] == "gpt-5.4"
    assert record["input_tokens"] == 1_000
    assert record["cached_input_tokens"] == 250
    assert record["output_tokens"] == 100
    assert record["reasoning_output_tokens"] == 20
    assert record["total_tokens"] == 1_120
    assert record["cost_usd_estimate"] is not None


def test_augment_spawn_record_reads_model_from_codex_log_header(tmp_path: Path) -> None:
    log_path = tmp_path / "codex.log"
    log_path.write_text(
        '# effective_settings: {"model": "gpt-5.4"}\n'
        + json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        )
    )

    record = augment_spawn_record(
        {
            "spawn_id": "s1",
            "skill": "trial-critic",
            "log_path": str(log_path),
        }
    )

    assert record["model"] == "gpt-5.4"
    assert record["cost_usd_estimate"] is not None


def test_parse_usage_from_log_resolves_mirrored_remote_runs_path(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    import openharness.lab.usage as usage_mod

    mirrored = tmp_path / "runs" / "lab" / "logs" / "spawn.log"
    mirrored.parent.mkdir(parents=True)
    mirrored.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        )
    )
    monkeypatch.setattr(usage_mod, "REPO_ROOT", tmp_path)

    usage = parse_usage_from_log(
        "/home/pier_ridgesecurity_ai/OpenHarness/runs/lab/logs/spawn.log",
        provider="codex-cli",
    )

    assert usage is not None
    assert usage.input_tokens == 10
    assert usage.output_tokens == 2
