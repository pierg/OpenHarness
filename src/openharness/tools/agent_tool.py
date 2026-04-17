"""Tool for spawning local agent tasks."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from openharness.coordinator.coordinator_mode import get_team_registry
from openharness.tasks.manager import get_task_manager
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class AgentToolInput(BaseModel):
    """Arguments for local agent spawning."""

    description: str = Field(description="Short description of the delegated work")
    prompt: str = Field(description="Full prompt for the local agent")
    model: str | None = Field(default=None)
    command: str | None = Field(default=None, description="Override spawn command")
    team: str | None = Field(default=None, description="Optional team to attach the agent to")
    mode: str = Field(
        default="local_agent",
        description="Agent mode: local_agent, remote_agent, or in_process_teammate",
    )


class AgentTool(BaseTool):
    """Spawn a local agent subprocess."""

    name = "agent"
    description = "Spawn a local background agent task."
    input_model = AgentToolInput

    async def execute(self, arguments: AgentToolInput, context: ToolExecutionContext) -> ToolResult:
        if arguments.mode not in {"local_agent", "remote_agent", "in_process_teammate"}:
            return ToolResult(
                output="Invalid mode. Use local_agent, remote_agent, or in_process_teammate.",
                is_error=True,
            )
        try:
            task = await get_task_manager().create_agent_task(
                prompt=arguments.prompt,
                description=arguments.description,
                cwd=context.cwd,
                task_type=arguments.mode,  # type: ignore[arg-type]
                model=arguments.model,
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
                command=arguments.command,
            )
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        if arguments.team:
            get_team_registry().add_agent(arguments.team, task.id)
        return ToolResult(output=f"Spawned {arguments.mode} task {task.id}")
