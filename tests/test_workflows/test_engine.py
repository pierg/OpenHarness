"""Tests for workflow engine execution paths."""

from __future__ import annotations

from openharness.agents.contracts import TaskDefinition
from openharness.workflows.contracts import WorkflowRunResult
from openharness.workflows.engine import WorkflowEngine
from openharness.workflows.specs import WorkflowSpec


class _FakeRuntime:
    last_instance = None

    def __init__(self, *, workspace_root, spec, trace_observer, api_client) -> None:
        self.workspace_root = workspace_root
        self.spec = spec
        self.trace_observer = trace_observer
        self.api_client = api_client
        self.started = False
        self.closed = False
        _FakeRuntime.last_instance = self

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True


class _FakeTopology:
    def __init__(self) -> None:
        self.calls: list[tuple[WorkflowSpec, TaskDefinition, _FakeRuntime]] = []

    async def run(self, spec, task, runtime) -> WorkflowRunResult:
        self.calls.append((spec, task, runtime))
        return WorkflowRunResult(
            workflow_name=spec.name,
            topology=spec.topology,
            entry_role=spec.entry_role or "main",
            team_name="team-demo",
            final_text=f"ran:{task.instruction}",
        )


async def test_workflow_engine_run_spec_executes_python_defined_spec(monkeypatch) -> None:
    topology = _FakeTopology()
    monkeypatch.setattr("openharness.workflows.engine.WorkflowRuntime", _FakeRuntime)
    monkeypatch.setattr("openharness.workflows.engine.get_topology", lambda _: topology)

    spec = WorkflowSpec(
        name="python-demo",
        topology="single",
        entry_role="main",
        roles={"main": {"agent": "default"}},
    )

    engine = WorkflowEngine("/tmp/workflow-demo")
    result = await engine.run_spec(TaskDefinition(instruction="fix it"), spec)

    runtime = _FakeRuntime.last_instance
    assert runtime is not None
    assert runtime.started is True
    assert runtime.closed is True
    assert topology.calls == [(spec, TaskDefinition(instruction="fix it"), runtime)]
    assert result.workflow_name == "python-demo"
    assert result.final_text == "ran:fix it"
