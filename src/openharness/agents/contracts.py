"""Agent-specific contracts and value types.

Workspace-level abstractions (``Workspace``, ``CommandResult``) live in
``openharness.workspace``.  This module re-exports them for backward
compatibility and defines the agent-only types that sit on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field

from openharness.workspace import CommandResult, Workspace

if TYPE_CHECKING:
    from openharness.runtime.session import AgentRuntime

AgentWorkspace = Workspace

T = TypeVar("T")

__all__ = [
    "Agent",
    "AgentRunResult",
    "AgentWorkspace",
    "CommandResult",
    "TaskDefinition",
    "Workspace",
]


@dataclass(frozen=True)
class AgentRunResult(Generic[T]):
    """Normalised result for a completed agent run.

    ``output`` holds the typed agent output — ``str`` for free-text agents,
    a Pydantic model for agents that produce structured output.
    """

    output: T
    input_tokens: int
    output_tokens: int

    @property
    def final_text(self) -> str:
        """Backward-compatible text access."""
        if isinstance(self.output, str):
            return self.output
        if isinstance(self.output, BaseModel):
            return self.output.model_dump_json()
        return str(self.output)


class TaskDefinition(BaseModel):
    """A formalised task definition to be executed by an agent.

    Provides a standard payload structure for Jinja template rendering:
    - ``instruction``: The primary natural language task.
    - ``payload``: Extra ad-hoc template variables for flexible agent definitions.
    """

    instruction: str
    payload: dict[str, Any] = Field(default_factory=dict)


class Agent(Protocol):
    """Protocol that every agent architecture must satisfy.

    The protocol is intentionally minimal — just ``run``.  This allows
    any agent to be used as a building block inside a composite agent.
    """

    async def run(
        self,
        task: TaskDefinition,
        runtime: "AgentRuntime",
    ) -> AgentRunResult:
        """Execute the agent on *task* using the provided *runtime*."""
        ...
