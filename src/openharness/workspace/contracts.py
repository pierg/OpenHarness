"""Workspace protocol and shared value types.

``Workspace`` is the universal interface for file I/O and shell execution.
All paths are POSIX strings; callers must not assume the substrate runs locally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CommandResult:
    """Normalised result of a shell command executed in a workspace."""

    stdout: str = ""
    stderr: str = ""
    return_code: int = 0


@runtime_checkable
class Workspace(Protocol):
    """Substrate-agnostic execution environment.

    Implemented by ``LocalWorkspace`` (this machine), and intended to be
    implemented by ``DockerWorkspace``, ``HarborWorkspace``, etc.
    """

    @property
    def cwd(self) -> str:
        """Return the workspace root used for relative path resolution."""

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
