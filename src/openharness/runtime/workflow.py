"""High-level workflow orchestration for OpenHarness runs."""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel

from openharness.agents.contracts import AgentRunResult, TaskDefinition
from openharness.agents.factory import AgentFactory
from openharness.api.client import SupportsStreamingMessages
from openharness.api.provider import detect_provider
from openharness.config import load_settings
from openharness.observability import create_trace_observer
from openharness.observability import NullTraceObserver, TraceObserver
from openharness.permissions.modes import PermissionMode
from openharness.runtime.session import AgentLogPaths, AgentRuntime
from openharness.runs.context import RunContext
from openharness.workspace import Workspace


class WorkflowResult(BaseModel):
    """The result of a workflow run."""

    agent_result: AgentRunResult


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
        run_context: RunContext | None = None,
    ) -> WorkflowResult:
        """Launch the specified agent to solve the task.

        Args:
            task: The definition of the task to solve.
            agent_name: The name of the agent configuration (YAML) to load.
            api_client: Optional explicit API client to use instead of the default.
            log_paths: Optional paths for JSONL event logging.
            trace_observer: Optional telemetry observer.
        """
        agent = self.factory.create(agent_name)
        run = run_context or RunContext.create(
            self.workspace.cwd,
            interface="workflow",
            run_id=getattr(trace_observer, "run_id", None),
            metadata={"agent_name": agent_name},
        )
        settings = load_settings().merge_cli_overrides(model=agent.config.model)
        owns_observer = trace_observer is None
        observer = trace_observer or create_trace_observer(
            session_id=uuid4().hex[:12],
            interface="workflow",
            cwd=self.workspace.cwd,
            model=agent.config.model,
            provider=detect_provider(settings).name,
            run_id=run.run_id,
        )
        run.bind_trace_observer(observer)
        run.start(
            metadata={
                "agent_name": agent_name,
                "instruction": task.instruction,
                "payload_keys": sorted(task.payload.keys()),
            }
        )
        if owns_observer:
            observer.start_session(
                metadata={
                    "agent_name": agent_name,
                    "run_id": run.run_id,
                }
            )
        runtime = AgentRuntime(
            workspace=self.workspace,
            permission_mode=PermissionMode.FULL_AUTO,
            api_client=api_client,
            log_paths=log_paths or run.build_log_paths(),
            trace_observer=observer,
        )
        try:
            result = await agent.run(task=task, runtime=runtime)
        except Exception as exc:
            if owns_observer:
                observer.end_session(metadata={"error": str(exc)})
            run.finish(
                status="failed",
                error=str(exc),
                metrics={
                    "input_tokens": runtime.build_result("").input_tokens,
                    "output_tokens": runtime.build_result("").output_tokens,
                },
            )
            raise

        if owns_observer:
            observer.end_session(
                output={"final_text": result.final_text},
                metadata={"agent_name": agent_name},
            )
        run.finish(
            status="completed",
            results={
                "final_text": result.final_text,
            },
            metrics={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.input_tokens + result.output_tokens,
            },
        )
        return WorkflowResult(agent_result=result)
