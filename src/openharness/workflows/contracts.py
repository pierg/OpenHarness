"""Contracts and value objects for workflow execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from openharness.agents.contracts import TaskDefinition


@dataclass(frozen=True)
class WorkerHandle:
    """Handle for a spawned workflow role."""

    role: str
    agent_id: str
    task_id: str
    backend_type: str
    team_name: str
    mailbox_name: str
    worktree_path: str | None = None


@dataclass(frozen=True)
class WorkflowMessage:
    """Normalized mailbox message surfaced to workflow code."""

    id: str
    type: str
    sender: str
    recipient: str
    text: str
    timestamp: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoleRunResult:
    """Result from running one workflow role."""

    role: str
    final_text: str
    input_tokens: int = 0
    output_tokens: int = 0
    workspace_cwd: str | None = None
    worker: WorkerHandle | None = None


@dataclass(frozen=True)
class WorkflowRunResult:
    """Result from running a workflow topology."""

    workflow_name: str
    topology: str
    entry_role: str
    team_name: str
    final_text: str
    role_results: dict[str, RoleRunResult] = field(default_factory=dict)
    mailbox_messages: tuple[WorkflowMessage, ...] = ()
    workers: dict[str, WorkerHandle] = field(default_factory=dict)


class WorkflowTopology(Protocol):
    """Protocol implemented by all workflow topologies."""

    name: str

    async def run(
        self,
        spec: Any,
        task: TaskDefinition,
        runtime: Any,
    ) -> WorkflowRunResult:
        """Execute the workflow topology."""

