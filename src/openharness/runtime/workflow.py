"""High-level workflow orchestration for OpenHarness runs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.agents.contracts import AgentRunResult, TaskDefinition
from openharness.agents.config import QuickEvaluation
from openharness.agents.factory import AgentFactory
from openharness.api.client import SupportsStreamingMessages
from openharness.observability import TraceObserver
from openharness.permissions.modes import PermissionMode
from openharness.runtime.session import AgentLogPaths, AgentRuntime
from openharness.workspace import Workspace


class WorkflowResult(BaseModel):
    """The result of a workflow run."""

    agent_result: AgentRunResult
    evaluation: dict[str, Any] = Field(default_factory=dict)


class Workflow:
    """Orchestrates an end-to-end task run over a workspace using a configured agent.

    A workflow handles:
    - Initializing the agent factory.
    - Selecting and creating the appropriate agent.
    - Constructing the AgentRuntime infrastructure (API client, tracing).
    - Executing the agent run.
    - Returning the encapsulated workflow result.
    """

    def __init__(
        self,
        workspace: Workspace,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        self.workspace = workspace
        self.factory = agent_factory or AgentFactory.with_catalog_configs(workspace.cwd)

    async def run(
        self,
        task: TaskDefinition,
        agent_name: str = "default",
        *,
        api_client: SupportsStreamingMessages | None = None,
        log_paths: AgentLogPaths | None = None,
        trace_observer: TraceObserver | None = None,
    ) -> WorkflowResult:
        """Launch the specified agent to solve the task.

        Args:
            task: The definition of the task to solve.
            agent_name: The name of the agent configuration (YAML) to load.
            api_client: Optional explicit API client to use instead of the default.
            log_paths: Optional paths for JSONL event logging.
            trace_observer: Optional telemetry observer.
        """
        config = self.factory.get_config(agent_name)
        agent = self.factory.create(agent_name)

        runtime = AgentRuntime(
            workspace=self.workspace,
            permission_mode=PermissionMode.FULL_AUTO,
            api_client=api_client,
            log_paths=log_paths,
            trace_observer=trace_observer,
        )

        result = await agent.run(task=task, runtime=runtime)

        return WorkflowResult(
            agent_result=result,
            evaluation=_run_quick_evaluations(config.evaluations, result.final_text),
        )


def _run_quick_evaluations(
    evaluations: tuple[QuickEvaluation, ...],
    output_text: str,
) -> dict[str, Any]:
    """Evaluate lightweight output assertions for YAML-configured agents."""
    if not evaluations:
        return {}

    results: list[dict[str, Any]] = []
    failures: list[str] = []

    for evaluation in evaluations:
        passed = True
        reasons: list[str] = []

        if evaluation.contains and evaluation.contains not in output_text:
            passed = False
            reasons.append(f"missing substring {evaluation.contains!r}")
        if evaluation.not_contains and evaluation.not_contains in output_text:
            passed = False
            reasons.append(f"forbidden substring {evaluation.not_contains!r} was present")

        result = {
            "name": evaluation.name,
            "passed": passed,
            "message": evaluation.message,
            "details": "; ".join(reasons) if reasons else "",
        }
        results.append(result)
        if not passed:
            failures.append(evaluation.name)

    return {
        "passed": not failures,
        "total": len(results),
        "failures": failures,
        "results": results,
    }
