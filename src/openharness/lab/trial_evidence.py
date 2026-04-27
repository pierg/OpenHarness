"""Deterministic evidence digest for per-trial critique.

The trial critic should reason over a compact, stable artifact first
and expand into raw files only when that digest is ambiguous. This
module builds that artifact from the trial directory without any LLM
judgment.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from openharness.lab import critic_io

MAX_TEXT_CHARS = 3_000
MAX_EVENT_LINES = 50_000
MAX_ARTIFACTS = 80


class TrialEvidenceError(RuntimeError):
    """Raised when a trial directory lacks required evidence."""


def build_trial_evidence(trial_dir: Path) -> dict[str, Any]:
    """Return deterministic, model-ready evidence for ``trial_dir``."""
    trial_dir = Path(trial_dir)
    result_path = trial_dir / "result.json"
    if not result_path.is_file():
        raise TrialEvidenceError(f"{result_path} is missing")
    result = _read_json(result_path)
    if not isinstance(result, dict):
        raise TrialEvidenceError(f"{result_path} must contain a JSON object")

    trajectory = _read_json(trial_dir / "agent" / "trajectory.json")
    messages = _read_jsonl(trial_dir / "messages.jsonl", limit=500)
    steps = _trajectory_steps(trajectory, messages)
    events = _summarize_events(trial_dir / "events.jsonl")
    agent_config_path, agent_config = _find_agent_config(trial_dir)
    verifier = _summarize_verifier(result, trial_dir)
    outcome = _determine_outcome(result, verifier)
    first_steps = [_summarize_step(s) for s in steps[:4]]
    final_steps = [_summarize_step(s) for s in steps[-8:]] if len(steps) > 4 else []

    digest: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trial_dir": str(trial_dir),
        "trial_id": result.get("id") or result.get("trial_name") or trial_dir.name,
        "trial_name": result.get("trial_name") or trial_dir.name,
        "task_name": result.get("task_name"),
        "task_id": result.get("task_id"),
        "task_checksum": result.get("task_checksum"),
        "outcome": outcome,
        "result_summary": _result_summary(result),
        "agent_config": _agent_config_summary(agent_config, agent_config_path),
        "verifier": verifier,
        "trajectory": {
            "steps_total": len(steps),
            "first_steps": first_steps,
            "final_steps": final_steps,
            "tool_call_counts": _tool_call_counts(steps),
            "repeated_tool_calls": _repeated_tool_calls(steps),
            "task_instruction": _task_instruction(steps),
        },
        "events": events,
        "available_artifacts": _available_artifacts(trial_dir),
        "expansion_policy": {
            "default": "Use this digest first.",
            "allowed_when": [
                "confidence would be below 0.75",
                "outcome or verifier cause is ambiguous",
                "component effect requires exact trajectory evidence",
                "digest excerpts are insufficient for a concrete root cause",
            ],
            "bounds": (
                "Read targeted files or specific trajectory turns only; do not "
                "read full trajectory/messages unless explicitly justified."
            ),
        },
        "ambiguity_flags": _ambiguity_flags(result, steps, verifier),
    }
    return digest


def write_trial_evidence(trial_dir: Path) -> Path:
    """Build and persist ``critic/trial-evidence.json``."""
    payload = build_trial_evidence(trial_dir)
    return critic_io.write_trial_evidence(trial_dir, payload)


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None


def _read_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _trajectory_steps(trajectory: Any, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(trajectory, dict):
        raw_steps = trajectory.get("steps")
        if isinstance(raw_steps, list):
            return [s for s in raw_steps if isinstance(s, dict)]
    if isinstance(trajectory, list):
        return [s for s in trajectory if isinstance(s, dict)]
    out: list[dict[str, Any]] = []
    for i, msg in enumerate(messages, start=1):
        role = msg.get("role") or msg.get("source") or msg.get("type")
        content = msg.get("content") or msg.get("message") or msg
        out.append({"step_id": i, "source": role, "message": content})
    return out


def _summarize_step(step: dict[str, Any]) -> dict[str, Any]:
    tool_calls = []
    for call in step.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        tool_calls.append(
            {
                "name": call.get("function_name") or call.get("name"),
                "arguments": _truncate(_jsonish(call.get("arguments"))),
            }
        )
    observation = step.get("observation")
    return {
        "step_id": step.get("step_id"),
        "source": step.get("source") or step.get("role"),
        "message": _truncate(_text(step.get("message") or step.get("content"))),
        "tool_calls": tool_calls,
        "observation": _summarize_observation(observation),
    }


def _summarize_observation(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict) and isinstance(value.get("results"), list):
        parts = []
        for item in value["results"][:3]:
            if isinstance(item, dict):
                parts.append(_text(item.get("content") or item))
        return _truncate("\n".join(parts))
    return _truncate(_text(value))


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return _jsonish(value)


def _jsonish(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)


def _truncate(text: str, *, limit: int = MAX_TEXT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n...[truncated {omitted} chars]"


def _tool_call_key(call: dict[str, Any]) -> str:
    name = call.get("function_name") or call.get("name") or "(unknown)"
    return f"{name}:{_jsonish(call.get('arguments'))}"


def _tool_call_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for step in steps:
        for call in step.get("tool_calls") or []:
            if isinstance(call, dict):
                counts[str(call.get("function_name") or call.get("name") or "(unknown)")] += 1
    return dict(counts.most_common())


def _repeated_tool_calls(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    examples: dict[str, dict[str, Any]] = {}
    for step in steps:
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            key = _tool_call_key(call)
            counts[key] += 1
            examples.setdefault(key, call)
    repeated = []
    for key, count in counts.most_common(12):
        if count < 2:
            continue
        call = examples[key]
        repeated.append(
            {
                "count": count,
                "name": call.get("function_name") or call.get("name"),
                "arguments": _truncate(_jsonish(call.get("arguments")), limit=800),
            }
        )
    return repeated


def _task_instruction(steps: list[dict[str, Any]]) -> str | None:
    for step in steps:
        source = str(step.get("source") or step.get("role") or "").lower()
        if source == "user":
            return _truncate(_text(step.get("message") or step.get("content")))
    return None


def _summarize_events(path: Path) -> dict[str, Any]:
    rows = _read_jsonl(path, limit=MAX_EVENT_LINES)
    type_counts: Counter[str] = Counter(str(r.get("type") or "unknown") for r in rows)
    models: Counter[str] = Counter(str(r.get("model")) for r in rows if r.get("model"))
    input_tokens = 0
    output_tokens = 0
    for row in rows:
        usage = row.get("usage")
        if not isinstance(usage, dict):
            continue
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
    return {
        "path": str(path) if path.is_file() else None,
        "rows_read": len(rows),
        "type_counts": dict(type_counts.most_common()),
        "models": dict(models.most_common()),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _find_agent_config(trial_dir: Path) -> tuple[Path | None, dict[str, Any]]:
    for parent in [trial_dir, *trial_dir.parents]:
        path = parent / "agent.resolved.yaml"
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                return path, {}
            return path, data if isinstance(data, dict) else {}
    return None, {}


def _agent_config_summary(config: dict[str, Any], path: Path | None) -> dict[str, Any]:
    return {
        "path": str(path) if path else None,
        "name": config.get("name"),
        "architecture": config.get("architecture"),
        "model": config.get("model"),
        "max_turns": config.get("max_turns"),
        "max_tokens": config.get("max_tokens"),
        "tools": config.get("tools") or [],
        "components": config.get("components") or [],
    }


def _summarize_verifier(result: dict[str, Any], trial_dir: Path) -> dict[str, Any]:
    verifier = result.get("verifier") if isinstance(result.get("verifier"), dict) else {}
    verifier_result = result.get("verifier_result")
    metadata = verifier.get("metadata") if isinstance(verifier.get("metadata"), dict) else {}
    parser_results = metadata.get("parser_results") if isinstance(metadata, dict) else {}
    tests = parser_results.get("tests") if isinstance(parser_results, dict) else []
    failed_tests = []
    passed_tests = 0
    if isinstance(tests, list):
        for test in tests:
            if not isinstance(test, dict):
                continue
            status = str(test.get("status") or test.get("outcome") or "").lower()
            if status in ("passed", "pass", "ok"):
                passed_tests += 1
            elif status:
                failed_tests.append(
                    {
                        "name": test.get("name") or test.get("id"),
                        "status": status,
                        "message": _truncate(
                            _text(test.get("message") or test.get("error")), limit=800
                        ),
                    }
                )
    run_log = trial_dir / "verifier" / "run.log"
    if not run_log.is_file():
        run_log = trial_dir / "trial.log"
    excerpt = None
    if run_log.is_file():
        excerpt = _tail_text(run_log, max_chars=2_000)
    reward = verifier["reward"] if "reward" in verifier else result.get("reward")
    return {
        "verifier_result": verifier_result,
        "reward": reward,
        "metadata_keys": sorted(metadata.keys()) if isinstance(metadata, dict) else [],
        "passed_tests": passed_tests,
        "failed_tests": failed_tests[:12],
        "log_excerpt": excerpt,
    }


def _tail_text(path: Path, *, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def _determine_outcome(result: dict[str, Any], verifier: dict[str, Any]) -> str:
    if result.get("exception_info"):
        return "errored"
    raw = result.get("verifier_result")
    if isinstance(raw, str):
        lowered = raw.lower()
        if lowered in ("passed", "pass", "success", "succeeded"):
            return "passed"
        if lowered in ("failed", "fail"):
            return "failed"
    reward = verifier.get("reward")
    if isinstance(reward, (int, float)):
        return "passed" if reward >= 1.0 else "failed"
    if verifier.get("failed_tests"):
        return "failed"
    return "errored" if not result.get("verifier") else "failed"


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    agent_result = result.get("agent_result")
    metadata = agent_result.get("metadata") if isinstance(agent_result, dict) else {}
    summary = metadata.get("summary") if isinstance(metadata, dict) else {}
    exception = result.get("exception_info")
    return {
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
        "trial_uri": result.get("trial_uri"),
        "exception_type": exception.get("exception_type") if isinstance(exception, dict) else None,
        "exception_message": _truncate(
            exception.get("exception_message") if isinstance(exception, dict) else "",
            limit=1_500,
        ),
        "final_text": _truncate(summary.get("final_text") if isinstance(summary, dict) else ""),
        "agent_input_tokens": summary.get("input_tokens") if isinstance(summary, dict) else None,
        "agent_output_tokens": summary.get("output_tokens") if isinstance(summary, dict) else None,
        "trace_url": metadata.get("trace_url") if isinstance(metadata, dict) else None,
    }


def _available_artifacts(trial_dir: Path) -> list[dict[str, Any]]:
    artifacts = []
    for path in sorted(trial_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(trial_dir)
        except ValueError:
            rel = path
        artifacts.append({"path": str(rel), "size_bytes": path.stat().st_size})
        if len(artifacts) >= MAX_ARTIFACTS:
            break
    return artifacts


def _ambiguity_flags(
    result: dict[str, Any],
    steps: list[dict[str, Any]],
    verifier: dict[str, Any],
) -> list[str]:
    flags: list[str] = []
    if not steps:
        flags.append("missing_trajectory")
    if result.get("exception_info") and not steps:
        flags.append("agent_errored_without_trajectory")
    if (
        result.get("verifier")
        and not verifier.get("failed_tests")
        and verifier.get("reward") in (0, 0.0)
    ):
        flags.append("failed_without_failed_test_details")
    if not result.get("verifier") and not result.get("exception_info"):
        flags.append("missing_verifier_and_exception")
    return flags
