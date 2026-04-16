"""Results collection and summary generation."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from openharness.experiments.manifest import ExperimentManifest, TrialError
from openharness.experiments.paths import RelPath, resolve_rel


class ExperimentResultRow(BaseModel):
    experiment_id: str
    instance_id: str
    dataset: str
    leg_id: str
    agent_id: str
    trial_id: str
    task_name: str
    trial_dir: RelPath
    score: float | None
    status: Literal["passed", "failed", "errored"]
    error_type: str | None = None
    error_phase: str | None = None
    error_message: str | None = None
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

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentSummary(BaseModel):
    n_trials: int
    n_passed: int
    n_failed: int
    n_errored: int
    n_errored_by_phase: dict[str, int] = Field(default_factory=dict)
    pass_rate: float | None
    mean_score: float | None
    total_tokens: int
    total_cost_usd: float
    median_duration_sec: float | None

    model_config = ConfigDict(extra="forbid", frozen=True)


class ResultsSummary(BaseModel):
    by_leg: dict[str, AgentSummary]

    model_config = ConfigDict(extra="forbid", frozen=True)


def _error_fields(error: TrialError | None) -> tuple[str | None, str | None, str | None]:
    if error is None:
        return None, None, None
    phase = error.phase.value if hasattr(error.phase, "value") else str(error.phase)
    return error.exception_type, phase, error.message


def collect_results(
    manifest: ExperimentManifest, *, experiment_root: Path
) -> list[ExperimentResultRow]:
    rows: list[ExperimentResultRow] = []

    for leg in manifest.legs:
        trials = leg.trials
        if not trials and leg.harbor_result_path:
            harbor_res_path = resolve_rel(experiment_root, leg.harbor_result_path)
            if harbor_res_path.exists():
                try:
                    from openharness.experiments.backends.harbor import HarborBackend
                    from openharness.runs.harbor import _collect_trial_results

                    harbor_trials = _collect_trial_results(harbor_res_path)
                    backend = HarborBackend()
                    trials = tuple(
                        backend._trial_record_from_harbor_result(t, experiment_root)
                        for t in harbor_trials
                    )
                except Exception:
                    pass

        for trial in trials:
            if trial.passed:
                status: Literal["passed", "failed", "errored"] = "passed"
            elif trial.error is not None:
                status = "errored"
            else:
                status = "failed"

            err_type, err_phase, err_message = _error_fields(trial.error)

            rows.append(
                ExperimentResultRow(
                    experiment_id=manifest.experiment_id,
                    instance_id=manifest.instance_id,
                    dataset=manifest.dataset,
                    leg_id=leg.leg_id,
                    agent_id=leg.agent_id,
                    trial_id=trial.trial_id,
                    task_name=trial.task_name,
                    trial_dir=trial.trial_dir,
                    score=trial.score,
                    status=status,
                    error_type=err_type,
                    error_phase=err_phase,
                    error_message=err_message,
                    model=trial.model,
                    input_tokens=trial.input_tokens,
                    output_tokens=trial.output_tokens,
                    total_tokens=trial.total_tokens,
                    cost_usd=trial.cost_usd,
                    duration_sec=trial.duration_sec,
                    agent_duration_sec=trial.agent_duration_sec,
                    env_setup_duration_sec=trial.env_setup_duration_sec,
                    verifier_duration_sec=trial.verifier_duration_sec,
                    trace_id=trial.trace_id,
                    trace_url=trial.trace_url,
                )
            )
    return rows


def summarize_results(rows: list[ExperimentResultRow]) -> ResultsSummary:
    by_leg: dict[str, AgentSummary] = {}
    for leg_id in sorted({row.leg_id for row in rows}):
        leg_rows = [row for row in rows if row.leg_id == leg_id]
        scores = [row.score for row in leg_rows if row.score is not None]
        durations = [row.duration_sec for row in leg_rows if row.duration_sec is not None]
        total_cost = sum(row.cost_usd or 0.0 for row in leg_rows)
        total_tokens = sum(row.total_tokens or 0 for row in leg_rows)
        n_passed = sum(1 for row in leg_rows if row.status == "passed")
        n_errored = sum(1 for row in leg_rows if row.status == "errored")
        n_failed = sum(1 for row in leg_rows if row.status == "failed")

        errored_by_phase: dict[str, int] = {}
        for row in leg_rows:
            if row.status != "errored":
                continue
            phase = row.error_phase or "unknown"
            errored_by_phase[phase] = errored_by_phase.get(phase, 0) + 1

        by_leg[leg_id] = AgentSummary(
            n_trials=len(leg_rows),
            n_passed=n_passed,
            n_failed=n_failed,
            n_errored=n_errored,
            n_errored_by_phase=errored_by_phase,
            pass_rate=n_passed / len(leg_rows) if leg_rows else None,
            mean_score=statistics.fmean(scores) if scores else None,
            total_tokens=total_tokens,
            total_cost_usd=round(total_cost, 10),
            median_duration_sec=statistics.median(durations) if durations else None,
        )
    return ResultsSummary(by_leg=by_leg)


def write_results(
    rows: list[ExperimentResultRow],
    summary: ResultsSummary,
    *,
    experiment_root: Path,
) -> None:
    results_dir = experiment_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    (results_dir / "rows.json").write_text(
        json.dumps([row.model_dump(mode="json") for row in rows], indent=2) + "\n",
        encoding="utf-8",
    )

    if rows:
        with (results_dir / "rows.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].model_dump(mode="json")))
            writer.writeheader()
            for row in rows:
                writer.writerow(row.model_dump(mode="json"))

    lines = [
        "# Experiment Summary",
        "",
        (
            "| Leg | Trials | Passed | Failed | Errored | Errors by Phase | Pass Rate | "
            "Mean Score | Tokens | Cost | Median Time |"
        ),
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for leg_id, stats in summary.by_leg.items():
        pass_rate = _fmt_float(stats.pass_rate)
        mean_score = _fmt_float(stats.mean_score)
        cost = _fmt_float(stats.total_cost_usd)
        median = _fmt_float(stats.median_duration_sec)
        phase_str = (
            ", ".join(f"{k}={v}" for k, v in sorted(stats.n_errored_by_phase.items())) or "-"
        )
        lines.append(
            f"| {leg_id} | {stats.n_trials} | {stats.n_passed} | {stats.n_failed} | "
            f"{stats.n_errored} | {phase_str} | {pass_rate} | {mean_score} | "
            f"{stats.total_tokens} | {cost} | {median} |"
        )
    (results_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"
