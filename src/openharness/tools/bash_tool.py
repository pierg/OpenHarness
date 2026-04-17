"""Shell command execution tool."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from openharness.sandbox import SandboxUnavailableError
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.utils.shell import create_shell_subprocess
from openharness.workspace import LocalWorkspace, Workspace


class BashToolInput(BaseModel):
    """Arguments for the bash tool."""

    command: str = Field(description="Shell command to execute")
    cwd: str | None = Field(default=None, description="Working directory override")
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class BashTool(BaseTool):
    """Execute a shell command with stdout/stderr capture."""

    name = "bash"
    description = "Run a shell command in the local repository."
    input_model = BashToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(self, arguments: BashToolInput, context: ToolExecutionContext) -> ToolResult:
        workspace = self._workspace or LocalWorkspace(context.cwd)
        cwd = _resolve_path(Path(workspace.cwd), arguments.cwd) if arguments.cwd else Path(workspace.cwd)

        if isinstance(workspace, LocalWorkspace):
            try:
                process = await create_shell_subprocess(
                    arguments.command,
                    cwd=cwd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except SandboxUnavailableError as exc:
                return ToolResult(output=str(exc), is_error=True)

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=arguments.timeout_seconds,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    output=f"Command timed out after {arguments.timeout_seconds} seconds",
                    is_error=True,
                )

            return ToolResult(
                output=format_command_output(
                    stdout.decode("utf-8", errors="replace") if stdout else None,
                    stderr.decode("utf-8", errors="replace") if stderr else None,
                ),
                is_error=process.returncode != 0,
                metadata={"returncode": process.returncode, "cwd": str(cwd)},
            )

        result = await workspace.run_shell(
            arguments.command,
            cwd=str(cwd),
            timeout_seconds=arguments.timeout_seconds,
        )
        return ToolResult(
            output=format_command_output(result.stdout, result.stderr),
            is_error=result.return_code != 0,
            metadata={"returncode": result.return_code, "cwd": str(cwd)},
        )


def format_command_output(stdout: str | None, stderr: str | None) -> str:
    """Combine stdout and stderr into a single bounded tool response."""
    parts = [p.rstrip() for p in (stdout, stderr) if p]
    text = "\n".join(parts).strip()
    if not text:
        return "(no output)"
    return f"{text[:12000]}\n...[truncated]..." if len(text) > 12000 else text


def _resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / candidate
    return path.resolve()
