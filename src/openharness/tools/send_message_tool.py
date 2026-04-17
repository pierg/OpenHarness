"""Tool for writing messages to running agent tasks."""

from __future__ import annotations

from pydantic import BaseModel, Field

from openharness.tasks.manager import get_task_manager
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SendMessageToolInput(BaseModel):
    """Arguments for sending a follow-up message to a task."""

    task_id: str = Field(description="Target local agent task id")
    message: str = Field(description="Message to write to the task stdin")


class SendMessageTool(BaseTool):
    """Send a message to a running local agent task."""

    name = "send_message"
    description = "Send a follow-up message to a running local agent task."
    input_model = SendMessageToolInput

    async def execute(self, arguments: SendMessageToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            await get_task_manager().write_to_task(arguments.task_id, arguments.message)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=f"Sent message to task {arguments.task_id}")
