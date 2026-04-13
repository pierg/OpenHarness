"""Tests for TeamOrchestrator behavior and trace propagation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openharness.agents.contracts import AgentRunResult
from openharness.runs.context import RunContext
from openharness.swarm.mailbox import create_user_message
from openharness.swarm.orchestration import TeamOrchestrator
from openharness.swarm.types import SpawnResult


class _TraceObserverStub:
    trace_id = "trace-root-123"
    run_id = "run-root-123"
    trace_name = "run-root-123"

    def start_session(self, *, metadata=None) -> None:
        del metadata

    def end_session(self, *, output=None, metadata=None) -> None:
        del output, metadata

    def flush(self) -> None:
        return None


class _FakeBackend:
    def __init__(self) -> None:
        self.spawn_calls = []

    async def spawn(self, config):
        self.spawn_calls.append(config)
        return SpawnResult(
            task_id="task-123",
            agent_id=f"{config.name}@{config.team}",
            backend_type="in_process",
        )

    async def send_message(self, agent_id, message):
        del agent_id, message

    async def shutdown(self, agent_id, *, force=False):
        del agent_id, force
        return True


class _FakeWorkflow:
    calls = []

    def __init__(self, workspace, agent_factory=None) -> None:
        del workspace, agent_factory

    async def run(
        self,
        task,
        agent_name,
        *,
        api_client=None,
        log_paths=None,
        trace_observer=None,
        run_context=None,
    ):
        del api_client, log_paths
        self.__class__.calls.append(
            {
                "task": task,
                "agent_name": agent_name,
                "trace_observer": trace_observer,
                "run_context": run_context,
            }
        )
        return SimpleNamespace(
            agent_result=AgentRunResult(output="done", input_tokens=1, output_tokens=2)
        )


def _agent_definition() -> SimpleNamespace:
    return SimpleNamespace(
        name="workflow_worker",
        agent_config_name="workflow_worker",
        model="claude-test",
        system_prompt="",
        system_prompt_mode="append",
        color=None,
        permissions=[],
        plan_mode_required=False,
        allow_permission_prompts=False,
        runner="yaml_workflow",
        agent_architecture="simple",
        permission_mode="default",
        tools=["*"],
        disallowed_tools=None,
        initial_prompt=None,
        max_turns=None,
    )


async def test_spawn_worker_propagates_run_id(tmp_path: Path) -> None:
    trace_observer = _TraceObserverStub()
    orchestrator = TeamOrchestrator(
        "team-demo",
        tmp_path,
        trace_observer=trace_observer,
    )
    orchestrator.backend = _FakeBackend()

    agent_id = await orchestrator.spawn_worker(
        role_name="worker",
        agent_def=_agent_definition(),
        bootstrap_task="Stand by",
        payload={"workflow_context": {"role": "worker"}},
    )

    assert agent_id == "worker@team-demo"
    assert len(orchestrator.backend.spawn_calls) == 1
    config = orchestrator.backend.spawn_calls[0]
    assert config.run_id == "run-root-123"


async def test_spawn_worker_propagates_run_root_from_context(tmp_path: Path) -> None:
    run_context = RunContext.create(tmp_path, interface="test", run_id="run-root-123")
    orchestrator = TeamOrchestrator(
        "team-demo",
        tmp_path,
        run_context=run_context,
    )
    orchestrator.backend = _FakeBackend()

    await orchestrator.spawn_worker(
        role_name="worker",
        agent_def=_agent_definition(),
        bootstrap_task="Stand by",
    )

    config = orchestrator.backend.spawn_calls[0]
    assert config.run_id == "run-root-123"
    assert config.run_root == str(run_context.run_dir)


async def test_run_inline_uses_shared_trace_observer(tmp_path: Path, monkeypatch) -> None:
    trace_observer = _TraceObserverStub()
    orchestrator = TeamOrchestrator(
        "team-demo",
        tmp_path,
        trace_observer=trace_observer,
    )
    monkeypatch.setattr("openharness.swarm.orchestration.AgentWorkflow", _FakeWorkflow)
    monkeypatch.setattr(
        "openharness.swarm.orchestration.AgentFactory.with_catalog_configs",
        lambda _: object(),
    )
    _FakeWorkflow.calls.clear()

    result = await orchestrator.run_inline(
        agent_def=_agent_definition(),
        instruction="Fix the bug",
        identity="coordinator@team-demo",
        payload={"workflow_context": {"role": "coordinator"}},
    )

    assert result.agent_result.final_text == "done"
    assert len(_FakeWorkflow.calls) == 1
    assert _FakeWorkflow.calls[0]["trace_observer"] is trace_observer


async def test_run_inline_uses_shared_run_context(tmp_path: Path, monkeypatch) -> None:
    run_context = RunContext.create(tmp_path, interface="test", run_id="run-root-123")
    orchestrator = TeamOrchestrator(
        "team-demo",
        tmp_path,
        run_context=run_context,
    )
    monkeypatch.setattr("openharness.swarm.orchestration.AgentWorkflow", _FakeWorkflow)
    monkeypatch.setattr(
        "openharness.swarm.orchestration.AgentFactory.with_catalog_configs",
        lambda _: object(),
    )
    _FakeWorkflow.calls.clear()

    await orchestrator.run_inline(
        agent_def=_agent_definition(),
        instruction="Fix the bug",
        identity="coordinator@team-demo",
    )

    assert _FakeWorkflow.calls[0]["run_context"] is run_context


async def test_wait_for_updates_can_leave_messages_unread(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    orchestrator = TeamOrchestrator("team-demo", tmp_path)
    orchestrator.workers = {"worker_a": "worker_a@team-demo"}
    await orchestrator.leader_mailbox.write(
        create_user_message(
            "worker_a@team-demo",
            "leader",
            "ready",
            correlation_id="corr-ready",
        )
    )

    observed = await orchestrator.wait_for_updates(
        ["worker_a"],
        timeout=0.5,
        mark_read=False,
    )

    unread = await orchestrator.leader_mailbox.read_all(unread_only=True)
    assert len(observed) == 1
    assert len(unread) == 1
