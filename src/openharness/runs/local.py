"""High-level local run orchestration."""

from __future__ import annotations

from openharness.agents.contracts import TaskDefinition
from openharness.agents.factory import AgentFactory
from openharness.runtime.workflow import Workflow
from openharness.runs.context import RunContext
from openharness.runs.specs import LocalAgentRunSpec, RunLaunchResult
from openharness.workspace import LocalWorkspace


async def run_local_agent(spec: LocalAgentRunSpec) -> RunLaunchResult:
    """Launch a local agent run with canonical run artifacts."""
    workspace = LocalWorkspace(spec.cwd)
    run_cwd = spec.run_cwd or workspace.cwd
    run_context = RunContext.create(
        run_cwd,
        interface="local_agent",
        run_id=spec.run_id,
        workspace_dir=workspace.cwd,
        metadata={
            "agent_name": spec.agent.name,
            "workspace": str(workspace.cwd),
            **spec.metadata,
        },
    )

    factory = AgentFactory.with_catalog_configs(workspace.cwd)
    config = factory.get_config(spec.agent.name)
    overrides = {}
    if spec.agent.model is not None:
        overrides["model"] = spec.agent.model
    if spec.agent.max_turns is not None:
        overrides["max_turns"] = spec.agent.max_turns
    if spec.agent.max_tokens is not None:
        overrides["max_tokens"] = spec.agent.max_tokens
    if overrides:
        factory.register(config.model_copy(update=overrides))

    workflow = Workflow(workspace, agent_factory=factory)
    await workflow.run(
        TaskDefinition(
            instruction=spec.task.instruction,
            payload=spec.task.payload,
        ),
        agent_name=spec.agent.name,
        api_client=spec.api_client,
        run_context=run_context,
    )

    return RunLaunchResult(
        run_id=run_context.run_id,
        run_dir=run_context.run_dir,
        manifest_path=run_context.manifest_path,
        trace_id=getattr(run_context.trace_observer, "trace_id", None),
        trace_url=getattr(run_context.trace_observer, "trace_url", None),
        result_path=run_context.artifacts.results_path,
        metrics_path=run_context.artifacts.metrics_path,
    )
