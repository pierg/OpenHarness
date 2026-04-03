"""Tool for triggering local named jobs on demand."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.services.cron import get_cron_job
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.tools.bash_tool import format_command_output
from openharness.workspace import LocalWorkspace, Workspace


class RemoteTriggerToolInput(BaseModel):
    """Arguments for triggering a local named job."""

    name: str = Field(description="Cron job name")
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class RemoteTriggerTool(BaseTool):
    """Run a registered cron job immediately."""

    name = "remote_trigger"
    description = "Trigger a configured local cron-style job immediately."
    input_model = RemoteTriggerToolInput

    def __init__(self, workspace: Workspace | None = None) -> None:
        self._workspace = workspace

    async def execute(
        self,
        arguments: RemoteTriggerToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        job = get_cron_job(arguments.name)
        if job is None:
            return ToolResult(output=f"Cron job not found: {arguments.name}", is_error=True)

        workspace = self._workspace or LocalWorkspace(context.cwd)
        cwd = job.get("cwd") or workspace.cwd

        result = await workspace.run_shell(
            str(job["command"]), cwd=cwd, timeout_seconds=arguments.timeout_seconds,
        )
        body = format_command_output(result.stdout, result.stderr)
        return ToolResult(
            output=f"Triggered {arguments.name}\n{body}",
            is_error=result.return_code != 0,
            metadata={"returncode": result.return_code},
        )
