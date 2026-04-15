"""Result collection and deterministic summaries for experiment manifests."""

from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class ExperimentResultRow(BaseModel):
    """Normalized row for one benchmark trial."""

    experiment_id: str
    run_spec_id: str
    agent_id: str
    dataset: str
    task_name: str
    trial_id: str
    score: float | None
    passed: bool
    error: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None
    duration_sec: float | None
    agent_duration_sec: float | None
    env_setup_duration_sec: float | None
    verifier_duration_sec: float | None
    trace_id: str | None
    trace_url: str | None
    trial_dir: str

    model_config = ConfigDict(extra="forbid", frozen=True)


def collect_experiment_results(manifest_path: str | Path) -> list[ExperimentResultRow]:
    """Collect normalized trial rows from an experiment manifest."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    experiment_id = manifest["experiment_id"]
    rows: list[ExperimentResultRow] = []
    for job in manifest.get("jobs", []):
        harbor_result_path = job.get("harbor_result_path")
        if not harbor_result_path:
            continue
        job_result_path = Path(harbor_result_path)
        job_dir = job_result_path.parent
        if not job_dir.exists():
            continue
        for trial_dir in sorted(path for path in job_dir.iterdir() if path.is_dir()):
            harbor_data = _read_json(trial_dir / "result.json")
            if not harbor_data:
                continue
            rows.append(
                _row_from_trial(
                    experiment_id=experiment_id,
                    job=job,
                    trial_dir=trial_dir,
                    harbor_data=harbor_data,
                )
            )
    return rows


def summarize_experiment_results(rows: list[ExperimentResultRow]) -> dict[str, Any]:
    """Return deterministic aggregate statistics for normalized result rows."""
    by_agent: dict[str, dict[str, Any]] = {}
    for agent_id in sorted({row.agent_id for row in rows}):
        agent_rows = [row for row in rows if row.agent_id == agent_id]
        scores = [row.score for row in agent_rows if row.score is not None]
        durations = [row.duration_sec for row in agent_rows if row.duration_sec is not None]
        total_cost = sum(row.cost_usd or 0.0 for row in agent_rows)
        total_tokens = sum(row.total_tokens or 0 for row in agent_rows)
        by_agent[agent_id] = {
            "n_trials": len(agent_rows),
            "n_passed": sum(1 for row in agent_rows if row.passed),
            "n_errors": sum(1 for row in agent_rows if row.error),
            "pass_rate": (
                sum(1 for row in agent_rows if row.passed) / len(agent_rows)
                if agent_rows
                else None
            ),
            "mean_score": statistics.fmean(scores) if scores else None,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 10),
            "median_duration_sec": statistics.median(durations) if durations else None,
        }
    return {"by_agent": by_agent}


def write_results_json(rows: list[ExperimentResultRow], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([row.model_dump(mode="json") for row in rows], indent=2) + "\n",
        encoding="utf-8",
    )


def write_results_csv(rows: list[ExperimentResultRow], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        target.write_text("", encoding="utf-8")
        return
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].model_dump(mode="json")))
        writer.writeheader()
        for row in rows:
            writer.writerow(row.model_dump(mode="json"))


def write_summary_markdown(summary: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Experiment Summary",
        "",
        "| Agent | Trials | Passed | Pass Rate | Mean Score | Errors | Tokens | Cost | Median Time |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for agent_id, stats in summary.get("by_agent", {}).items():
        pass_rate = _fmt_float(stats["pass_rate"])
        mean_score = _fmt_float(stats["mean_score"])
        cost = _fmt_float(stats["total_cost_usd"])
        median = _fmt_float(stats["median_duration_sec"])
        lines.append(
            f"| {agent_id} | {stats['n_trials']} | {stats['n_passed']} | {pass_rate} | "
            f"{mean_score} | {stats['n_errors']} | {stats['total_tokens']} | {cost} | {median} |"
        )
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _row_from_trial(
    *,
    experiment_id: str,
    job: dict[str, Any],
    trial_dir: Path,
    harbor_data: dict[str, Any],
) -> ExperimentResultRow:
    trial_id = trial_dir.name
    task_name = harbor_data.get("task_name") or (
        trial_id.rsplit("__", 1)[0] if "__" in trial_id else trial_id
    )
    agent_result = harbor_data.get("agent_result") or {}
    metadata = agent_result.get("metadata") or {}
    rewards = (harbor_data.get("verifier_result") or {}).get("rewards") or {}
    score = rewards.get("reward")
    exception = harbor_data.get("exception_info")
    input_tokens = agent_result.get("n_input_tokens")
    output_tokens = agent_result.get("n_output_tokens")
    total_tokens = None
    if input_tokens is not None or output_tokens is not None:
        total_tokens = (input_tokens or 0) + (output_tokens or 0)
    return ExperimentResultRow(
        experiment_id=experiment_id,
        run_spec_id=job["run_spec_id"],
        agent_id=job["agent_id"],
        dataset=job["dataset"],
        task_name=task_name,
        trial_id=trial_id,
        score=float(score) if isinstance(score, (int, float)) else None,
        passed=isinstance(score, (int, float)) and score > 0,
        error=str(exception) if exception else None,
        model=metadata.get("model"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=agent_result.get("cost_usd"),
        duration_sec=_duration_sec(harbor_data.get("started_at"), harbor_data.get("finished_at")),
        agent_duration_sec=_phase_duration(harbor_data.get("agent_execution")),
        env_setup_duration_sec=_phase_duration(harbor_data.get("environment_setup")),
        verifier_duration_sec=_phase_duration(harbor_data.get("verifier")),
        trace_id=metadata.get("trace_id"),
        trace_url=metadata.get("trace_url"),
        trial_dir=str(trial_dir),
    )


def _phase_duration(phase: dict[str, Any] | None) -> float | None:
    if not phase:
        return None
    return _duration_sec(phase.get("started_at"), phase.get("finished_at"))


def _duration_sec(started: str | None, finished: str | None) -> float | None:
    if not started or not finished:
        return None
    try:
        return (_parse_time(finished) - _parse_time(started)).total_seconds()
    except ValueError:
        return None


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"
