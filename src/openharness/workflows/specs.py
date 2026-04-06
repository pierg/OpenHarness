"""Declarative workflow configuration models."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


BackendMode = Literal["auto", "in_process", "subprocess"]
RoleMode = Literal["inline", "spawned"]
IsolationMode = Literal["shared", "worktree"]


class RoleSpec(BaseModel):
    """One workflow role bound to an agent spec."""

    agent: str
    description: str = ""
    mode: RoleMode = "inline"
    backend: BackendMode = "auto"
    isolation: IsolationMode = "shared"
    spawn_as: str | None = None
    bootstrap_task: str | None = None
    prompt_prefix: str | None = None
    initial_payload: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: tuple[str, ...] | None = None
    disallowed_tools: tuple[str, ...] | None = None
    model: str | None = None
    max_turns: int | None = None


class RoutingPolicy(BaseModel):
    """Allowed coordination edges for one role."""

    may_spawn: tuple[str, ...] = ()
    may_message: tuple[str, ...] = ()


class MessagingSpec(BaseModel):
    """Workflow messaging transport settings."""

    transport: Literal["mailbox"] = "mailbox"


class PermissionsSpec(BaseModel):
    """Workflow permission-handling settings."""

    mode: Literal["inherit", "leader_brokered"] = "inherit"


class LifecycleSpec(BaseModel):
    """Workflow worker lifecycle settings."""

    persistent_workers: bool = True
    cleanup_worktrees: bool = True


class ObservabilitySpec(BaseModel):
    """Workflow tracing settings."""

    emit_role_spans: bool = True


class CoordinationSpec(BaseModel):
    """Coordination services configured for the workflow."""

    messaging: MessagingSpec = Field(default_factory=MessagingSpec)
    permissions: PermissionsSpec = Field(default_factory=PermissionsSpec)
    lifecycle: LifecycleSpec = Field(default_factory=LifecycleSpec)
    observability: ObservabilitySpec = Field(default_factory=ObservabilitySpec)


class WorkflowSpec(BaseModel):
    """Declarative workflow configuration."""

    kind: Literal["workflow"] = "workflow"
    name: str
    topology: str = "single"
    description: str = ""
    entry_role: str | None = None
    roles: dict[str, RoleSpec]
    routing: dict[str, RoutingPolicy] = Field(default_factory=dict)
    coordination: CoordinationSpec = Field(default_factory=CoordinationSpec)
    topology_config: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> WorkflowSpec:
        """Load a workflow configuration from YAML."""
        path_obj = Path(path)
        raw = yaml.safe_load(path_obj.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"Expected a YAML mapping, got {type(raw).__name__}")

        if "name" not in raw:
            raw["name"] = path_obj.stem

        return cls.model_validate(raw)

    @model_validator(mode="after")
    def _validate_references(self) -> WorkflowSpec:
        if not self.roles:
            raise ValueError("Workflow must define at least one role")

        if self.entry_role is not None and self.entry_role not in self.roles:
            raise ValueError(f"Unknown entry_role {self.entry_role!r}")

        for role_name, policy in self.routing.items():
            if role_name not in self.roles:
                raise ValueError(f"Routing policy references unknown role {role_name!r}")
            for target in (*policy.may_spawn, *policy.may_message):
                if target not in self.roles:
                    raise ValueError(
                        f"Routing policy for {role_name!r} references unknown role {target!r}"
                    )
        return self

