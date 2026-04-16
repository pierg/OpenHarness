"""Experiment planning logic."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from openharness.agents.catalog import get_catalog_agent_config
from openharness.agents.config import AgentConfig
from openharness.experiments.spec import ExperimentSpec, AgentOverrides


class Leg(BaseModel):
    leg_id: str  # = alias
    agent_id: str  # catalog id
    agent_config: AgentConfig  # fully resolved (defaults + overrides + catalog)
    n_concurrent: int
    n_attempts: int
    harbor_run_id: str  # = _safe_id(instance_id + "-" + leg_id)
    overrides_env: dict[str, str] = {}

    model_config = ConfigDict(extra="forbid", frozen=True)


class ExperimentPlan(BaseModel):
    instance_id: str
    spec: ExperimentSpec
    legs: tuple[Leg, ...]

    model_config = ConfigDict(extra="forbid", frozen=True)


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "-" for ch in value).strip("-")


def _apply_agent_runtime_overrides(
    agent_config: AgentConfig,
    defaults: AgentOverrides,
    overrides: AgentOverrides,
) -> AgentConfig:
    """Apply overrides to an AgentConfig and all its subagents."""
    update: dict[str, Any] = {}

    # Resolve effective values: overrides win over defaults
    model = overrides.model or defaults.model
    if model is not None:
        update["model"] = model

    max_turns = overrides.max_turns or defaults.max_turns
    if max_turns is not None:
        update["max_turns"] = max_turns

    max_tokens = overrides.max_tokens or defaults.max_tokens
    if max_tokens is not None:
        update["max_tokens"] = max_tokens

    if agent_config.subagents:
        update["subagents"] = {
            name: _apply_agent_runtime_overrides(subagent, defaults, overrides)
            for name, subagent in agent_config.subagents.items()
        }
    return agent_config.model_copy(update=update)


def plan_experiment(
    spec: ExperimentSpec,
    *,
    instance_id: str,
    cwd: str | Path | None = None,
) -> ExperimentPlan:
    """Expand a declarative experiment into concrete execution legs.

    *cwd* is the project root used to resolve user/project-level agent
    catalogs; the experiment output directory is intentionally NOT a good
    default because we do not want to create ``.openharness/`` inside it.
    """
    legs: list[Leg] = []
    catalog_cwd: str | Path | None = cwd if cwd is not None else Path.cwd()

    for agent_spec in spec.agents:
        leg_id = agent_spec.alias or agent_spec.id

        catalog_item = get_catalog_agent_config(agent_spec.id, catalog_cwd)
        if catalog_item is None:
            raise KeyError(f"Unknown agent config id: {agent_spec.id}")

        # Apply overrides
        resolved_agent_config = _apply_agent_runtime_overrides(
            catalog_item.config,
            spec.defaults,
            agent_spec.overrides,
        )

        overrides_env: dict[str, str] = {}
        if spec.defaults.env:
            overrides_env.update(spec.defaults.env)
        if agent_spec.overrides.env:
            overrides_env.update(agent_spec.overrides.env)

        legs.append(
            Leg(
                leg_id=leg_id,
                agent_id=agent_spec.id,
                agent_config=resolved_agent_config,
                n_concurrent=agent_spec.overrides.n_concurrent or spec.defaults.n_concurrent or 1,
                n_attempts=agent_spec.overrides.n_attempts or spec.defaults.n_attempts or 1,
                harbor_run_id=_safe_id(f"{instance_id}-{leg_id}"),
                overrides_env=overrides_env,
            )
        )

    return ExperimentPlan(
        instance_id=instance_id,
        spec=spec,
        legs=tuple(legs),
    )
