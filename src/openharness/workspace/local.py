"""Local workspace backed by pathlib and asyncio subprocess."""

from __future__ import annotations

import asyncio
from pathlib import Path

from openharness.workspace.contracts import CommandResult, Workspace


class LocalWorkspace:
    """``Workspace`` implementation that operates on the local filesystem."""

    def __init__(self, cwd: str | Path) -> None:
        self._cwd = str(Path(cwd).resolve())

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
        resolved_cwd = cwd or self._cwd
        process = await asyncio.create_subprocess_exec(
            "/bin/bash", "-lc", command,
            cwd=resolved_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return CommandResult(
                stdout="", stderr=f"Command timed out after {timeout_seconds}s",
                return_code=-1,
            )
        return CommandResult(
            stdout=stdout.decode("utf-8", errors="replace") if stdout else "",
            stderr=stderr.decode("utf-8", errors="replace") if stderr else "",
            return_code=process.returncode or 0,
        )

    async def read_file(self, path: str) -> bytes:
        return Path(path).read_bytes()

    async def write_file(
        self, path: str, content: bytes, *, create_directories: bool = True,
    ) -> None:
        p = Path(path)
        if create_directories:
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)

    async def file_exists(self, path: str) -> bool:
        return Path(path).is_file()

    async def dir_exists(self, path: str) -> bool:
        return Path(path).is_dir()


assert isinstance(LocalWorkspace("/tmp"), Workspace)
