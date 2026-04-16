"""Declarative experiment specifications for benchmark runs."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExperimentConfig(BaseModel):
    """Top-level experiment YAML model."""

    dataset: str
    agents: tuple[str, ...]
    id: str | None = None

    # Common parameters for all agents in this experiment
    model: str | None = None
    max_turns: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    n_concurrent: int | None = Field(default=None, ge=1)
    n_attempts: int | None = Field(default=None, ge=1)
    include_tasks: tuple[str, ...] | None = None
    exclude_tasks: tuple[str, ...] | None = None
    n_tasks: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("agents")
    @classmethod
    def _agents_must_not_be_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("At least one agent id is required.")
        return value


class ExperimentJob(BaseModel):
    """Concrete job produced by expanding one experiment instance across one agent id."""

    experiment_id: str
    experiment_instance_id: str
    agent_id: str
    dataset: str
    config: ExperimentConfig

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def job_id(self) -> str:
        return f"{self.experiment_instance_id}.{self.agent_id}"

    @property
    def harbor_run_id(self) -> str:
        return _safe_id(self.job_id)


def load_experiment_config(path: str | Path) -> ExperimentConfig:
    """Load an experiment YAML file."""
    path_obj = Path(path)
    raw = yaml.safe_load(path_obj.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected experiment YAML mapping, got {type(raw).__name__}")
    if "id" not in raw:
        raw["id"] = path_obj.stem
    return ExperimentConfig.model_validate(raw)


def expand_experiment_jobs(
    config: ExperimentConfig,
    *,
    experiment_instance_id: str | None = None,
) -> list[ExperimentJob]:
    """Expand a declarative experiment into concrete run/agent jobs."""
    resolved_instance_id = experiment_instance_id or config.id or "experiment"
    jobs: list[ExperimentJob] = []
    for agent_id in config.agents:
        jobs.append(
            ExperimentJob(
                experiment_id=config.id or "experiment",
                experiment_instance_id=resolved_instance_id,
                agent_id=agent_id,
                dataset=config.dataset,
                config=config,
            )
        )
    return jobs


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value).strip("-")
