"""Tests for experiment results aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from openharness.experiments.manifest import (
    ExperimentManifest,
    LegAggregate,
    LegRecord,
    LegResultStatus,
    LegStatus,
    Reproducibility,
    TrialError,
    TrialErrorPhase,
    TrialRecord,
)
from openharness.experiments.results import (
    collect_results,
    summarize_results,
    write_results,
)


def _build_manifest() -> ExperimentManifest:
    now = datetime.now(timezone.utc)
    trials = (
        TrialRecord(
            trial_id="t1",
            task_name="task-a",
            trial_dir=Path("legs/default/harbor/job/task-a__t1"),
            score=1.0,
            passed=True,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=0.01,
            duration_sec=5.0,
        ),
        TrialRecord(
            trial_id="t2",
            task_name="task-b",
            trial_dir=Path("legs/default/harbor/job/task-b__t2"),
            score=0.0,
            passed=False,
            error=TrialError(
                exception_type="RuntimeError",
                message="docker failed",
                phase=TrialErrorPhase.ENV_SETUP,
            ),
            duration_sec=2.5,
        ),
    )

    leg = LegRecord(
        leg_id="basic",
        agent_id="basic",
        status=LegStatus.SUCCEEDED,
        result_status=LegResultStatus.PARTIAL,
        started_at=now,
        finished_at=now,
        duration_sec=1.0,
        harbor_dir=Path("legs/basic/harbor"),
        harbor_result_path=Path("legs/basic/harbor/job/result.json"),
        agent_config_path=Path("legs/basic/agent.resolved.yaml"),
        trials=trials,
        aggregate=LegAggregate(
            n_trials=2,
            n_passed=1,
            n_failed=0,
            n_errored=1,
            n_errored_by_phase={"env_setup": 1},
            mean_score=0.5,
            total_input_tokens=100,
            total_output_tokens=50,
            total_tokens=150,
            total_cost_usd=0.01,
        ),
    )
    return ExperimentManifest(
        experiment_id="exp-1",
        instance_id="inst-1",
        dataset="ds",
        spec_path=Path("config.source.yaml"),
        resolved_spec_path=Path("config.resolved.yaml"),
        created_at=now,
        updated_at=now,
        reproducibility=Reproducibility(
            git_sha=None,
            git_dirty=False,
            harbor_version=None,
            openharness_version="0.0.0",
            python_version="3.12.0",
            hostname="h",
        ),
        legs=(leg,),
    )


def test_collect_results_classifies_statuses(tmp_path: Path) -> None:
    manifest = _build_manifest()
    rows = collect_results(manifest, experiment_root=tmp_path)
    assert [row.status for row in rows] == ["passed", "errored"]
    err_row = rows[1]
    assert err_row.error_type == "RuntimeError"
    assert err_row.error_phase == "env_setup"


def test_summary_and_write_results(tmp_path: Path) -> None:
    manifest = _build_manifest()
    rows = collect_results(manifest, experiment_root=tmp_path)
    summary = summarize_results(rows)

    stats = summary.by_leg["basic"]
    assert stats.n_trials == 2
    assert stats.n_passed == 1
    assert stats.n_errored == 1
    assert stats.n_errored_by_phase == {"env_setup": 1}
    assert stats.pass_rate == 0.5
    assert stats.total_tokens == 150

    write_results(rows, summary, experiment_root=tmp_path)
    md = (tmp_path / "results" / "summary.md").read_text(encoding="utf-8")
    assert "env_setup=1" in md
    assert "basic" in md
