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
  - basic
  - planner_executor
""",
        encoding="utf-8",
    )

    config = load_experiment_spec(spec_path)

    assert config.id == "tb2-baseline"
    assert config.dataset == "terminal-bench@2.0"
    assert [a.id for a in config.agents] == ["basic", "planner_executor"]
    assert config.task_filter.include_tasks == ("build-*", "git-*")
    assert config.task_filter.n_tasks == 5
    assert config.defaults.model == "gemini-2.5-flash"


def test_agent_alias_objects_are_parsed():
    config = ExperimentSpec.model_validate(
        {
            "id": "tb2-baseline",
            "dataset": "terminal-bench@2.0",
            "agents": [{"id": "basic", "alias": "baseline"}],
        }
    )
    assert config.agents[0].id == "basic"
    assert config.agents[0].alias == "baseline"


def test_duplicate_aliases_rejected():
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(
            {
                "id": "tb2-baseline",
                "dataset": "terminal-bench@2.0",
                "agents": [
                    {"id": "basic", "alias": "baseline"},
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
                    "basic",
                    "basic",
                ],
            }
        )


def test_profile_overrides_deep_merge(tmp_path):
    from openharness.experiments.spec import load_experiment_spec_full

    spec_path = tmp_path / "tb2.yaml"
    spec_path.write_text(
        """
id: tb2-baseline
dataset: terminal-bench@2.0
model: gemini-2.5-flash
n_tasks: 50
agents:
  - basic
  - planner_executor
profiles:
  demo:
    task_filter:
      include_tasks: ["build-*"]
      n_tasks: 2
    defaults:
      model: gemini-2.0-flash
""",
        encoding="utf-8",
    )

    loaded = load_experiment_spec_full(spec_path, profile="demo")
    assert loaded.spec.defaults.model == "gemini-2.0-flash"
    assert loaded.spec.task_filter.include_tasks == ("build-*",)
    assert loaded.spec.task_filter.n_tasks == 2
    assert loaded.source_text.startswith("\nid: tb2-baseline")
    assert loaded.profile == "demo"


def test_unknown_profile_rejected(tmp_path):
    from openharness.experiments.spec import load_experiment_spec_full

    spec_path = tmp_path / "tb2.yaml"
    spec_path.write_text(
        """
id: tb2-baseline
dataset: terminal-bench@2.0
agents:
  - basic
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Profile 'missing' not found"):
        load_experiment_spec_full(spec_path, profile="missing")
