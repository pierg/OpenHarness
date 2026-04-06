"""Run the coordinator/worker workflow through `openharness.workflows`.

This version delegates the orchestration glue to the workflow module:
- load the workflow YAML from the project catalog
- pre-spawn worker roles
- run the coordinator inline
- collect mailbox updates and clean up worker lifecycle
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from openharness.agents.contracts import TaskDefinition
from openharness.api.factory import create_api_client
from openharness.config import load_settings
from openharness.observability import create_trace_observer
from openharness.services.runs import generate_run_id
from openharness.workflows import WorkflowEngine
from demo_support import (
    INSTRUCTION,
    WORKFLOW_NAME,
    format_result_lines,
    script_prints_twelve,
    seed_workspace,
)

log = logging.getLogger(__name__)


async def main() -> None:
    # 1. Enable live flushing so spans appear while the workflow is running
    os.environ.setdefault("OPENHARNESS_LANGFUSE_FLUSH_MODE", "live")

    from openharness.observability.logging import setup_logging

    setup_logging()

    settings = load_settings()
    run_id = generate_run_id()

    with tempfile.TemporaryDirectory(prefix="oh-workflow-coordinator-") as tmpdir:
        workspace_dir = Path(tmpdir)
        seed_workspace(
            workspace_dir,
            model=settings.model,
            include_workflow_yaml=True,
        )

        trace_observer = create_trace_observer(
            session_id=uuid4().hex[:12],
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

        engine = WorkflowEngine(
            workspace_dir,
            api_client=create_api_client(settings),
            trace_observer=trace_observer,
        )

        try:
            result = await engine.run(
                TaskDefinition(instruction=INSTRUCTION),
                workflow_name=WORKFLOW_NAME,
            )

            passed = script_prints_twelve(workspace_dir)

            for line in format_result_lines(result, passed=passed):
                log.info(line)

            trace_observer.end_session(
                output={"final_text": result.final_text, "passed": passed},
                metadata={
                    "status": "completed",
                    "passed": passed,
                },
            )
            log.info(f"Done! Passed: {passed}")

        except Exception as exc:
            trace_observer.end_session(output={"error": str(exc)}, metadata={"status": "error"})
            log.error(f"Task failed: {exc}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
