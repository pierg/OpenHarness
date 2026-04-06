"""Tests for built-in workflow topologies."""

from __future__ import annotations

from openharness.agents.contracts import TaskDefinition
from openharness.workflows.contracts import RoleRunResult, WorkerHandle, WorkflowMessage
from openharness.workflows.specs import WorkflowSpec
from openharness.workflows.topologies.coordinator_worker import CoordinatorWorkerTopology
from openharness.workflows.topologies.fanout_join import FanoutJoinTopology
from openharness.workflows.topologies.single import SingleTopology


class FakeRuntime:
    def __init__(self) -> None:
        self.team_name = "team-demo"
        self.worker_handles: dict[str, WorkerHandle] = {}
        self.inline_calls: list[tuple[str, TaskDefinition]] = []
        self.spawn_calls: list[tuple[str, TaskDefinition]] = []
        self.send_calls: list[tuple[str, str]] = []
        self.mailbox_reads: list[tuple[str, bool]] = []
        self._mailbox_batches = [
            [
                WorkflowMessage(
                    id="ready-implementer",
                    type="user_message",
                    sender="implementer@team-demo",
                    recipient="leader",
                    text="implementer ready",
                    timestamp=0.0,
                ),
                WorkflowMessage(
                    id="ready-verifier",
                    type="user_message",
                    sender="verifier@team-demo",
                    recipient="leader",
                    text="verifier ready",
                    timestamp=0.0,
                ),
            ],
            [
                WorkflowMessage(
                    id="implementer-done",
                    type="user_message",
                    sender="implementer@team-demo",
                    recipient="leader",
                    text="implemented fix",
                    timestamp=1.0,
                )
            ],
            [
                WorkflowMessage(
                    id="verifier-done",
                    type="user_message",
                    sender="verifier@team-demo",
                    recipient="leader",
                    text="verified output 12",
                    timestamp=2.0,
                )
            ],
        ]
        self._full_mailbox = [
            message
            for batch in self._mailbox_batches
            for message in batch
        ]

    async def run_inline(self, role: str, task: TaskDefinition) -> RoleRunResult:
        self.inline_calls.append((role, task))
        if role == "joiner":
            fanout_results = task.payload["fanout_results"]
            text = " | ".join(f"{name}={value}" for name, value in sorted(fanout_results.items()))
        elif role == "coordinator":
            text = ",".join(sorted(task.payload["workflow_workers"].values()))
        else:
            text = f"{role}:{task.instruction}"
        return RoleRunResult(role=role, final_text=text, workspace_cwd="/tmp")

    async def spawn(self, role: str, task: TaskDefinition) -> WorkerHandle:
        self.spawn_calls.append((role, task))
        handle = WorkerHandle(
            role=role,
            agent_id=f"{role}@{self.team_name}",
            task_id=f"task-{role}",
            backend_type="in_process",
            team_name=self.team_name,
            mailbox_name=role,
        )
        self.worker_handles[role] = handle
        return handle

    async def send(self, worker: WorkerHandle | str, message: str) -> None:
        if isinstance(worker, WorkerHandle):
            self.send_calls.append((worker.role, message))
        else:
            self.send_calls.append((worker, message))

    async def read_mailbox(
        self,
        target: str,
        *,
        unread_only: bool = True,
        **_: object,
    ) -> list[WorkflowMessage]:
        self.mailbox_reads.append((target, unread_only))
        if unread_only:
            if self._mailbox_batches:
                return self._mailbox_batches.pop(0)
            return []
        return list(self._full_mailbox)


async def test_single_topology_runs_entry_role() -> None:
    runtime = FakeRuntime()
    spec = WorkflowSpec(
        name="single-demo",
        topology="single",
        entry_role="main",
        roles={"main": {"agent": "default"}},
    )
    result = await SingleTopology().run(spec, TaskDefinition(instruction="fix it"), runtime)
    assert result.entry_role == "main"
    assert result.final_text == "main:fix it"
    assert list(result.role_results) == ["main"]


async def test_fanout_join_topology_joins_inline_results() -> None:
    runtime = FakeRuntime()
    spec = WorkflowSpec(
        name="fanout-demo",
        topology="fanout_join",
        entry_role="joiner",
        roles={
            "implementer": {"agent": "default"},
            "reviewer": {"agent": "default"},
            "joiner": {"agent": "default"},
        },
        topology_config={"fanout_roles": ["implementer", "reviewer"], "join_role": "joiner"},
    )
    result = await FanoutJoinTopology().run(spec, TaskDefinition(instruction="fix it"), runtime)
    assert result.final_text == "implementer=implementer:fix it | reviewer=reviewer:fix it"
    assert sorted(result.role_results) == ["implementer", "joiner", "reviewer"]


async def test_coordinator_worker_topology_spawns_workers_then_runs_coordinator() -> None:
    runtime = FakeRuntime()
    spec = WorkflowSpec(
        name="coord-demo",
        topology="coordinator_worker",
        entry_role="coordinator",
        roles={
            "coordinator": {"agent": "default"},
            "implementer": {"agent": "default", "mode": "spawned"},
            "verifier": {"agent": "default", "mode": "spawned"},
        },
        topology_config={"worker_roles": ["implementer", "verifier"]},
    )
    result = await CoordinatorWorkerTopology().run(
        spec,
        TaskDefinition(instruction="fix it"),
        runtime,
    )

    assert [role for role, _ in runtime.spawn_calls] == ["implementer", "verifier"]
    assert len(runtime.send_calls) == 0
    assert runtime.mailbox_reads == [
        ("leader", False),
    ]
    coordinator_task = runtime.inline_calls[0][1]
    assert result.final_text == "implementer@team-demo,verifier@team-demo"
    assert sorted(result.workers) == ["implementer", "verifier"]
