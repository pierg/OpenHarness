"""Filesystem globbing tool."""

from __future__ import annotations

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
    """List files matching a glob pattern.

    Uses Python-native ``Path.glob`` for local workspaces and falls back to
    ``find`` via ``workspace.run_shell`` for remote substrates.
    """

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
            return self._execute_local(arguments, workspace)
        return await self._execute_via_shell(arguments, workspace)

    def _execute_local(self, arguments: GlobToolInput, workspace: LocalWorkspace) -> ToolResult:
        root = _resolve_path(Path(workspace.cwd), arguments.root)
        matches = sorted(
            str(path.relative_to(root))
            for path in root.glob(arguments.pattern)
        )
        if not matches:
            return ToolResult(output="(no matches)")
        return ToolResult(output="\n".join(matches[: arguments.limit]))

    async def _execute_via_shell(self, arguments: GlobToolInput, workspace: Workspace) -> ToolResult:
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
        relative = [ln[len(prefix):] if ln.startswith(prefix) else ln for ln in lines]
        return ToolResult(output="\n".join(relative))


def _resolve_path(base: Path, candidate: str | None) -> Path:
    path = Path(candidate or ".").expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _sq(s: str) -> str:
    """Shell-quote a string."""
    return "'" + s.replace("'", "'\\''") + "'"
