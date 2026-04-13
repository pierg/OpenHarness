"""Tool for spawning local agent tasks."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from openharness.coordinator.agent_definitions import get_agent_definition
from openharness.coordinator.coordinator_mode import get_team_registry
from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateSpawnConfig
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

logger = logging.getLogger(__name__)


class AgentToolInput(BaseModel):
    """Arguments for local agent spawning."""

    description: str = Field(description="Short description of the delegated work")
    prompt: str = Field(description="Full prompt for the local agent")
    subagent_type: str | None = Field(
        default=None,
        description="Agent type for definition lookup (e.g. 'general-purpose', 'Explore', 'worker')",
    )
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

        # Look up agent definition if subagent_type is specified
        agent_def = None
        if arguments.subagent_type:
            agent_def = get_agent_definition(arguments.subagent_type, cwd=str(context.cwd))

        # Resolve team and agent name for the swarm backend
        team = arguments.team or "default"
        agent_name = (
            agent_def.subagent_type
            if agent_def is not None
            else arguments.subagent_type or "agent"
        )
        registry = get_backend_registry()
        try:
            if arguments.mode == "in_process_teammate":
                executor = registry.get_executor("in_process")
            else:
                # Keep default agents pollable through the task manager.
                executor = registry.get_executor("subprocess")

            config = TeammateSpawnConfig(
                name=agent_name,
                team=team,
                prompt=arguments.prompt,
                description=arguments.description,
                cwd=str(context.cwd),
                parent_session_id="main",
                model=arguments.model or (agent_def.model if agent_def else None),
                system_prompt=agent_def.system_prompt if agent_def else None,
                system_prompt_mode=agent_def.system_prompt_mode if agent_def else None,
                color=agent_def.color if agent_def else None,
                permissions=agent_def.permissions if agent_def else [],
                plan_mode_required=agent_def.plan_mode_required if agent_def else False,
                allow_permission_prompts=(
                    agent_def.allow_permission_prompts if agent_def else False
                ),
                runner=agent_def.runner if agent_def else "prompt_native",
                agent_config_name=agent_def.agent_config_name if agent_def else None,
                agent_architecture=agent_def.agent_architecture if agent_def else None,
                permission_mode=agent_def.permission_mode if agent_def else None,
                allowed_tools=agent_def.tools if agent_def and agent_def.tools != ["*"] else None,
                disallowed_tools=agent_def.disallowed_tools if agent_def else None,
                initial_prompt=agent_def.initial_prompt if agent_def else None,
                max_turns=agent_def.max_turns if agent_def else None,
                run_id=getattr(context.metadata.get("trace_observer"), "run_id", None),
            )
            result = await executor.spawn(config)
        except Exception as exc:
            logger.error("Failed to spawn agent: %s", exc)
            return ToolResult(output=str(exc), is_error=True)

        if not result.success:
            return ToolResult(output=result.error or "Failed to spawn agent", is_error=True)

        if arguments.team:
            registry = get_team_registry()
            try:
                registry.add_agent(arguments.team, result.task_id)
            except ValueError:
                registry.create_team(arguments.team)
                registry.add_agent(arguments.team, result.task_id)

        return ToolResult(
            output=(
                f"Spawned agent {result.agent_id} "
                f"(task_id={result.task_id}, backend={result.backend_type})"
            )
        )
