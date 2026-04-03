"""Agent-specific contracts and value types.

Workspace-level abstractions (``Workspace``, ``CommandResult``) live in
``openharness.workspace``.  This module re-exports them for backward
compatibility and defines the agent-only types that sit on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from openharness.observability import TraceObserver
from openharness.tools.base import ToolRegistry
from openharness.workspace import CommandResult, Workspace

# Re-exports so existing ``from openharness.agents.contracts import …`` still work.
AgentWorkspace = Workspace
__all__ = [
    "AgentLogPaths",
    "AgentRunContext",
    "AgentRunResult",
    "AgentWorkspace",
    "CommandResult",
    "ToolRegistryFactory",
    "Workspace",
]


class ToolRegistryFactory(Protocol):
    """Factory that creates a tool registry bound to a concrete workspace."""

    def build(self, workspace: Workspace) -> ToolRegistry:
        """Return a tool registry bound to *workspace*."""


@dataclass(frozen=True)
class AgentLogPaths:
    """Paths where a run should emit JSONL logs."""

    messages_path: str
    events_path: str


@dataclass(frozen=True)
class AgentRunContext:
    """Extra execution context supplied by the host integration layer."""

    trace_observer: TraceObserver | None = None


@dataclass(frozen=True)
class AgentRunResult:
    """Normalised result for a completed agent run."""

    final_text: str
    input_tokens: int
    output_tokens: int
