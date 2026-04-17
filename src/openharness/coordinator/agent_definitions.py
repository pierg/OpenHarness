"""Built-in agent definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    """Minimal local agent definition."""

    name: str
    description: str


def get_builtin_agent_definitions() -> list[AgentDefinition]:
    """Return built-in agent roles."""
    return [
        AgentDefinition(name="default", description="General-purpose local coding agent"),
        AgentDefinition(name="worker", description="Execution-focused worker agent"),
        AgentDefinition(name="explorer", description="Read-heavy exploration agent"),
    ]
