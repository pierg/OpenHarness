"""Run a coordinator and worker swarm using Python abstractions.

This example creates a team of two persistent workers and one inline
coordinator. The coordinator delegates steps via mailboxes and
waits for the workers to fix a bug in the workspace.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from openharness.api.factory import create_api_client
from openharness.config import load_settings
from openharness.coordinator.agent_definitions import get_agent_definition
from openharness.observability import create_trace_observer
from openharness.services.runs import generate_run_id
from openharness.swarm.orchestration import TeamOrchestrator
from demo_support import (
    INSTRUCTION,
    TOPOLOGY_NAME,
    WORKFLOW_NAME,
    format_result_lines,
    script_prints_twelve,
    seed_workspace,
)

log = logging.getLogger(__name__)


async def main() -> None:
    os.environ.setdefault("OPENHARNESS_LANGFUSE_FLUSH_MODE", "live")

    from openharness.observability.logging import setup_logging

    setup_logging()
    settings = load_settings()
    api_client = create_api_client(settings)
    run_id = generate_run_id()

    team_name = f"{WORKFLOW_NAME.replace('_', '-')}-{uuid.uuid4().hex[:8]}"

    with tempfile.TemporaryDirectory(prefix="oh-swarm-coordinator-") as tmpdir:
        workspace_dir = Path(tmpdir)
        seed_workspace(workspace_dir, model=settings.model)

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
                "topology": TOPOLOGY_NAME,
                "run_id": run_id,
            }
        )

        log.info(f"Run ID:    {run_id}")
        log.info(f"Workspace: {workspace_dir}")
        log.info(f"Trace ID:  {trace_observer.trace_id}")
        log.info("Running... (Check Langfuse for live updates)")

        coordinator_def = get_agent_definition("workflow_coordinator", cwd=str(workspace_dir))
        worker_def = get_agent_definition("workflow_worker", cwd=str(workspace_dir))
        if not coordinator_def or not worker_def:
            trace_observer.end_session(
                output={"error": "Demo agent definitions are missing"},
                metadata={"status": "error"},
            )
            raise RuntimeError("Demo agent definitions are missing")

        result = None
        passed = False
        mailbox_messages = []
        try:
            # Provide the handles to the agents so they know who to talk to
            worker_handles = {
                "worker_a": f"worker_a@{team_name}",
                "worker_b": f"worker_b@{team_name}",
            }

            common_context = {
                "workflow_name": WORKFLOW_NAME,
                "workflow_topology": TOPOLOGY_NAME,
                "team_name": team_name,
                "leader_agent_id": f"leader@{team_name}",
                "worker_handles": worker_handles,
                "run_id": run_id,
                "trace_id": trace_observer.trace_id,
            }

            async with TeamOrchestrator(
                team_name,
                workspace_dir,
                trace_observer=trace_observer,
            ) as team:
                log.info("Spawning worker_a...")
                await team.spawn_worker(
                    role_name="worker_a",
                    agent_def=worker_def,
                    bootstrap_task=(
                        "You are worker A. Stand by for follow-up instructions from the leader.\n"
                        "Reply once to the leader mailbox that you are ready."
                    ),
                    payload={"workflow_context": common_context},
                )

                log.info("Spawning worker_b...")
                await team.spawn_worker(
                    role_name="worker_b",
                    agent_def=worker_def,
                    bootstrap_task=(
                        "You are worker B. Stand by for follow-up instructions from the leader.\n"
                        "Reply once to the leader mailbox that you are ready."
                    ),
                    payload={"workflow_context": common_context},
                )

                log.info("Waiting for workers to report ready...")
                await team.wait_for_updates(
                    ["worker_a", "worker_b"],
                    timeout=15.0,
                    mark_read=False,
                )

                log.info("Running coordinator inline...")
                result = await team.run_inline(
                    agent_def=coordinator_def,
                    instruction=INSTRUCTION,
                    payload={"workflow_context": common_context},
                    api_client=api_client,
                )

                passed = script_prints_twelve(workspace_dir)
                mailbox_messages = await team.read_mailbox(unread_only=False)
        except Exception as exc:
            trace_observer.end_session(
                output={"error": str(exc)},
                metadata={"status": "error"},
            )
            raise
        else:
            trace_observer.end_session(
                output={
                    "final_text": result.agent_result.final_text if result is not None else "",
                    "passed": passed,
                },
                metadata={"status": "completed", "passed": passed},
            )

        # Output the run summary
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
