"""Workflow engine for running declarative workflow specs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openharness.agents.contracts import TaskDefinition
from openharness.observability import NullTraceObserver, TraceObserver
from openharness.workflows.catalog import get_catalog_workflow_specs
from openharness.workflows.registry import get_topology
from openharness.workflows.runtime import WorkflowRuntime
from openharness.workflows.specs import WorkflowSpec


class WorkflowEngine:
    """Load and run workflow specs from the merged catalog."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        trace_observer: TraceObserver | None = None,
        api_client: Any | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.trace_observer = trace_observer or NullTraceObserver()
        self.api_client = api_client

    async def run(self, task: TaskDefinition, workflow_name: str):
        """Execute one named workflow against *task*."""
        catalog = get_catalog_workflow_specs(self.workspace_root)
        try:
            item = catalog[workflow_name]
        except KeyError as exc:
            raise KeyError(
                f"Unknown workflow {workflow_name!r}. Available: {sorted(catalog)}"
            ) from exc

        return await self.run_spec(task, item.spec)

    async def run_spec(
        self,
        task: TaskDefinition,
        workflow_spec: WorkflowSpec,
    ):
        """Execute one workflow spec constructed in Python."""
        topology = get_topology(workflow_spec.topology)
        runtime = WorkflowRuntime(
            workspace_root=self.workspace_root,
            spec=workflow_spec,
            trace_observer=self.trace_observer,
            api_client=self.api_client,
        )
        await runtime.start()
        try:
            with self.trace_observer.span(
                name=f"workflow:{workflow_spec.name}",
                input={"instruction": task.instruction, "payload": task.payload},
                metadata={
                    "topology": workflow_spec.topology,
                    "run_id": getattr(self.trace_observer, "run_id", None),
                },
            ):
                return await topology.run(workflow_spec, task, runtime)
        finally:
            await runtime.close()
