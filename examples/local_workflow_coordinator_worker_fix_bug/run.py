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

EXAMPLES_ROOT = Path(__file__).resolve().parents[1]
if str(EXAMPLES_ROOT) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_ROOT))

from _shared.helpers import (  # noqa: E402
    get_bugfix_instruction,
    prepare_bugfix_workspace,
    script_prints_twelve,
)
from openharness.api.factory import create_api_client  # noqa: E402
from openharness.config import load_settings  # noqa: E402
from openharness.coordinator.agent_definitions import get_agent_definition  # noqa: E402
from openharness.observability import create_trace_observer  # noqa: E402
from openharness.experiments.observability import setup_local_langfuse  # noqa: E402
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
    setup_local_langfuse()
    settings = load_settings().merge_cli_overrides(model="gemini-3.1-flash-lite-preview")
    api_client = create_api_client(settings)
    workspace_dir = prepare_bugfix_workspace()

    # Install configs
    target_dir = workspace_dir / ".openharness" / "agent_configs"
    target_dir.mkdir(parents=True, exist_ok=True)
    shared_dir = Path(__file__).resolve().parents[1] / "_shared" / "agent_configs"
    for cfg in ("workflow_coordinator.yaml", "workflow_worker.yaml"):
        (target_dir / cfg).write_text(
            (shared_dir / cfg).read_text(encoding="utf-8"), encoding="utf-8"
        )

    team_name = f"workflow-team-{uuid.uuid4().hex[:8]}"
    run_context = RunContext.create(
        EXAMPLES_ROOT.parent,
        interface="example_workflow_team",
        run_id=workspace_dir.parent.name,
        workspace_dir=workspace_dir,
        metadata={"example": "local_workflow_coordinator_worker_fix_bug"},
    )
    trace_observer = create_trace_observer(
        session_id=uuid.uuid4().hex[:12],
        interface="example_workflow_team",
        cwd=str(workspace_dir),
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

    coordinator_def = get_agent_definition("workflow_coordinator", cwd=str(workspace_dir))
    worker_def = get_agent_definition("workflow_worker", cwd=str(workspace_dir))
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
            workspace_dir,
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
                instruction=get_bugfix_instruction(local=True),
                identity=workflow_context["leader_agent_id"],
                payload={"workflow_context": workflow_context},
                api_client=api_client,
            )
            passed = script_prints_twelve(workspace_dir)
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

    log.info("Run ID:    %s", run_context.run_id)
    log.info("Workspace: %s", workspace_dir.resolve())
    log.info("Run dir:   %s", run_context.run_dir)
    log.info("Passed:    %s", passed)
    log.info("Team      : %s", team_name)
    log.info("Model     : %s", settings.model)
    log.info("Trace URL : %s", trace_observer.trace_url)
    log.info("Final     : %s", result.agent_result.final_text if result is not None else "")

    for label, path in {
        "manifest": run_context.artifacts.metadata_path,
        "messages": run_context.artifacts.messages_path,
        "events": run_context.artifacts.events_path,
        "results": run_context.artifacts.results_path,
        "metrics": run_context.artifacts.metrics_path,
    }.items():
        marker = "yes" if path.exists() else "no"
        log.info("Artifact:  %-8s %s (%s)", label, path, marker)

    if mailbox_messages:
        log.info("Mailbox:")
        for message in mailbox_messages:
            log.info("  %s -> %s: %s", message.sender, message.recipient, _message_text(message))


if __name__ == "__main__":
    asyncio.run(main())
