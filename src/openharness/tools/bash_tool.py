"""Shell command execution tool."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.workspace import LocalWorkspace, Workspace


class BashToolInput(BaseModel):
    """Arguments for the bash tool."""

    command: str = Field(description="Shell command to execute")
    cwd: str | None = Field(default=None, description="Working directory override")
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class BashTool(BaseTool):
    """Execute a shell command with stdout/stderr capture."""

    name = "bash"
    description = "Run a shell command."
    input_model = BashToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(self, arguments: BashToolInput, context: ToolExecutionContext) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)
        cwd = _resolve(workspace.cwd, arguments.cwd) if arguments.cwd else workspace.cwd
        result = await workspace.run_shell(
            arguments.command, cwd=cwd, timeout_seconds=arguments.timeout_seconds,
        )
        return ToolResult(
            output=format_command_output(result.stdout, result.stderr),
            is_error=result.return_code != 0,
            metadata={"returncode": result.return_code, "cwd": cwd},
        )


def format_command_output(stdout: str | None, stderr: str | None) -> str:
    """Combine stdout and stderr into a single bounded tool response."""
    parts = [p.rstrip() for p in (stdout, stderr) if p]
    text = "\n".join(parts).strip()
    if not text:
        return "(no output)"
    return f"{text[:12000]}\n...[truncated]..." if len(text) > 12000 else text


def _resolve(base: str, candidate: str) -> str:
    from pathlib import Path

    p = Path(candidate).expanduser()
    if p.is_absolute():
        return str(p)
    return str((Path(base) / candidate).resolve())
