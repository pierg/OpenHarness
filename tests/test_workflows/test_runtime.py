"""Tests for workflow runtime trace context propagation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openharness.agents.contracts import TaskDefinition
from openharness.swarm.types import SpawnResult
from openharness.workflows.runtime import WorkflowRuntime
from openharness.workflows.specs import WorkflowSpec


class _RecordingTraceObserver:
    enabled = True
    trace_id = "trace-root-123"
    run_id = "run-root-123"
    trace_name = "run-root-123"

    def start_session(self, *, metadata=None) -> None:
        del metadata

    def end_session(self, *, output=None, metadata=None) -> None:
        del output, metadata

    def start_model_call(self, *, model, input, metadata=None, model_parameters=None):
        raise NotImplementedError

    def start_tool_call(self, *, tool_name, tool_input, metadata=None):
        raise NotImplementedError

    def start_span(self, *, name, input=None, metadata=None):
        raise NotImplementedError

    def model_call(self, *, model, input, metadata=None, model_parameters=None):
        raise NotImplementedError

    def tool_call(self, *, tool_name, tool_input, metadata=None):
        raise NotImplementedError

    def span(self, *, name, input=None, metadata=None):
        raise NotImplementedError

    def flush(self) -> None:
        return None


class _FakeExecutor:
    def __init__(self) -> None:
        self.spawn_calls = []

    async def spawn(self, config):
        self.spawn_calls.append(config)
        return SpawnResult(
            task_id="task-123",
            agent_id=f"{config.name}@{config.team}",
            backend_type="in_process",
        )


def _workflow_spec() -> WorkflowSpec:
    return WorkflowSpec(
        name="workflow-demo",
        topology="single",
        roles={"worker": {"agent": "workflow_worker", "mode": "spawned"}},
    )


def _agent_definition() -> SimpleNamespace:
    return SimpleNamespace(
        model="claude-test",
        system_prompt="",
        system_prompt_mode="append",
        color=None,
        permissions=[],
        plan_mode_required=False,
        allow_permission_prompts=False,
        runner="yaml_workflow",
        agent_config_name="workflow_worker",
        agent_architecture="simple",
        permission_mode="default",
        tools=["*"],
        disallowed_tools=None,
        initial_prompt=None,
        max_turns=None,
    )


async def test_spawn_propagates_run_id_into_worker_trace_context(tmp_path: Path) -> None:
    runtime = WorkflowRuntime(
        workspace_root=tmp_path,
        spec=_workflow_spec(),
        trace_observer=_RecordingTraceObserver(),
    )
    executor = _FakeExecutor()

    async def _resolve_role_cwd(role: str) -> Path:
        assert role == "worker"
        return tmp_path

    runtime._resolve_role_cwd = _resolve_role_cwd  # type: ignore[method-assign]
    runtime._resolve_agent_definition = lambda _: _agent_definition()  # type: ignore[method-assign]
    runtime._resolve_executor = lambda _: executor  # type: ignore[method-assign]

    await runtime.spawn("worker", TaskDefinition(instruction="Fix the bug"))

    assert len(executor.spawn_calls) == 1
    config = executor.spawn_calls[0]
    assert config.run_id == "run-root-123"
    assert config.task_payload["workflow_context"]["run_id"] == "run-root-123"
    assert config.task_payload["workflow_context"]["trace_id"] == "trace-root-123"
