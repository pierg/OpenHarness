"""Declarative experiment specifications for benchmark runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


_RUNTIME_OVERRIDE_FIELDS = (
    "model",
    "max_turns",
    "max_tokens",
    "n_concurrent",
    "n_attempts",
    "include_tasks",
    "exclude_tasks",
    "n_tasks",
)


class ExperimentRuntimeOverrides(BaseModel):
    """Runtime options that may override agent config defaults for a benchmark."""

    model: str | None = None
    max_turns: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    n_concurrent: int | None = Field(default=None, ge=1)
    n_attempts: int | None = Field(default=None, ge=1)
    include_tasks: tuple[str, ...] | None = None
    exclude_tasks: tuple[str, ...] | None = None
    n_tasks: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True)

    def merge(self, override: "ExperimentRuntimeOverrides | None") -> "ExperimentRuntimeOverrides":
        """Return a new settings object where non-null values in ``override`` win."""
        if override is None:
            return self
        values = self.model_dump()
        for field in _RUNTIME_OVERRIDE_FIELDS:
            value = getattr(override, field)
            if value is not None:
                values[field] = value
        return ExperimentRuntimeOverrides.model_validate(values)


class ExperimentRunSpec(ExperimentRuntimeOverrides):
    """One logical run slice inside an experiment."""

    id: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentSpec(BaseModel):
    """Top-level experiment YAML model."""

    id: str
    dataset: str
    defaults: ExperimentRuntimeOverrides = Field(default_factory=ExperimentRuntimeOverrides)
    agents: tuple[str, ...]
    runs: tuple[ExperimentRunSpec, ...]

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("agents")
    @classmethod
    def _agents_must_not_be_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("At least one agent id is required.")
        return value

    @field_validator("runs")
    @classmethod
    def _runs_must_not_be_empty(cls, value: tuple[ExperimentRunSpec, ...]):
        if not value:
            raise ValueError("At least one run is required.")
        seen: set[str] = set()
        for run in value:
            if run.id in seen:
                raise ValueError(f"Duplicate run id: {run.id}")
            seen.add(run.id)
        return value


class ExperimentJob(BaseModel):
    """Concrete job produced by expanding one run id across one agent id."""

    experiment_id: str
    run_id: str
    agent_id: str
    dataset: str
    settings: ExperimentRuntimeOverrides

    model_config = ConfigDict(extra="forbid", frozen=True)

    @property
    def job_id(self) -> str:
        return f"{self.experiment_id}.{self.run_id}.{self.agent_id}"

    @property
    def harbor_run_id(self) -> str:
        return _safe_id(self.job_id)


def load_experiment_spec(path: str | Path) -> ExperimentSpec:
    """Load an experiment YAML file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected experiment YAML mapping, got {type(raw).__name__}")
    return ExperimentSpec.model_validate(raw)


def expand_experiment_jobs(
    spec: ExperimentSpec,
    *,
    cli_overrides: ExperimentRuntimeOverrides | None = None,
) -> list[ExperimentJob]:
    """Expand a declarative experiment into concrete run/agent jobs."""
    jobs: list[ExperimentJob] = []
    for run in spec.runs:
        run_settings = spec.defaults.merge(run).merge(cli_overrides)
        for agent_id in spec.agents:
            jobs.append(
                ExperimentJob(
                    experiment_id=spec.id,
                    run_id=run.id,
                    agent_id=agent_id,
                    dataset=spec.dataset,
                    settings=run_settings,
                )
            )
    return jobs


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value).strip("-")


def runtime_overrides_from_mapping(raw: dict[str, Any]) -> ExperimentRuntimeOverrides:
    """Build runtime overrides from CLI or programmatic dictionaries."""
    return ExperimentRuntimeOverrides.model_validate({k: v for k, v in raw.items() if v is not None})
