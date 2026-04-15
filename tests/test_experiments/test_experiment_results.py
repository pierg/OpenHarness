from __future__ import annotations

import json
from pathlib import Path

from openharness.experiments.results import (
    collect_experiment_results,
    summarize_experiment_results,
)


def test_collect_experiment_results_normalizes_harbor_trials(tmp_path: Path):
    job_dir = tmp_path / "harbor_jobs" / "run-oh-1"
    job_dir.mkdir(parents=True)
    result_path = job_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "stats": {
                    "evals": {
                        "openharness__terminal-bench": {
                            "metrics": [{"mean": 0.5}],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    trial_dir = job_dir / "build-pov-ray__abc123"
    trial_dir.mkdir()
    (trial_dir / "result.json").write_text(
        json.dumps(
            {
                "agent_result": {
                    "n_input_tokens": 100,
                    "n_output_tokens": 20,
                    "cost_usd": 0.01,
                    "metadata": {
                        "model": "gemini-2.5-flash",
                        "trace_id": "trace-1",
                        "trace_url": "http://trace/1",
                    },
                },
                "verifier_result": {"rewards": {"reward": 1.0}},
                "started_at": "2026-04-14T10:00:00.000000Z",
                "finished_at": "2026-04-14T10:01:00.000000Z",
                "agent_execution": {
                    "started_at": "2026-04-14T10:00:10.000000Z",
                    "finished_at": "2026-04-14T10:00:40.000000Z",
                },
                "environment_setup": {
                    "started_at": "2026-04-14T10:00:00.000000Z",
                    "finished_at": "2026-04-14T10:00:10.000000Z",
                },
                "verifier": {
                    "started_at": "2026-04-14T10:00:40.000000Z",
                    "finished_at": "2026-04-14T10:00:50.000000Z",
                },
            }
        ),
        encoding="utf-8",
    )

    manifest_path = tmp_path / "experiment.json"
    manifest_path.write_text(
        json.dumps(
            {
                "experiment_id": "tb2-baseline",
                "jobs": [
                    {
                        "job_id": "tb2-baseline.smoke.default",
                        "run_spec_id": "smoke",
                        "agent_id": "default",
                        "dataset": "terminal-bench@2.0",
                        "openharness_run_id": "run-oh-1",
                        "harbor_result_path": str(result_path),
                        "status": "succeeded",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rows = collect_experiment_results(manifest_path)

    assert len(rows) == 1
    row = rows[0]
    assert row.experiment_id == "tb2-baseline"
    assert row.run_spec_id == "smoke"
    assert row.agent_id == "default"
    assert row.dataset == "terminal-bench@2.0"
    assert row.task_name == "build-pov-ray"
    assert row.score == 1.0
    assert row.passed is True
    assert row.model == "gemini-2.5-flash"
    assert row.input_tokens == 100
    assert row.output_tokens == 20
    assert row.total_tokens == 120
    assert row.cost_usd == 0.01
    assert row.duration_sec == 60.0
    assert row.agent_duration_sec == 30.0
    assert row.env_setup_duration_sec == 10.0
    assert row.verifier_duration_sec == 10.0
    assert row.trace_id == "trace-1"
    assert row.trace_url == "http://trace/1"
    assert row.trial_dir == str(trial_dir)


def test_summarize_experiment_results_groups_by_agent(tmp_path: Path):
    manifest_path = tmp_path / "experiment.json"
    manifest_path.write_text(
        json.dumps(
            {
                "experiment_id": "tb2-baseline",
                "jobs": [],
            }
        ),
        encoding="utf-8",
    )

    from openharness.experiments.results import ExperimentResultRow

    summary = summarize_experiment_results(
        [
            ExperimentResultRow(
                experiment_id="tb2-baseline",
                run_spec_id="smoke",
                agent_id="default",
                dataset="terminal-bench@2.0",
                task_name="task-a",
                trial_id="task-a__1",
                score=1.0,
                passed=True,
                error=None,
                model="gemini-2.5-flash",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cost_usd=0.01,
                duration_sec=30.0,
                agent_duration_sec=20.0,
                env_setup_duration_sec=5.0,
                verifier_duration_sec=5.0,
                trace_id=None,
                trace_url=None,
                trial_dir="/tmp/task-a",
            ),
            ExperimentResultRow(
                experiment_id="tb2-baseline",
                run_spec_id="smoke",
                agent_id="default",
                dataset="terminal-bench@2.0",
                task_name="task-b",
                trial_id="task-b__1",
                score=0.0,
                passed=False,
                error=None,
                model="gemini-2.5-flash",
                input_tokens=20,
                output_tokens=5,
                total_tokens=25,
                cost_usd=0.02,
                duration_sec=60.0,
                agent_duration_sec=40.0,
                env_setup_duration_sec=10.0,
                verifier_duration_sec=10.0,
                trace_id=None,
                trace_url=None,
                trial_dir="/tmp/task-b",
            ),
        ]
    )

    assert summary["by_agent"]["default"]["n_trials"] == 2
    assert summary["by_agent"]["default"]["n_passed"] == 1
    assert summary["by_agent"]["default"]["pass_rate"] == 0.5
    assert summary["by_agent"]["default"]["mean_score"] == 0.5
    assert summary["by_agent"]["default"]["total_tokens"] == 40
    assert summary["by_agent"]["default"]["total_cost_usd"] == 0.03
    assert summary["by_agent"]["default"]["median_duration_sec"] == 45.0
