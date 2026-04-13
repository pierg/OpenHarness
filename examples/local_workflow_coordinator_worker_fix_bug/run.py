"""Run a coordinator with two persistent workers.

Feature slice:
- `TeamOrchestrator`
- persistent workers with mailboxes
- inline coordinator workflow
- runtime-generated run ID propagation to spawned workers

Usage:
    uv run python examples/local_workflow_coordinator_worker_fix_bug/run.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _shared.bugfix_task import (  # noqa: E402
    EXAMPLE_MODEL,
    INSTRUCTION,
    configure_local_langfuse,
    install_project_agent_configs,
    log_run_summary,
    prepare_run_workspace,
    script_prints_twelve,
)
from openharness.api.factory import create_api_client  # noqa: E402
from openharness.config import load_settings  # noqa: E402
from openharness.coordinator.agent_definitions import get_agent_definition  # noqa: E402
from openharness.observability import create_trace_observer  # noqa: E402
from openharness.runs.context import RunContext  # noqa: E402
from openharness.swarm.orchestration import TeamOrchestrator  # noqa: E402

log = logging.getLogger(__name__)


def _message_text(message: object) -> str:
    payload = getattr(message, "payload", {}) or {}
    if payload.get("content"):
        return str(payload["content"])
    if payload.get("summary"):
        return str(payload["summary"])
    return str(getattr(message, "summary", "") or payload)


async def main() -> None:
    from openharness.observability.logging import setup_logging

    setup_logging()
    configure_local_langfuse()
    settings = load_settings().merge_cli_overrides(model=EXAMPLE_MODEL)
    api_client = create_api_client(settings)
    run_workspace = prepare_run_workspace("local_workflow_coordinator_worker_fix_bug")
    workspace = run_workspace.workspace
    install_project_agent_configs(
        workspace.root,
        ("workflow_coordinator.yaml", "workflow_worker.yaml"),
    )

    team_name = f"workflow-team-{uuid.uuid4().hex[:8]}"
    run_context = RunContext.create(
        EXAMPLES_ROOT.parent,
        interface="example_workflow_team",
        run_id=run_workspace.run_id,
        workspace_dir=workspace.root,
        metadata={"example": "local_workflow_coordinator_worker_fix_bug"},
    )
    trace_observer = create_trace_observer(
        session_id=uuid.uuid4().hex[:12],
        interface="example_workflow_team",
        cwd=str(workspace.root),
        model=settings.model,
        run_id=run_context.run_id,
    )
    run_context.bind_trace_observer(trace_observer)
    run_context.start(
        metadata={
            "team_name": team_name,
            "model": settings.model,
        }
    )
    trace_observer.start_session(
        metadata={
            "example": "local_workflow_coordinator_worker_fix_bug",
            "team_name": team_name,
            "run_id": run_context.run_id,
        }
    )
    run_context.set_trace_identity(
        trace_id=trace_observer.trace_id,
        trace_url=trace_observer.trace_url,
    )
    run_context.save_manifest()
    run_context.log_start()

    coordinator_def = get_agent_definition("workflow_coordinator", cwd=str(workspace.root))
    worker_def = get_agent_definition("workflow_worker", cwd=str(workspace.root))
    if coordinator_def is None or worker_def is None:
        trace_observer.end_session(metadata={"status": "error"})
        raise RuntimeError("Workflow team agent definitions were not loaded")

    result = None
    mailbox_messages = []
    passed = False
    try:
        worker_handles = {
            "worker_a": f"worker_a@{team_name}",
            "worker_b": f"worker_b@{team_name}",
        }
        workflow_context = {
            "team_name": team_name,
            "leader_agent_id": f"leader@{team_name}",
            "worker_handles": worker_handles,
            "run_id": run_context.run_id,
            "trace_id": trace_observer.trace_id,
        }

        async with TeamOrchestrator(
            team_name,
            workspace.root,
            trace_observer=trace_observer,
            run_context=run_context,
        ) as team:
            await team.spawn_worker(
                role_name="worker_a",
                agent_def=worker_def,
                bootstrap_task="You are worker A. Report ready to the leader, then wait.",
                payload={"workflow_context": workflow_context},
            )
            await team.spawn_worker(
                role_name="worker_b",
                agent_def=worker_def,
                bootstrap_task="You are worker B. Report ready to the leader, then wait.",
                payload={"workflow_context": workflow_context},
            )
            await team.wait_for_updates(["worker_a", "worker_b"], timeout=20.0)

            result = await team.run_inline(
                agent_def=coordinator_def,
                instruction=INSTRUCTION,
                identity=workflow_context["leader_agent_id"],
                payload={"workflow_context": workflow_context},
                api_client=api_client,
            )
            passed = script_prints_twelve(workspace.root)
            mailbox_messages = await team.read_all_mailboxes()
    except Exception as exc:
        trace_observer.end_session(output={"error": str(exc)}, metadata={"status": "error"})
        run_context.finish(status="failed", error=str(exc), metadata={"team_name": team_name})
        raise
    else:
        trace_observer.end_session(
            output={
                "final_text": result.agent_result.final_text if result is not None else "",
                "passed": passed,
            },
            metadata={"status": "completed", "passed": passed},
        )
        run_context.finish(
            status="completed" if passed else "failed",
            error=None if passed else "The final script output was not 12.",
            metadata={
                "team_name": team_name,
                "trace_id": trace_observer.trace_id,
                "trace_url": trace_observer.trace_url,
            },
            results={
                "passed": passed,
                "final_text": result.agent_result.final_text if result is not None else "",
                "mailbox_message_count": len(mailbox_messages),
            },
            metrics={
                "input_tokens": result.agent_result.input_tokens if result is not None else 0,
                "output_tokens": result.agent_result.output_tokens if result is not None else 0,
                "total_tokens": (
                    result.agent_result.input_tokens + result.agent_result.output_tokens
                    if result is not None
                    else 0
                ),
            },
        )

    view = SimpleNamespace(
        final_text=result.agent_result.final_text if result is not None else "",
        mailbox_messages=mailbox_messages,
    )
    log_run_summary(
        log,
        run_id=run_context.run_id,
        workspace=workspace.root,
        run_dir=run_context.run_dir,
        passed=passed,
        extra={
            "Team": team_name,
            "Model": settings.model,
            "Trace URL": trace_observer.trace_url,
            "Final": view.final_text,
        },
    )
    if view.mailbox_messages:
        log.info("Mailbox:")
        for message in view.mailbox_messages:
            log.info("  %s -> %s: %s", message.sender, message.recipient, _message_text(message))


if __name__ == "__main__":
    asyncio.run(main())
