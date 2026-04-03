"""Agent runtime and workflow orchestration.

Provides the high-level ``Workflow`` runner as well as the low-level
``AgentRuntime`` plumbing used by individual agents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.agents.contracts import TaskDefinition
from openharness.runtime.session import AgentLogPaths, AgentRuntime

if TYPE_CHECKING:
    from openharness.runtime.workflow import Workflow

__all__ = ["AgentLogPaths", "AgentRuntime", "TaskDefinition", "Workflow"]


def __getattr__(name: str):
    if name == "Workflow":
        from openharness.runtime.workflow import Workflow

        return Workflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
