"""Run the same coordinator/worker orchestration without `openharness.workflows`.

This is the hand-written equivalent of ``run.py``:
- create the team and leader mailbox
- spawn the worker agents directly through the swarm backend
- inject the coordinator payload manually
- shut workers down and read mailbox messages manually
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from openharness.agents.contracts import TaskDefinition
from openharness.agents.factory import AgentFactory
from openharness.api.factory import create_api_client
from openharness.config import load_settings
from openharness.coordinator.agent_definitions import AgentDefinition, get_agent_definition
from openharness.observability import create_trace_observer
from openharness.runtime.workflow import Workflow as AgentWorkflow
from openharness.services.runs import generate_run_id
from openharness.swarm.mailbox import TeammateMailbox
from openharness.swarm.registry import get_backend_registry
from openharness.swarm.types import TeammateSpawnConfig
from openharness.workspace import LocalWorkspace
from demo_support import (
    INSTRUCTION,
    TOPOLOGY_NAME,
    WORKFLOW_NAME,
    format_result_lines,
    script_prints_twelve,
    seed_workspace,
)

log = logging.getLogger(__name__)


def _workflow_context(
    *,
    run_id: str,
    team_name: str,
    role_name: str,
    role_mode: str,
    worker_handles: dict[str, str],
) -> dict[str, object]:
    return {
        "workflow_name": WORKFLOW_NAME,
        "workflow_topology": TOPOLOGY_NAME,
        "team_name": team_name,
        "leader_agent_id": f"leader@{team_name}",
        "role_name": role_name,
        "role_mode": role_mode,
        "worker_handles": worker_handles,
        "run_id": run_id,
        "routing": {},
    }


def _worker_agent_ids(team_name: str) -> dict[str, str]:
    return {
        "worker_a": f"worker_a@{team_name}",
        "worker_b": f"worker_b@{team_name}",
    }


def _build_spawn_config(
    *,
    run_id: str,
    role_name: str,
    bootstrap_task: str,
    team_name: str,
    workspace_dir: Path,
    agent_definition: AgentDefinition,
    worker_handles: dict[str, str],
) -> TeammateSpawnConfig:
    return TeammateSpawnConfig(
        name=role_name,
        team=team_name,
        prompt=bootstrap_task,
        cwd=str(workspace_dir),
        parent_session_id="leader",
        description=bootstrap_task.strip(),
        model=agent_definition.model,
        system_prompt=agent_definition.system_prompt,
        system_prompt_mode=agent_definition.system_prompt_mode,
        color=agent_definition.color,
        permissions=agent_definition.permissions,
        plan_mode_required=agent_definition.plan_mode_required,
        allow_permission_prompts=agent_definition.allow_permission_prompts,
        run_id=run_id,
        runner=agent_definition.runner,
        agent_config_name=agent_definition.agent_config_name,
        agent_architecture=agent_definition.agent_architecture,
        permission_mode=agent_definition.permission_mode,
        allowed_tools=(
            agent_definition.tools if agent_definition.tools not in (None, ["*"]) else None
        ),
        disallowed_tools=agent_definition.disallowed_tools,
        initial_prompt=agent_definition.initial_prompt,
        max_turns=agent_definition.max_turns,
        task_payload={
            "workflow_context": _workflow_context(
                run_id=run_id,
                team_name=team_name,
                role_name=role_name,
                role_mode="spawned",
                worker_handles=worker_handles,
            ),
        },
    )


async def main() -> None:
    # 1. Enable live flushing so spans appear while the workflow is running
    os.environ.setdefault("OPENHARNESS_LANGFUSE_FLUSH_MODE", "live")

    from openharness.observability.logging import setup_logging

    setup_logging()

    settings = load_settings()
    run_id = generate_run_id()
    team_name = f"{WORKFLOW_NAME.replace('_', '-')}-{uuid.uuid4().hex[:8]}"
    worker_handles = _worker_agent_ids(team_name)
    api_client = create_api_client(settings)

    with tempfile.TemporaryDirectory(prefix="oh-workflow-coordinator-python-") as tmpdir:
        workspace_dir = Path(tmpdir)
        seed_workspace(
            workspace_dir,
            model=settings.model,
            include_workflow_yaml=False,
        )

        trace_observer = create_trace_observer(
            session_id=uuid.uuid4().hex[:12],
            interface="example_workflow_coordinator_worker",
            cwd=str(workspace_dir),
            model=settings.model,
            run_id=run_id,
        )
        trace_observer.start_session(
            metadata={
                "example": "local_workflow_coordinator_worker_fix_bug",
                "workflow_name": WORKFLOW_NAME,
                "run_id": run_id,
            }
        )

        log.info(f"Run ID:    {run_id}")
        log.info(f"Workspace: {workspace_dir}")
        log.info(f"Trace ID:  {trace_observer.trace_id}")
        log.info("Running... (Check Langfuse for live updates)")

        coordinator_def = get_agent_definition("workflow_coordinator", cwd=str(workspace_dir))
        worker_def = get_agent_definition("workflow_worker", cwd=str(workspace_dir))
        if coordinator_def is None or worker_def is None:
            raise RuntimeError("Workflow demo agent definitions are missing")

        leader_mailbox = TeammateMailbox(team_name=team_name, agent_id="leader")
        await leader_mailbox.clear()

        backend = get_backend_registry().get_executor("in_process")
        worker_a = None
        worker_b = None
        passed = False
        mailbox_messages = []

        try:
            log.info("Spawning worker_a...")
            worker_a = await backend.spawn(
                _build_spawn_config(
                    run_id=run_id,
                    role_name="worker_a",
                    bootstrap_task="You are worker A. Stand by for follow-up instructions from the leader.\nReply once to the leader mailbox that you are ready.",
                    team_name=team_name,
                    workspace_dir=workspace_dir,
                    agent_definition=worker_def,
                    worker_handles=worker_handles,
                )
            )

            log.info("Spawning worker_b...")
            worker_b = await backend.spawn(
                _build_spawn_config(
                    run_id=run_id,
                    role_name="worker_b",
                    bootstrap_task="You are worker B. Stand by for follow-up instructions from the leader.\nReply once to the leader mailbox that you are ready.",
                    team_name=team_name,
                    workspace_dir=workspace_dir,
                    agent_definition=worker_def,
                    worker_handles=worker_handles,
                )
            )

            if not worker_a.success:
                raise RuntimeError(worker_a.error or "Failed to spawn worker_a")
            if not worker_b.success:
                raise RuntimeError(worker_b.error or "Failed to spawn worker_b")

            workspace = LocalWorkspace(workspace_dir)
            factory = AgentFactory.with_catalog_configs(workspace_dir)
            coordinator = AgentWorkflow(workspace, agent_factory=factory)

            log.info("Running coordinator inline...")
            with trace_observer.span(
                name="workflow_role:coordinator",
                input={"instruction": INSTRUCTION},
                metadata={"workflow": WORKFLOW_NAME, "mode": "inline", "run_id": run_id},
            ):
                result = await coordinator.run(
                    TaskDefinition(
                        instruction=INSTRUCTION,
                        payload={
                            "workflow_context": _workflow_context(
                                run_id=run_id,
                                team_name=team_name,
                                role_name="coordinator",
                                role_mode="inline",
                                worker_handles=worker_handles,
                            ),
                        },
                    ),
                    agent_name=coordinator_def.agent_config_name or coordinator_def.name,
                    api_client=api_client,
                    trace_observer=trace_observer,
                )

            passed = script_prints_twelve(workspace_dir)
            mailbox_messages = await leader_mailbox.read_all(unread_only=False)

            log.info("Closing workers...")

            trace_observer.end_session(
                output={"final_text": result.agent_result.final_text, "passed": passed},
                metadata={
                    "status": "completed",
                    "passed": passed,
                },
            )

        except Exception as exc:
            trace_observer.end_session(output={"error": str(exc)}, metadata={"status": "error"})
            log.error(f"Task failed: {exc}")
            raise
        finally:
            if worker_a is not None and worker_a.success:
                await backend.shutdown(worker_a.agent_id)
            if worker_b is not None and worker_b.success:
                await backend.shutdown(worker_b.agent_id)

        result_view = SimpleNamespace(
            workflow_name=WORKFLOW_NAME,
            topology=TOPOLOGY_NAME,
            team_name=team_name,
            final_text=result.agent_result.final_text,
            mailbox_messages=mailbox_messages,
        )
        for line in format_result_lines(result_view, passed=passed):
            log.info(line)


if __name__ == "__main__":
    asyncio.run(main())
