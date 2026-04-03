"""Harbor-backed workspace implementation.

Adapts a Harbor ``BaseEnvironment`` to the ``Workspace`` protocol so that all
workspace-aware tools work transparently against remote Harbor environments.

The ``HarborEnvironment`` protocol is defined locally to avoid a circular
dependency between ``workspace`` and ``harbor`` packages.  Any object that
satisfies the protocol — including Harbor's real ``BaseEnvironment`` and
test fakes — works out of the box.
"""

from __future__ import annotations

import posixpath
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from openharness.workspace.contracts import CommandResult, Workspace


@dataclass
class _ExecResult:
    stdout: str | None = None
    stderr: str | None = None
    return_code: int = 0


@runtime_checkable
class HarborEnvironment(Protocol):
    """Minimal interface that a Harbor environment must satisfy."""

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> _ExecResult: ...

    async def upload_file(self, source_path: Path | str, target_path: str) -> None: ...

    async def download_file(self, source_path: str, target_path: Path | str) -> None: ...

    async def is_dir(self, path: str, user: str | int | None = None) -> bool: ...

    async def is_file(self, path: str, user: str | int | None = None) -> bool: ...


class HarborWorkspace:
    """``Workspace`` backed by a Harbor ``BaseEnvironment``."""

    def __init__(self, environment: HarborEnvironment, *, cwd: str = "/app") -> None:
        self._environment = environment
        self._cwd = _normalize(cwd)

    @property
    def cwd(self) -> str:
        return self._cwd

    async def run_shell(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout_seconds: int | None = None,
    ) -> CommandResult:
        resolved_cwd = _resolve(self._cwd, cwd) if cwd else self._cwd
        result = await self._environment.exec(
            command=command,
            cwd=resolved_cwd,
            timeout_sec=timeout_seconds,
        )
        return CommandResult(
            stdout=result.stdout or "",
            stderr=result.stderr or "",
            return_code=result.return_code,
        )

    async def read_file(self, path: str) -> bytes:
        resolved = _resolve(self._cwd, path)
        with tempfile.TemporaryDirectory(prefix="openharness-harbor-read-") as td:
            local = Path(td) / PurePosixPath(resolved).name
            await self._environment.download_file(resolved, local)
            return local.read_bytes()

    async def write_file(
        self,
        path: str,
        content: bytes,
        *,
        create_directories: bool = True,
    ) -> None:
        resolved = _resolve(self._cwd, path)
        if create_directories:
            parent = posixpath.dirname(resolved) or "/"
            await self._environment.exec(
                command=f"mkdir -p {shlex.quote(parent)}",
                timeout_sec=20,
            )
        with tempfile.TemporaryDirectory(prefix="openharness-harbor-write-") as td:
            local = Path(td) / PurePosixPath(resolved).name
            local.write_bytes(content)
            await self._environment.upload_file(local, resolved)

    async def file_exists(self, path: str) -> bool:
        return await self._environment.is_file(_resolve(self._cwd, path))

    async def dir_exists(self, path: str) -> bool:
        return await self._environment.is_dir(_resolve(self._cwd, path))


def _normalize(path: str) -> str:
    """Ensure *path* is absolute and cleaned of redundant separators."""
    if not path.startswith("/"):
        path = "/" + path
    return posixpath.normpath(path)


def _resolve(base: str, path: str) -> str:
    """Resolve *path* against *base*, keeping absolute paths as-is."""
    if path.startswith("/"):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(base, path))


assert isinstance(HarborWorkspace.__new__(HarborWorkspace), Workspace)
