"""Coordinator + persistent worker workflow topology."""

from __future__ import annotations

from openharness.agents.contracts import TaskDefinition
from openharness.workflows.contracts import WorkflowRunResult
import logging

log = logging.getLogger(__name__)


class CoordinatorWorkerTopology:
    """Pre-spawn workers, then run a coordinator inline."""

    name = "coordinator_worker"

    async def run(self, spec, task: TaskDefinition, runtime) -> WorkflowRunResult:
        coordinator_role = (
            spec.topology_config.get("coordinator_role") or spec.entry_role
        )
        if not coordinator_role:
            raise ValueError(
                "coordinator_worker topology requires entry_role or "
                "topology_config.coordinator_role"
            )
        if coordinator_role not in spec.roles:
            raise ValueError(f"Unknown coordinator role {coordinator_role!r}")

        configured_roles = spec.topology_config.get("worker_roles")
        worker_roles = list(configured_roles) if configured_roles else [
            role_name
            for role_name, role_spec in spec.roles.items()
            if role_name != coordinator_role and role_spec.mode == "spawned"
        ]
        if not worker_roles:
            raise ValueError(
                "coordinator_worker topology requires at least one spawned worker role"
            )

        workers: dict[str, object] = {}
        for role_name in worker_roles:
            worker_spec = spec.roles[role_name]
            bootstrap_instruction = (
                worker_spec.bootstrap_task
                or (
                    "You are a persistent workflow worker. "
                    "Wait for follow-up messages from the leader mailbox and act on them."
                )
            )
            worker_task = TaskDefinition(
                instruction=bootstrap_instruction,
                payload=task.payload,
            )
            log.info(f"Spawning persistent worker for role '{role_name}'...")
            workers[role_name] = await runtime.spawn(role_name, worker_task)

        coordinator_task = TaskDefinition(
            instruction=task.instruction,
            payload={
                **task.payload,
                "workflow_workers": {
                    role: worker.agent_id for role, worker in workers.items()
                },
            },
        )
        
        log.info(f"Running coordinator inline as '{coordinator_role}'...")
        coordinator_result = await runtime.run_inline(coordinator_role, coordinator_task)
        
        mailbox_messages = tuple(
            await runtime.read_mailbox("leader", unread_only=False, mark_read=False)
        )

        return WorkflowRunResult(
            workflow_name=spec.name,
            topology=spec.topology,
            entry_role=coordinator_role,
            team_name=runtime.team_name,
            final_text=coordinator_result.final_text,
            role_results={coordinator_role: coordinator_result},
            mailbox_messages=mailbox_messages,
            workers=runtime.worker_handles,
        )

