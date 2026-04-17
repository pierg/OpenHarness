"""High-level Harbor run orchestration."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

from openharness.harbor import run_harbor_job
from openharness.observability import rewrite_trace_url_for_public, score_trace
from openharness.runs.specs import HarborAgentRunSpec, HarborJobResult, TrialResult
from openharness.services.runs import generate_run_id

log = logging.getLogger(__name__)


def run_harbor_agent(spec: HarborAgentRunSpec) -> HarborJobResult:
    """Launch a Harbor job and return a HarborJobResult with per-trial data."""
    job_id = spec.run_id or generate_run_id()
    run_cwd = Path(spec.cwd).expanduser().resolve()

    from openharness.config.paths import get_project_runs_dir

    # Only touch the on-disk ``<cwd>/runs/<job_id>/`` layout when Harbor needs
    # it for the fallback jobs_dir. When jobs_dir is explicit (experiments),
    # avoid creating an empty sibling directory.
    if spec.job.jobs_dir is not None:
        jobs_dir = spec.job.jobs_dir
        job_dir = jobs_dir.parent
    else:
        job_dir = get_project_runs_dir(run_cwd) / job_id
        jobs_dir = job_dir / "harbor_jobs"

    log.debug("Harbor job starting: job_id=%s  job_dir=%s", job_id, job_dir)

    job_spec = replace(
        spec.job,
        job_name=job_id,
        jobs_dir=jobs_dir,
        run_cwd=run_cwd,
        metadata={
            **spec.job.metadata,
            **spec.metadata,
        },
    )
    harbor_result = run_harbor_job(job_spec)

    trials = _collect_trial_results(harbor_result.result_path)

    log.debug(
        "Harbor job finished: job_id=%s  trials=%d  passed=%d",
        job_id,
        len(trials),
        sum(1 for t in trials if t.passed),
    )

    return HarborJobResult(
        job_id=job_id,
        job_dir=job_dir,
        harbor_result_path=harbor_result.result_path,
        trials=trials,
    )


def _collect_trial_results(job_result_path: Path) -> list[TrialResult]:
    """Walk the Harbor job directory and build a TrialResult for each trial."""
    job_dir = job_result_path.parent
    if not job_dir.exists():
        return []

    trials: list[TrialResult] = []
    for trial_dir in sorted(job_dir.iterdir()):
        if not trial_dir.is_dir():
            continue

        trial_id = trial_dir.name
        task_name = trial_id.rsplit("__", 1)[0] if "__" in trial_id else trial_id

        harbor_data = _read_harbor_result(trial_dir)
        oh_data = _read_openharness_run(trial_dir)

        agent_result = harbor_data.get("agent_result") or {}
        metadata = agent_result.get("metadata") or {}
        verifier = harbor_data.get("verifier_result") or {}
        rewards = verifier.get("rewards") or {}
        score = rewards.get("reward") if rewards else None
        exception = harbor_data.get("exception_info")
        trace_id = oh_data.get("trace_id") or metadata.get("trace_id")
        raw_trace_url = oh_data.get("trace_url") or metadata.get("trace_url")
        trace_url = (
            rewrite_trace_url_for_public(str(raw_trace_url)) if raw_trace_url else None
        )

        score_value: float | None = float(score) if isinstance(score, (int, float)) else None
        agent_duration = _phase_duration(harbor_data.get("agent_execution"))

        # Push verifier outcomes into Langfuse so the trace UI shows pass/fail
        # alongside cost/tokens. Failures here are non-fatal.
        if trace_id:
            _emit_langfuse_scores(
                trace_id=trace_id,
                score=score_value,
                error=exception,
                cost_usd=agent_result.get("cost_usd"),
                duration_sec=agent_duration,
                rewards=rewards,
            )

        # Persist the externally reachable trace URL back into ``run.json`` so
        # downstream consumers (rows.json, summary.md) reference a clickable
        # URL rather than the container-internal Langfuse host.
        if trace_url and trace_url != raw_trace_url and oh_data:
            _maybe_rewrite_trace_url_in_run_json(trial_dir, trace_url)

        trials.append(
            TrialResult(
                trial_id=trial_id,
                task_name=task_name,
                trial_dir=trial_dir,
                score=score_value,
                trace_id=trace_id,
                trace_url=trace_url,
                error=str(exception) if exception else None,
                input_tokens=agent_result.get("n_input_tokens"),
                output_tokens=agent_result.get("n_output_tokens"),
                cost_usd=agent_result.get("cost_usd"),
                model=metadata.get("model"),
                duration_sec=_duration_sec(
                    harbor_data.get("started_at"), harbor_data.get("finished_at")
                ),
                agent_duration_sec=agent_duration,
                env_setup_duration_sec=_phase_duration(harbor_data.get("environment_setup")),
                verifier_duration_sec=_phase_duration(harbor_data.get("verifier")),
            )
        )

    return trials


def _emit_langfuse_scores(
    *,
    trace_id: str,
    score: float | None,
    error: object | None,
    cost_usd: float | None,
    duration_sec: float | None,
    rewards: dict,
) -> None:
    """Attach verifier + derived scores to the Langfuse trace.

    Errors are intentionally swallowed: Langfuse is observability, not a
    correctness dependency for the experiment runner.
    """
    if score is not None:
        score_trace(trace_id=trace_id, name="reward", value=float(score))
        score_trace(
            trace_id=trace_id,
            name="passed",
            value="true" if score > 0 else "false",
            data_type="CATEGORICAL",
        )
    if error is not None:
        score_trace(
            trace_id=trace_id,
            name="errored",
            value="true",
            data_type="CATEGORICAL",
            comment=str(error)[:256],
        )
    if cost_usd is not None:
        score_trace(trace_id=trace_id, name="cost_usd", value=float(cost_usd))
    if duration_sec is not None:
        score_trace(trace_id=trace_id, name="agent_duration_sec", value=float(duration_sec))
    # Surface any additional reward dimensions (sub-rewards) the verifier
    # produced so they show up next to the primary score.
    if isinstance(rewards, dict):
        for key, value in rewards.items():
            if key == "reward" or not isinstance(value, (int, float)):
                continue
            score_trace(trace_id=trace_id, name=f"reward.{key}", value=float(value))


def _maybe_rewrite_trace_url_in_run_json(trial_dir: Path, public_url: str) -> None:
    """Rewrite ``trace_url`` inside ``run.json`` to the externally reachable host."""
    path = trial_dir / "run.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if data.get("trace_url") == public_url:
        return
    data["trace_url"] = public_url
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    except OSError:
        log.debug("Failed to rewrite trace_url in %s", path, exc_info=True)


def _read_harbor_result(trial_dir: Path) -> dict:
    path = trial_dir / "result.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_openharness_run(trial_dir: Path) -> dict:
    path = trial_dir / "run.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _duration_sec(started: str | None, finished: str | None) -> float | None:
    if not started or not finished:
        return None
    try:
        from datetime import datetime

        s = datetime.fromisoformat(started.replace("Z", "+00:00"))
        f = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        return (f - s).total_seconds()
    except (ValueError, TypeError):
        return None


def _phase_duration(phase: dict | None) -> float | None:
    if not phase:
        return None
    return _duration_sec(phase.get("started_at"), phase.get("finished_at"))
