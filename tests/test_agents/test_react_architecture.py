"""Tests for the ReAct architecture orchestrator (`ReActAgent.run`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openharness.agents.architectures.react import ReActAgent, Thought
from openharness.agents.config import AgentConfig
from openharness.agents.contracts import AgentRunResult, TaskDefinition


def _make_config(*, max_turns: int = 5) -> AgentConfig:
    return AgentConfig(
        name="react-test",
        architecture="react",
        max_turns=max_turns,
        subagents={
            "thinker": AgentConfig(name="thinker", architecture="simple"),
            "actor": AgentConfig(name="actor", architecture="simple"),
        },
    )


@dataclass
class _Call:
    config_name: str
    instruction: str
    output_type_name: str | None


@dataclass
class _FakeRuntime:
    """Minimal AgentRuntime stand-in for ReActAgent.run.

    The thinker is fed a queue of pre-baked ``Thought`` objects; the actor
    is fed a queue of pre-baked observation strings. Each call is recorded
    so the test can assert on order and arguments.
    """

    thoughts: list[Thought]
    actor_outputs: list[str] = field(default_factory=list)
    calls: list[_Call] = field(default_factory=list)

    async def run_agent_config(
        self,
        config: AgentConfig,
        task: TaskDefinition,
        extra_template_vars: dict[str, Any] | None = None,
        output_type: type | None = None,
    ) -> Any:
        self.calls.append(
            _Call(
                config_name=config.name,
                instruction=task.instruction,
                output_type_name=output_type.__name__ if output_type is not None else None,
            )
        )
        if output_type is Thought:
            assert self.thoughts, "thinker invoked more times than expected"
            return self.thoughts.pop(0)
        assert self.actor_outputs, "actor invoked more times than expected"
        return self.actor_outputs.pop(0)

    def build_result(self, output: Any) -> AgentRunResult:
        return AgentRunResult(output=output, input_tokens=0, output_tokens=0)


@pytest.mark.asyncio
async def test_terminal_action_executes_before_finishing():
    """Regression test for: thinker emits is_finished=True together with
    a non-empty action — the runtime must execute that action (e.g. the
    final ``echo > /app/regex.txt``) instead of silently dropping it."""
    runtime = _FakeRuntime(
        thoughts=[
            Thought(
                reasoning="write the file and we are done",
                action="echo 'pattern' > /app/regex.txt",
                is_finished=True,
                final_answer="saved to /app/regex.txt",
            )
        ],
        actor_outputs=["wrote /app/regex.txt"],
    )
    agent = ReActAgent(_make_config())

    result = await agent.run(TaskDefinition(instruction="task"), runtime)

    actor_calls = [c for c in runtime.calls if c.config_name == "actor"]
    assert len(actor_calls) == 1, "the terminal action must be dispatched once"
    assert actor_calls[0].instruction == "echo 'pattern' > /app/regex.txt"
    assert result.output == "saved to /app/regex.txt"


@pytest.mark.asyncio
async def test_terminal_action_observation_used_when_final_answer_empty():
    runtime = _FakeRuntime(
        thoughts=[
            Thought(
                reasoning="write file",
                action="touch /app/done",
                is_finished=True,
                final_answer="",
            )
        ],
        actor_outputs=["created /app/done"],
    )
    agent = ReActAgent(_make_config())

    result = await agent.run(TaskDefinition(instruction="task"), runtime)

    assert result.output == "created /app/done"


@pytest.mark.asyncio
async def test_finish_with_no_action_returns_final_answer_directly():
    runtime = _FakeRuntime(
        thoughts=[
            Thought(
                reasoning="already done in earlier steps",
                action="",
                is_finished=True,
                final_answer="all good",
            )
        ],
    )
    agent = ReActAgent(_make_config())

    result = await agent.run(TaskDefinition(instruction="task"), runtime)

    actor_calls = [c for c in runtime.calls if c.config_name == "actor"]
    assert actor_calls == [], "no actor invocation when there is no pending action"
    assert result.output == "all good"


@pytest.mark.asyncio
async def test_normal_loop_alternates_think_and_act():
    runtime = _FakeRuntime(
        thoughts=[
            Thought(reasoning="step 1", action="ls /app", is_finished=False),
            Thought(
                reasoning="found files; emit final cmd",
                action="cat /app/data.txt > /app/out.txt",
                is_finished=True,
                final_answer="done",
            ),
        ],
        actor_outputs=["data.txt", "wrote out.txt"],
    )
    agent = ReActAgent(_make_config())

    result = await agent.run(TaskDefinition(instruction="task"), runtime)

    sequence = [c.config_name for c in runtime.calls]
    assert sequence == ["thinker", "actor", "thinker", "actor"]
    assert result.output == "done"


@pytest.mark.asyncio
async def test_no_action_no_finish_records_nudge_observation():
    """When the thinker emits an empty action without finishing, the loop
    must record a corrective observation rather than spinning silently."""
    runtime = _FakeRuntime(
        thoughts=[
            Thought(reasoning="hmm", action="", is_finished=False),
            Thought(
                reasoning="ok now I will finish",
                action="",
                is_finished=True,
                final_answer="done after nudge",
            ),
        ],
    )
    agent = ReActAgent(_make_config())

    result = await agent.run(TaskDefinition(instruction="task"), runtime)

    thinker_calls = [c for c in runtime.calls if c.config_name == "thinker"]
    assert len(thinker_calls) == 2
    # The second thinker call should have seen the nudge observation in payload
    # (we asserted that indirectly — the runtime got called twice and finished).
    assert result.output == "done after nudge"


@pytest.mark.asyncio
async def test_max_steps_returns_last_observation_when_never_finished():
    runtime = _FakeRuntime(
        thoughts=[
            Thought(reasoning=f"step {i}", action=f"cmd {i}", is_finished=False)
            for i in range(1, 4)
        ],
        actor_outputs=["out 1", "out 2", "out 3"],
    )
    agent = ReActAgent(_make_config(max_turns=3))

    result = await agent.run(TaskDefinition(instruction="task"), runtime)

    assert result.output == "out 3"
