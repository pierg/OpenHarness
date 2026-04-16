"""Declarative experiment specifications (YAML)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentOverrides(BaseModel):
    model: str | None = None
    max_turns: int | None = Field(default=None, ge=1)
    max_tokens: int | None = Field(default=None, ge=1)
    n_concurrent: int | None = Field(default=None, ge=1)
    n_attempts: int | None = Field(default=None, ge=1)
    env: dict[str, str] | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentLegSpec(BaseModel):
    id: str  # catalog agent id ("default", "react", ...)
    alias: str | None = None  # leg directory name; defaults to id
    overrides: AgentOverrides = Field(default_factory=AgentOverrides)

    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskFilter(BaseModel):
    include_tasks: tuple[str, ...] = ()
    exclude_tasks: tuple[str, ...] = ()
    n_tasks: int | None = Field(default=None, ge=1)

    model_config = ConfigDict(extra="forbid", frozen=True)


class EnvSpec(BaseModel):
    type: str = "docker"
    # additional fields could be added here for daytona etc.
    kwargs: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow", frozen=True)


class ExperimentSpec(BaseModel):
    id: str
    dataset: str
    agents: tuple[AgentLegSpec, ...]
    defaults: AgentOverrides = Field(default_factory=AgentOverrides)
    task_filter: TaskFilter = Field(default_factory=TaskFilter)
    environment: EnvSpec = Field(default_factory=EnvSpec)
    fail_fast: bool = False
    leg_concurrency: int = Field(default=1, ge=1)
    profiles: dict[str, Any] = Field(default_factory=dict)
    
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("agents", mode="before")
    @classmethod
    def _normalize_agents(cls, v: Any) -> Any:
        if not v:
            raise ValueError("At least one agent is required.")
        normalized = []
        for agent in v:
            if isinstance(agent, str):
                normalized.append(AgentLegSpec(id=agent))
            elif isinstance(agent, dict):
                normalized.append(AgentLegSpec.model_validate(agent))
            elif isinstance(agent, AgentLegSpec):
                normalized.append(agent)
            else:
                raise ValueError(f"Invalid agent type: {type(agent)}")
        return normalized

    @model_validator(mode="after")
    def _check_unique_aliases(self) -> ExperimentSpec:
        aliases = []
        for agent in self.agents:
            alias = agent.alias or agent.id
            if alias in aliases:
                raise ValueError(f"Duplicate agent alias found: {alias}")
            aliases.append(alias)
        return self


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge update dict into base dict."""
    result = base.copy()
    for k, v in update.items():
        if isinstance(v, dict) and k in result and isinstance(result[k], dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def load_experiment_spec(path: str | Path, profile: str | None = None) -> ExperimentSpec:
    """Load an experiment YAML file, optionally merging a profile."""
    path_obj = Path(path)
    raw = yaml.safe_load(path_obj.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected experiment YAML mapping, got {type(raw).__name__}")
    
    # Backwards compatibility translation
    if "id" not in raw:
        raw["id"] = path_obj.stem
        
    # Migrate old flat overrides to defaults
    flat_overrides = {}
    for key in ["model", "max_turns", "max_tokens", "n_concurrent", "n_attempts", "env"]:
        if key in raw:
            flat_overrides[key] = raw.pop(key)
    if flat_overrides and "defaults" not in raw:
        raw["defaults"] = flat_overrides

    # Migrate old task fields
    task_fields = {}
    for key in ["include_tasks", "exclude_tasks", "n_tasks"]:
        if key in raw:
            task_fields[key] = raw.pop(key)
    if task_fields and "task_filter" not in raw:
        raw["task_filter"] = task_fields

    if profile:
        profiles = raw.get("profiles", {})
        if profile not in profiles:
            raise ValueError(f"Profile '{profile}' not found in {path_obj}")
        # Merge the profile over the base raw dict
        raw = _deep_merge(raw, profiles[profile])

    return ExperimentSpec.model_validate(raw)
