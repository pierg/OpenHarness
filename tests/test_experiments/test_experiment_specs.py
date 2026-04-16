from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.experiments.specs import (
    ExperimentConfig,
    expand_experiment_jobs,
    load_experiment_config,
)


def test_load_experiment_config(tmp_path):
    spec_path = tmp_path / "tb2.yaml"
    spec_path.write_text(
        """
id: tb2-baseline
dataset: terminal-bench@2.0
model: gemini-2.5-flash
max_turns: 30
max_tokens: 8192
n_concurrent: 4
n_attempts: 1
include_tasks:
  - build-*
  - git-*
n_tasks: 5
agents:
  - default
  - planner_executor
""",
        encoding="utf-8",
    )

    config = load_experiment_config(spec_path)

    assert config.id == "tb2-baseline"
    assert config.dataset == "terminal-bench@2.0"
    assert config.agents == ("default", "planner_executor")
    assert config.include_tasks == ("build-*", "git-*")
    assert config.n_tasks == 5


def test_agent_alias_objects_are_rejected():
    with pytest.raises(ValidationError):
        ExperimentConfig.model_validate(
            {
                "id": "tb2-baseline",
                "dataset": "terminal-bench@2.0",
                "agents": [{"name": "baseline", "config": "default"}],
            }
        )


def test_expand_experiment_jobs_applies_defaults_cli_precedence_and_instance_id():
    config = ExperimentConfig.model_validate(
        {
            "id": "tb2-baseline",
            "dataset": "terminal-bench@2.0",
            "model": "gemini-2.5-flash",
            "max_turns": 30,
            "n_concurrent": 4,
            "include_tasks": ["build-*"],
            "n_tasks": 10,
            "agents": ["default", "reflection"],
        }
    )

    jobs = expand_experiment_jobs(
        config,
        experiment_instance_id="tb2-baseline-agent-fixes",
    )

    assert [job.job_id for job in jobs] == [
        "tb2-baseline-agent-fixes.default",
        "tb2-baseline-agent-fixes.reflection",
    ]
    assert jobs[0].experiment_id == "tb2-baseline"
    assert jobs[0].experiment_instance_id == "tb2-baseline-agent-fixes"
    assert jobs[0].config.model == "gemini-2.5-flash"
    assert jobs[0].config.max_turns == 30
    assert jobs[0].config.n_tasks == 10
    assert jobs[0].config.include_tasks == ("build-*",)
    assert jobs[0].config.n_concurrent == 4
    assert jobs[1].agent_id == "reflection"
