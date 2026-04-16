from __future__ import annotations

from pathlib import Path

from openharness.experiments.runner import (
    build_harbor_run_spec,
    resolve_agent_config_for_experiment,
)
from openharness.experiments.specs import ExperimentJob, ExperimentConfig


def test_resolve_agent_config_applies_runtime_overrides_recursively():
    config_obj = ExperimentConfig(
        dataset="terminal-bench@2.0",
        agents=("planner_executor",),
        model="gemini-3.1-flash-lite-preview",
        max_turns=30,
        max_tokens=8192,
    )

    config = resolve_agent_config_for_experiment("planner_executor", config_obj)

    assert config.name == "planner_executor"
    assert config.subagents["planner"].model == "gemini-3.1-flash-lite-preview"
    assert config.subagents["planner"].max_turns == 30
    assert config.subagents["planner"].max_tokens == 8192
    assert config.subagents["executor"].model == "gemini-3.1-flash-lite-preview"
    assert config.subagents["executor"].max_turns == 30
    assert config.subagents["executor"].max_tokens == 8192


def test_build_harbor_run_spec_uses_resolved_agent_yaml(tmp_path: Path):
    config = ExperimentConfig(
        dataset="terminal-bench@2.0",
        agents=("default",),
        model="gemini-3.1-flash-lite-preview",
        max_turns=30,
        max_tokens=8192,
        n_concurrent=4,
        n_attempts=1,
        include_tasks=("build-*", "git-*"),
        n_tasks=5,
    )
    job = ExperimentJob(
        experiment_id="tb2-baseline",
        experiment_instance_id="smoke",
        agent_id="default",
        dataset="terminal-bench@2.0",
        config=config,
    )

    spec = build_harbor_run_spec(job, cwd=tmp_path, jobs_dir=tmp_path / "jobs")

    assert spec.run_id == "smoke-default"
    assert spec.job.agent.agent_name == "default"
    assert spec.job.agent.model == "gemini-3.1-flash-lite-preview"
    assert spec.job.agent.max_turns == 30
    assert spec.job.agent.max_tokens == 8192
    assert spec.job.agent.agent_config_yaml is not None
    assert "name: default" in spec.job.agent.agent_config_yaml
    assert "model: gemini-3.1-flash-lite-preview" in spec.job.agent.agent_config_yaml
    assert spec.job.task.dataset == "terminal-bench@2.0"
    assert spec.job.task.include_task_names == ("build-*", "git-*")
    assert spec.job.task.n_tasks == 5
    assert spec.job.n_concurrent_trials == 4
    assert spec.job.n_attempts == 1
    assert spec.job.jobs_dir == tmp_path / "jobs"
    assert spec.job.metadata["experiment_id"] == "tb2-baseline"
    assert spec.job.metadata["experiment_instance_id"] == "smoke"
    assert spec.job.metadata["agent_id"] == "default"
