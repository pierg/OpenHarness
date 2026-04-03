"""Explicit agent and workspace contracts.

Defines the structural protocols and value types that decouple agent logic from
any specific execution substrate (local filesystem, Docker, Harbor, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from openharness.observability import TraceObserver
from openharness.tools.base import ToolRegistry


@dataclass(frozen=True)
class CommandResult:
    """Normalised result for a shell command executed in a workspace."""

    stdout: str = ""
    stderr: str = ""
    return_code: int = 0


@runtime_checkable
class AgentWorkspace(Protocol):
    """Protocol implemented by any execution substrate used by an agent.

    Implementors include ``LocalWorkspaceAdapter`` (local filesystem) and
    ``HarborWorkspaceAdapter`` (Harbor remote environment).  All paths are
    POSIX strings; callers must not assume the substrate runs locally.
    """

    @property
    def cwd(self) -> str:
        """Return the workspace root used for relative tool paths."""

    async def run_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        """Execute a shell command inside the workspace."""

    async def read_file(self, path: str) -> bytes:
        """Read raw bytes from a file path."""

    async def write_file(
        self,
        path: str,
        content: bytes,
        *,
        create_directories: bool = True,
    ) -> None:
        """Write raw bytes to a file path."""

    async def file_exists(self, path: str) -> bool:
        """Return whether the path is an existing file."""

    async def dir_exists(self, path: str) -> bool:
        """Return whether the path is an existing directory."""


class ToolRegistryFactory(Protocol):
    """Factory that creates a tool registry bound to a concrete workspace."""

    def build(self, workspace: AgentWorkspace) -> ToolRegistry:
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
