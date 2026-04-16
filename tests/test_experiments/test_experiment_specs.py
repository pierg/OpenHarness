from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.experiments.spec import (
    ExperimentSpec,
    load_experiment_spec,
)


def test_load_experiment_spec(tmp_path):
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

    config = load_experiment_spec(spec_path)

    assert config.id == "tb2-baseline"
    assert config.dataset == "terminal-bench@2.0"
    assert [a.id for a in config.agents] == ["default", "planner_executor"]
    assert config.task_filter.include_tasks == ("build-*", "git-*")
    assert config.task_filter.n_tasks == 5
    assert config.defaults.model == "gemini-2.5-flash"


def test_agent_alias_objects_are_parsed():
    config = ExperimentSpec.model_validate(
        {
            "id": "tb2-baseline",
            "dataset": "terminal-bench@2.0",
            "agents": [{"id": "default", "alias": "baseline"}],
        }
    )
    assert config.agents[0].id == "default"
    assert config.agents[0].alias == "baseline"


def test_duplicate_aliases_rejected():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(
            {
                "id": "tb2-baseline",
                "dataset": "terminal-bench@2.0",
                "agents": [
                    {"id": "default", "alias": "baseline"},
                    {"id": "planner_executor", "alias": "baseline"},
                ],
            }
        )


def test_duplicate_ids_without_alias_rejected():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(
            {
                "id": "tb2-baseline",
                "dataset": "terminal-bench@2.0",
                "agents": [
                    "default",
                    "default",
                ],
            }
        )
