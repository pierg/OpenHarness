"""Tool for removing git worktrees."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class ExitWorktreeToolInput(BaseModel):
    """Arguments for worktree removal."""

    path: str = Field(description="Worktree path to remove")


class ExitWorktreeTool(BaseTool):
    """Remove a git worktree."""

    name = "exit_worktree"
    description = "Remove a git worktree by path."
    input_model = ExitWorktreeToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(
        self,
        arguments: ExitWorktreeToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)
        path = _resolve(workspace.cwd, arguments.path)

        result = await workspace.run_shell(
            f"git worktree remove --force {_sq(path)}",
        )
        output = (result.stdout or result.stderr).strip() or f"Removed worktree {path}"
        return ToolResult(output=output, is_error=result.return_code != 0)


def _resolve(base: str, candidate: str) -> str:
    p = Path(candidate).expanduser()
    if p.is_absolute():
        return str(p)
    return str((Path(base) / candidate).resolve())


def _sq(s: str) -> str:
    """Shell-quote a string."""
    return "'" + s.replace("'", "'\\''") + "'"
