from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.experiments.specs import (
    ExperimentRuntimeOverrides,
    ExperimentSpec,
    expand_experiment_jobs,
    load_experiment_spec,
)


def test_load_experiment_spec_uses_agent_config_ids(tmp_path):
    spec_path = tmp_path / "tb2.yaml"
    spec_path.write_text(
        """
id: tb2-baseline
dataset: terminal-bench@2.0
defaults:
  model: gemini-2.5-flash
  max_turns: 30
  max_tokens: 8192
  n_concurrent: 4
  n_attempts: 1
agents:
  - default
  - planner_executor
runs:
  - id: smoke
    include_tasks:
      - build-*
      - git-*
    n_tasks: 5
""",
        encoding="utf-8",
    )

    spec = load_experiment_spec(spec_path)

    assert spec.id == "tb2-baseline"
    assert spec.dataset == "terminal-bench@2.0"
    assert spec.agents == ("default", "planner_executor")
    assert spec.runs[0].id == "smoke"
    assert spec.runs[0].include_tasks == ("build-*", "git-*")


def test_agent_alias_objects_are_rejected():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(
            {
                "id": "tb2-baseline",
                "dataset": "terminal-bench@2.0",
                "agents": [{"name": "baseline", "config": "default"}],
                "runs": [{"id": "smoke", "n_tasks": 1}],
            }
        )


def test_structural_agent_fields_are_not_runtime_overrides():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(
            {
                "id": "tb2-baseline",
                "dataset": "terminal-bench@2.0",
                "defaults": {
                    "model": "gemini-2.5-flash",
                    "tools": ["bash"],
                },
                "agents": ["default"],
                "runs": [{"id": "smoke", "n_tasks": 1}],
            }
        )


def test_expand_experiment_jobs_applies_default_run_and_cli_precedence():
    spec = ExperimentSpec.model_validate(
        {
            "id": "tb2-baseline",
            "dataset": "terminal-bench@2.0",
            "defaults": {
                "model": "gemini-2.5-flash",
                "max_turns": 30,
                "n_concurrent": 4,
                "include_tasks": ["build-*"],
                "n_tasks": 10,
            },
            "agents": ["default", "reflection"],
            "runs": [
                {
                    "id": "smoke",
                    "model": "gemini-2.5-pro",
                    "include_tasks": ["git-*"],
                    "n_tasks": 2,
                }
            ],
        }
    )

    jobs = expand_experiment_jobs(
        spec,
        cli_overrides=ExperimentRuntimeOverrides(n_concurrent=8),
    )

    assert [job.job_id for job in jobs] == [
        "tb2-baseline.smoke.default",
        "tb2-baseline.smoke.reflection",
    ]
    assert jobs[0].settings.model == "gemini-2.5-pro"
    assert jobs[0].settings.max_turns == 30
    assert jobs[0].settings.n_tasks == 2
    assert jobs[0].settings.include_tasks == ("git-*",)
    assert jobs[0].settings.n_concurrent == 8
    assert jobs[1].agent_id == "reflection"
