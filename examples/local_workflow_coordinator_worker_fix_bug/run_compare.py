"""Workflow architecture comparison: all workflow configs solve the same bug.

Sets up an identical buggy workspace for each registered workflow configuration,
runs them in sequence, then prints a comparison table.

Usage:
    uv run python examples/local_workflow_coordinator_worker_fix_bug/run_compare.py
    uv run python examples/local_workflow_coordinator_worker_fix_bug/run_compare.py coordinator_worker_bugfix
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from openharness.agents.contracts import TaskDefinition
from openharness.api.factory import create_api_client
from openharness.config import load_settings
from openharness.observability import create_trace_observer
from openharness.services.runs import generate_run_id
from openharness.workflows import WorkflowEngine
from openharness.workflows.catalog import get_catalog_workflow_specs

from demo_support import (
    INSTRUCTION,
    script_prints_twelve,
    seed_workspace,
)

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    workflow_name: str
    topology: str
    passed: bool
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    error: str | None = None


async def run_workflow(
    workflow_name: str,
    settings,
) -> RunResult:
    """Run a single workflow config in its own isolated workspace."""
    run_id = generate_run_id()
    with tempfile.TemporaryDirectory(prefix=f"oh-{workflow_name}-") as tmpdir:
        workspace_dir = Path(tmpdir)
        seed_workspace(
            workspace_dir,
            model=settings.model,
            include_workflow_yaml=True,
        )

        trace_observer = create_trace_observer(
            session_id=uuid4().hex[:12],
            interface="example_workflow_comparison",
            cwd=str(workspace_dir),
            model=settings.model,
            run_id=run_id,
        )
        trace_observer.start_session(
            metadata={
                "example": "local_workflow_coordinator_worker_fix_bug",
                "workflow_name": workflow_name,
                "run_id": run_id,
            }
        )
        log.info(f"Run ID:    {run_id}")

        engine = WorkflowEngine(
            workspace_dir,
            api_client=create_api_client(settings),
            trace_observer=trace_observer,
        )

        catalog = get_catalog_workflow_specs(workspace_dir)
        if workflow_name not in catalog:
            raise KeyError(f"Unknown workflow {workflow_name!r}")
        item = catalog[workflow_name]

        t0 = time.perf_counter()
        try:
            result = await engine.run(
                TaskDefinition(instruction=INSTRUCTION),
                workflow_name=workflow_name,
            )
            elapsed = time.perf_counter() - t0

            passed = script_prints_twelve(workspace_dir)

            trace_observer.end_session(
                output={"final_text": result.final_text, "passed": passed},
                metadata={"status": "completed", "passed": passed},
            )

            input_tokens = sum(
                r.agent_result.input_tokens for r in result.role_results.values() if r.agent_result
            )
            output_tokens = sum(
                r.agent_result.output_tokens for r in result.role_results.values() if r.agent_result
            )

            return RunResult(
                workflow_name=workflow_name,
                topology=item.spec.topology,
                passed=passed,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                elapsed_seconds=elapsed,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            trace_observer.end_session(
                output={"error": str(exc)},
                metadata={"status": "error"},
            )
            return RunResult(
                workflow_name=workflow_name,
                topology=item.spec.topology,
                passed=False,
                input_tokens=0,
                output_tokens=0,
                elapsed_seconds=elapsed,
                error=str(exc)[:120],
            )


def _print_table(results: list[RunResult]) -> None:
    """Pretty-print a comparison table."""
    hdr = f"{'Workflow':<30} {'Topology':<20} {'Pass':>6} {'In Tok':>8} {'Out Tok':>8} {'Time':>7}  Error"
    log.info(hdr)
    log.info("-" * len(hdr) + "----------")
    for r in results:
        status = "✅" if r.passed else "❌"
        err = r.error or ""
        log.info(
            f"{r.workflow_name:<30} {r.topology:<20} {status:>6} "
            f"{r.input_tokens:>8} {r.output_tokens:>8} {r.elapsed_seconds:>6.1f}s  {err}"
        )


async def main() -> None:
    # 1. Enable live flushing so spans appear while the agent is running
    import os

    os.environ.setdefault("OPENHARNESS_LANGFUSE_FLUSH_MODE", "live")

    from openharness.observability.logging import setup_logging

    setup_logging()

    settings = load_settings()

    # Get available workflows directly from the source directory
    from demo_support import SOURCE_OH_DIR

    catalog = get_catalog_workflow_specs(SOURCE_OH_DIR.parent)
    available = list(catalog.keys())

    # Allow filtering via CLI args
    requested = sys.argv[1:] or available
    workflows_to_run = [w for w in requested if w in available]

    if not workflows_to_run:
        log.info(f"No matching workflows. Available: {available}")
        return

    log.info("Task: Coordinate workers to fix sum_evens.py so it returns 12 instead of 9")
    log.info(f"Workflows to run: {workflows_to_run}\n")

    results: list[RunResult] = []
    for workflow_name in workflows_to_run:
        spec = catalog[workflow_name].spec
        log.info(f"--- Running: {workflow_name} (topology: {spec.topology}) ---")
        result = await run_workflow(workflow_name, settings)
        status = "✅ PASS" if result.passed else "❌ FAIL"
        log.info(
            f"    {status}  ({result.elapsed_seconds:.1f}s, {result.input_tokens + result.output_tokens} tokens)"
        )
        if result.error:
            log.info(f"    Error: {result.error}")
        log.info("")
        results.append(result)

    log.info("\n" + "=" * 80)
    log.info("COMPARISON TABLE")
    log.info("=" * 80)
    _print_table(results)

    passed = sum(1 for r in results if r.passed)
    log.info(f"\n{passed}/{len(results)} workflows solved the task.")


if __name__ == "__main__":
    asyncio.run(main())
