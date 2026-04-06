"""Fan-out / join workflow topology."""

from __future__ import annotations

import asyncio

from openharness.agents.contracts import TaskDefinition
from openharness.workflows.contracts import WorkflowRunResult
import logging

log = logging.getLogger(__name__)


class FanoutJoinTopology:
    """Run multiple inline roles in parallel, then join their outputs."""

    name = "fanout_join"

    async def run(self, spec, task: TaskDefinition, runtime) -> WorkflowRunResult:
        join_role = spec.topology_config.get("join_role") or spec.entry_role
        if not join_role:
            raise ValueError(
                "fanout_join topology requires entry_role or topology_config.join_role"
            )
        if join_role not in spec.roles:
            raise ValueError(f"Unknown join role {join_role!r}")

        configured_roles = spec.topology_config.get("fanout_roles")
        fanout_roles = list(configured_roles) if configured_roles else [
            role_name for role_name in spec.roles if role_name != join_role
        ]
        if not fanout_roles:
            raise ValueError("fanout_join topology requires at least one fanout role")

        log.info(f"Fanning out to roles: {', '.join(fanout_roles)}...")
        fanout_results = await asyncio.gather(
            *(runtime.run_inline(role_name, task) for role_name in fanout_roles)
        )
        by_role = {result.role: result for result in fanout_results}

        log.info(f"Joining results via '{join_role}'...")
        join_task = TaskDefinition(
            instruction=task.instruction,
            payload={
                **task.payload,
                "fanout_results": {role: result.final_text for role, result in by_role.items()},
            },
        )
        join_result = await runtime.run_inline(join_role, join_task)
        by_role[join_role] = join_result

        return WorkflowRunResult(
            workflow_name=spec.name,
            topology=spec.topology,
            entry_role=join_role,
            team_name=runtime.team_name,
            final_text=join_result.final_text,
            role_results=by_role,
        )
