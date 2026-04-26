"""Agent configuration models with YAML loading and Jinja prompt rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Self

import yaml
from jinja2 import BaseLoader, Environment, StrictUndefined
from pydantic import BaseModel, Field, model_validator

from openharness.tools import DEFAULT_TOOL_NAMES

_JINJA_ENV = Environment(loader=BaseLoader(), undefined=StrictUndefined)


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

    name: str = "basic"
    architecture: str = "simple"
    description: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_turns: int = 8
    max_tokens: int = 4096
    tools: tuple[str, ...] = DEFAULT_TOOL_NAMES

    # Free-form tags naming the components active on this agent (e.g.
    # "loop-guard", "web-tools"). When non-empty, AgentConfig.from_yaml
    # resolves each id against components/<id>.yaml, runs conflict
    # checks, and merges each component's `wires:` payload into this
    # config. The list itself also travels into leg/agent.resolved.yaml
    # so trial artifacts can be grouped after the fact. The source of
    # truth for what each id means lives in components/<id>.yaml; the
    # human-readable index is lab/components.md.
    components: tuple[str, ...] = ()

    # Free-form extras populated by the components loader (and any
    # caller that needs to thread runtime configuration into an
    # architecture without enlarging this schema). Architectures
    # consult `extras.get("<component_id>", {})` at construction time.
    extras: dict[str, Any] = Field(default_factory=dict)

    # Optional explicit allow-list of `openharness_system_context`
    # sections to include when rendering this agent's system prompt.
    # ``None`` (default) lets the runtime decide based on session_mode
    # and the agent's registered tools. Use this for surgical control,
    # e.g. a planner subagent that wants only ``"base"``.
    # See ``openharness.prompts.context.SYSTEM_CONTEXT_SECTIONS`` for
    # the recognized names.
    system_context_sections: tuple[str, ...] | None = None

    definition: AgentDefinitionMetadata | None = None
    prompts: dict[str, str] = Field(default_factory=dict)

    subagents: dict[str, "AgentConfig"] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_benchmark_oracle_extras(self) -> Self:
        """Reject runtime policy that depends on benchmark task identity."""
        _validate_no_benchmark_oracle_extras(self.extras, config_name=self.name)
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> AgentConfig:
        """Load an agent configuration from a YAML file."""
        path_obj = Path(path)
        raw = yaml.safe_load(path_obj.read_text(encoding="utf-8"))
        return cls.from_mapping(raw, source_name=path_obj.stem)

    @classmethod
    def from_yaml_text(cls, text: str, *, source_name: str = "inline") -> AgentConfig:
        """Load an agent configuration from a YAML string."""
        raw = yaml.safe_load(text)
        return cls.from_mapping(raw, source_name=source_name)

    @classmethod
    def from_mapping(cls, raw: Any, *, source_name: str) -> AgentConfig:
        """Validate a raw YAML mapping as an agent configuration.

        If ``raw`` lists ``components: [...]``, the entries are
        resolved against the ``components/`` registry *before*
        pydantic validation so any merged tools, prompt fragments,
        or extras pass the same schema as a hand-written YAML.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"Expected a YAML mapping, got {type(raw).__name__}")

        if "name" not in raw:
            raw["name"] = source_name

        if raw.get("components"):
            from openharness.agents.components import apply_components

            apply_components(raw, source_name=source_name)

        return cls.model_validate(raw)

    def render_prompt(self, prompt_name: str, **kwargs: Any) -> str:
        """Render a specific named prompt with the given kwargs."""
        template = self.prompts.get(prompt_name)
        if template is None:
            raise KeyError(f"Prompt '{prompt_name}' not found in agent config '{self.name}'")
        return _JINJA_ENV.from_string(template).render(**kwargs)


def _validate_no_benchmark_oracle_extras(
    extras: dict[str, Any], *, config_name: str
) -> None:
    router = extras.get("model_router")
    if router is None:
        return
    if not isinstance(router, dict):
        return

    task_models = router.get("task_models")
    if task_models:
        raise ValueError(
            f"Agent config '{config_name}' uses extras.model_router.task_models, "
            "which routes by exact benchmark task identity. Runtime agent policy "
            "must only use information available on unseen tasks: the instruction, "
            "workspace, tools, and observations. Use an instruction/workspace-derived "
            "classifier or mark the experiment diagnostic-only instead."
        )


AgentConfig.model_rebuild()
