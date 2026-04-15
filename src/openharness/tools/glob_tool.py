"""Filesystem globbing tool."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class GlobToolInput(BaseModel):
    """Arguments for the glob tool."""

    pattern: str = Field(description="Glob pattern relative to the working directory")
    root: str | None = Field(default=None, description="Optional search root")
    limit: int = Field(default=200, ge=1, le=5000)


class GlobTool(BaseTool):
    """List files matching a glob pattern."""

    name = "glob"
    description = "List files matching a glob pattern."
    input_model = GlobToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    def is_read_only(self, arguments: GlobToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: GlobToolInput, context: ToolExecutionContext) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)

        if isinstance(workspace, LocalWorkspace):
            root = _resolve_path(Path(workspace.cwd), arguments.root)
            matches = await _glob(root, arguments.pattern, limit=arguments.limit)
            if not matches:
                return ToolResult(output="(no matches)")
            return ToolResult(output="\n".join(matches))

        return await self._execute_via_shell(arguments, workspace)

    async def _execute_via_shell(
        self, arguments: GlobToolInput, workspace: Workspace
    ) -> ToolResult:
        root = arguments.root or workspace.cwd
        result = await workspace.run_shell(
            f"find {_sq(root)} -path {_sq(root + '/' + arguments.pattern)} "
            f"-maxdepth 20 2>/dev/null | head -n {arguments.limit} | sort",
            cwd=root,
        )
        lines = [ln for ln in result.stdout.strip().splitlines() if ln]
        if not lines:
            return ToolResult(output="(no matches)")
        prefix = root.rstrip("/") + "/"
        relative = [ln[len(prefix) :] if ln.startswith(prefix) else ln for ln in lines]
        return ToolResult(output="\n".join(relative))


def _resolve_path(base: Path, candidate: str | None) -> Path:
    path = Path(candidate or ".").expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _sq(s: str) -> str:
    """Shell-quote a string."""
    return "'" + s.replace("'", "'\\''") + "'"


def _looks_like_git_repo(path: Path) -> bool:
    """Heuristic: include hidden paths for code repositories, not arbitrary dirs."""
    current = path
    for _ in range(6):
        git_dir = current / ".git"
        if git_dir.exists():
            return True
        if current.parent == current:
            break
        current = current.parent
    return False


async def _glob(root: Path, pattern: str, *, limit: int) -> list[str]:
    """Fast local glob implementation with ripgrep fallback."""
    rg = shutil.which("rg")
    if rg and ("**" in pattern or "/" in pattern):
        include_hidden = _looks_like_git_repo(root)
        cmd = [rg, "--files"]
        if include_hidden:
            cmd.append("--hidden")
        cmd.extend(["--glob", pattern, "."])

        from openharness.sandbox.session import get_docker_sandbox

        session = get_docker_sandbox()
        if session is not None and session.is_running:
            process = await session.exec_command(
                cmd,
                cwd=root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        lines: list[str] = []
        try:
            assert process.stdout is not None
            while len(lines) < limit:
                raw = await process.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    lines.append(line)
        finally:
            if len(lines) >= limit and process.returncode is None:
                process.terminate()
            await process.wait()

        lines.sort()
        return lines

    return sorted(str(path.relative_to(root)) for path in root.glob(pattern))[:limit]
