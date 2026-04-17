"""Tool for creating and entering git worktrees."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class EnterWorktreeToolInput(BaseModel):
    """Arguments for entering a worktree."""

    branch: str = Field(description="Target branch name for the worktree")
    path: str | None = Field(default=None, description="Optional worktree path")
    create_branch: bool = Field(default=True)
    base_ref: str = Field(default="HEAD", description="Base ref when creating a new branch")


class EnterWorktreeTool(BaseTool):
    """Create a git worktree."""

    name = "enter_worktree"
    description = "Create a git worktree and return its path."
    input_model = EnterWorktreeToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(
        self,
        arguments: EnterWorktreeToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)

        top = await workspace.run_shell("git rev-parse --show-toplevel")
        if top.return_code != 0:
            return ToolResult(output="enter_worktree requires a git repository", is_error=True)

        repo_root = top.stdout.strip()
        wt_path = _worktree_path(repo_root, arguments.branch, arguments.path)

        if arguments.create_branch:
            cmd = f"git worktree add -b {_sq(arguments.branch)} {_sq(wt_path)} {_sq(arguments.base_ref)}"
        else:
            cmd = f"git worktree add {_sq(wt_path)} {_sq(arguments.branch)}"

        result = await workspace.run_shell(cmd, cwd=repo_root)
        output = (result.stdout or result.stderr).strip() or f"Created worktree {wt_path}"
        if result.return_code != 0:
            return ToolResult(output=output, is_error=True)
        return ToolResult(output=f"{output}\nPath: {wt_path}")


def _worktree_path(repo_root: str, branch: str, path: str | None) -> str:
    if path:
        from pathlib import Path as P

        p = P(path).expanduser()
        if not p.is_absolute():
            p = P(repo_root) / p
        return str(p.resolve())
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "worktree"
    return f"{repo_root}/.openharness/worktrees/{slug}"


def _sq(s: str) -> str:
    """Shell-quote a string."""
    return "'" + s.replace("'", "'\\''") + "'"
