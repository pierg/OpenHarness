"""Agent configuration models with YAML loading and Jinja prompt rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from jinja2 import BaseLoader, Environment, StrictUndefined
from pydantic import BaseModel, Field

from openharness.tools import DEFAULT_TOOL_NAMES

_JINJA_ENV = Environment(loader=BaseLoader(), undefined=StrictUndefined)


class QuickEvaluation(BaseModel):
    """A lightweight post-run assertion for fast regression checks."""

    name: str
    contains: str | None = None
    not_contains: str | None = None
    message: str | None = None


class AgentDefinitionMetadata(BaseModel):
    """Coordinator/swarm metadata projected from a YAML agent config."""

    subagent_type: str | None = None
    description: str | None = None
    runner: Literal["prompt_native", "yaml_workflow", "harbor"] = "yaml_workflow"
    system_prompt: str | None = None
    system_prompt_mode: Literal["default", "replace", "append"] | None = None
    color: str | None = None
    permission_mode: str | None = None
    permissions: tuple[str, ...] = ()
    plan_mode_required: bool = False
    allow_permission_prompts: bool = False
    tools: tuple[str, ...] | None = None
    disallowed_tools: tuple[str, ...] | None = None
    skills: tuple[str, ...] = ()
    required_mcp_servers: tuple[str, ...] = ()
    background: bool = False
    initial_prompt: str | None = None
    isolation: Literal["worktree", "remote"] | None = None


class AgentConfig(BaseModel):
    """Declarative agent configuration — loadable from YAML or constructed in Python.

    Can recursively contain definitions for subagents.
    """

    name: str = "default"
    architecture: str = "simple"
    description: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_turns: int = 8
    max_tokens: int = 4096
    tools: tuple[str, ...] = DEFAULT_TOOL_NAMES

    definition: AgentDefinitionMetadata | None = None
    evaluations: tuple[QuickEvaluation, ...] = ()
    prompts: dict[str, str] = Field(default_factory=dict)

    subagents: dict[str, "AgentConfig"] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> AgentConfig:
        """Load an agent configuration from a YAML file."""
        path_obj = Path(path)
        raw = yaml.safe_load(path_obj.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected a YAML mapping, got {type(raw).__name__}")
        
        if "name" not in raw:
            raw["name"] = path_obj.stem

        return cls.model_validate(raw)

    def render_prompt(self, prompt_name: str, **kwargs: Any) -> str:
        """Render a specific named prompt with the given kwargs."""
        template = self.prompts.get(prompt_name)
        if template is None:
            raise KeyError(f"Prompt '{prompt_name}' not found in agent config '{self.name}'")
        return _JINJA_ENV.from_string(template).render(**kwargs)


AgentConfig.model_rebuild()
