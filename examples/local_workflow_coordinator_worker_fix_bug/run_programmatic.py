"""Run the coordinator/worker demo with code-level orchestration.

This variant keeps the same persistent worker setup as ``run.py`` but moves
the coordination logic out of the coordinator prompt and into Python control
flow. The leader process explicitly:

1. Spawns the workers
2. Waits for bootstrap readiness
3. Assigns the implementation step to worker A
4. Waits for worker A to report completion
5. Assigns verification to worker B
6. Verifies the result locally and, if needed, performs one repair loop
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

from openharness.config import load_settings
from openharness.coordinator.agent_definitions import get_agent_definition
from openharness.observability import create_trace_observer
from openharness.services.runs import generate_run_id
from openharness.swarm.mailbox import MailboxMessage
from openharness.swarm.orchestration import TeamOrchestrator
from demo_support import (
    TOPOLOGY_NAME,
    WORKFLOW_NAME,
    format_result_lines,
    script_prints_twelve,
    seed_workspace,
)

log = logging.getLogger(__name__)


def _message_text(message: MailboxMessage) -> str:
    payload = message.payload or {}
    if "content" in payload and payload["content"]:
        return str(payload["content"])
    if "summary" in payload and payload["summary"]:
        return str(payload["summary"])
    if message.summary:
        return message.summary
    return str(payload)


def _worker_instruction(role_name: str) -> str:
    if role_name == "worker_a":
        return (
            "Open `sum_evens.py`, fix the bug so that `sum_evens([1, 2, 3, 4, 5, 6])` "
            "returns `12`, and save the file.\n\n"
            "Do not do the final verification step. After you finish editing, send one "
            "concise update to the leader describing exactly what you changed."
        )
    if role_name == "worker_b":
        return (
            "Run `python sum_evens.py` from the current directory and verify whether the "
            "output is `12`.\n\n"
            "Do not edit files. Send one concise update to the leader that includes the "
            "exact script output and whether the verification passed."
        )
    raise ValueError(f"Unsupported role {role_name!r}")


async def _wait_for_role_update(
    team: TeamOrchestrator,
    role_name: str,
    *,
    timeout: float,
) -> MailboxMessage:
    updates = await team.wait_for_updates([role_name], timeout=timeout)
    agent_id = team.workers[role_name]
    for message in reversed(updates):
        if message.sender == agent_id:
            return message
    raise RuntimeError(f"No update received from {role_name!r}")


async def _assign_and_wait(
    team: TeamOrchestrator,
    role_name: str,
    instruction: str,
    *,
    timeout: float,
) -> MailboxMessage:
    await team.send(role_name, instruction)
    return await _wait_for_role_update(team, role_name, timeout=timeout)


async def main() -> None:
    os.environ.setdefault("OPENHARNESS_LANGFUSE_FLUSH_MODE", "live")

    from openharness.observability.logging import setup_logging

    setup_logging()
    settings = load_settings()
    run_id = generate_run_id()
    team_name = f"{WORKFLOW_NAME.replace('_', '-')}-programmatic-{uuid.uuid4().hex[:8]}"

    with tempfile.TemporaryDirectory(prefix="oh-swarm-coordinator-") as tmpdir:
        workspace_dir = Path(tmpdir)
        seed_workspace(workspace_dir, model=settings.model)

        trace_observer = create_trace_observer(
            session_id=uuid.uuid4().hex[:12],
            interface="example_workflow_coordinator_worker_programmatic",
            cwd=str(workspace_dir),
            model=settings.model,
            run_id=run_id,
        )
        trace_observer.start_session(
            metadata={
                "example": "local_workflow_coordinator_worker_fix_bug",
                "workflow_name": WORKFLOW_NAME,
                "topology": TOPOLOGY_NAME,
                "coordination_mode": "code_level",
                "run_id": run_id,
            }
        )

        log.info(f"Run ID:    {run_id}")
        log.info(f"Workspace: {workspace_dir}")
        log.info(f"Trace ID:  {trace_observer.trace_id}")
        log.info("Running programmatic coordinator... (Check Langfuse for live updates)")

        worker_def = get_agent_definition("workflow_worker", cwd=str(workspace_dir))
        if not worker_def:
            trace_observer.end_session(
                output={"error": "Demo worker definition is missing"},
                metadata={"status": "error"},
            )
            raise RuntimeError("Demo worker definition is missing")

        mailbox_messages: list[MailboxMessage] = []
        passed = False
        final_text = ""

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

        try:
            async with TeamOrchestrator(
                team_name,
                workspace_dir,
                trace_observer=trace_observer,
            ) as team:
                try:
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
                    await team.wait_for_updates(["worker_a", "worker_b"], timeout=15.0)

                    log.info("Assigning implementation to worker_a...")
                    fix_update = await _assign_and_wait(
                        team,
                        "worker_a",
                        _worker_instruction("worker_a"),
                        timeout=90.0,
                    )
                    log.info("worker_a: %s", _message_text(fix_update))

                    log.info("Assigning verification to worker_b...")
                    verify_update = await _assign_and_wait(
                        team,
                        "worker_b",
                        _worker_instruction("worker_b"),
                        timeout=60.0,
                    )
                    log.info("worker_b: %s", _message_text(verify_update))

                    passed = script_prints_twelve(workspace_dir)
                    if not passed:
                        log.info("Local verification failed; asking worker_a to repair...")
                        repair_instruction = (
                            "The local verification still failed after your first patch.\n\n"
                            f"Worker B reported: {_message_text(verify_update)}\n\n"
                            "Re-open `sum_evens.py`, correct the bug, save the file, and send one "
                            "concise update describing the final fix."
                        )
                        repair_update = await _assign_and_wait(
                            team,
                            "worker_a",
                            repair_instruction,
                            timeout=90.0,
                        )
                        log.info("worker_a repair: %s", _message_text(repair_update))

                        log.info("Re-running verification with worker_b...")
                        verify_update = await _assign_and_wait(
                            team,
                            "worker_b",
                            _worker_instruction("worker_b"),
                            timeout=60.0,
                        )
                        log.info("worker_b: %s", _message_text(verify_update))
                        passed = script_prints_twelve(workspace_dir)

                    mailbox_messages = await team.read_all_mailboxes()
                    final_text = (
                        "Programmatic coordinator completed the fix and verification flow."
                        if passed
                        else "Programmatic coordinator exhausted the repair loop without a passing result."
                    )
                finally:
                    pass
                    
        except Exception as exc:
            trace_observer.end_session(
                output={"error": str(exc)},
                metadata={"status": "error"},
            )
            raise
        else:
            trace_observer.end_session(
                output={"final_text": final_text, "passed": passed},
                metadata={"status": "completed", "passed": passed},
            )

        result_view = SimpleNamespace(
            workflow_name=f"{WORKFLOW_NAME}_programmatic",
            topology=TOPOLOGY_NAME,
            team_name=team_name,
            final_text=final_text,
            mailbox_messages=mailbox_messages,
        )
        for line in format_result_lines(result_view, passed=passed):
            log.info(line)


if __name__ == "__main__":
    asyncio.run(main())
