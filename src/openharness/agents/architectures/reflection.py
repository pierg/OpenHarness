"""Reflection agent architecture.

Runs a *worker* agent, then uses a *critic* config to produce a structured
verdict.  Iterates until the critic approves or max attempts are reached.

Demonstrates:
- Agent composition (worker is any ``Agent``)
- Structured output (critic returns ``Verdict``)
- Iterative refinement via ``TaskDefinition.payload`` feedback
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from openharness.agents.config import AgentConfig
from openharness.agents.contracts import Agent, AgentRunResult, TaskDefinition
from openharness.runtime.session import AgentRuntime

log = logging.getLogger(__name__)


class Verdict(BaseModel):
    """Structured verdict produced by the critic."""

    approved: bool
    feedback: str = ""
    issues: list[str] = Field(default_factory=list)


class ReflectionAgent:
    """Worker + critic with iterative refinement.

    The worker is a full ``Agent`` (any architecture).
    The critic is a subagent config that returns structured ``Verdict``.
    """

    def __init__(self, config: AgentConfig, *, worker: Agent, **_rest: Any) -> None:
        self._config = config
        self._worker = worker
        if "critic" not in config.subagents:
            raise ValueError(f"ReflectionAgent config '{config.name}' must define a 'critic' subagent.")
        self._critic_config = config.subagents["critic"]
        self._max_attempts = max(1, config.max_turns)

    @property
    def config(self) -> AgentConfig:
        return self._config

    async def run(self, task: TaskDefinition, runtime: AgentRuntime) -> AgentRunResult:
        trace_observer = runtime.trace_observer
        with trace_observer.span(
            name=f"agent:{self._config.name}",
            input={
                "instruction": task.instruction,
                "payload": task.payload,
            },
            metadata={
                "architecture": self._config.architecture,
                "max_attempts": self._max_attempts,
            },
        ) as agent_span:
            result: AgentRunResult | None = None

            for attempt in range(1, self._max_attempts + 1):
                with trace_observer.span(
                    name=f"attempt:{attempt}",
                    input={"instruction": task.instruction},
                    metadata={"agent": self._config.name},
                ) as attempt_span:
                    log.info("Reflection attempt %d/%d", attempt, self._max_attempts)
                    result = await self._worker.run(task, runtime)

                    verdict: Verdict = await runtime.run_agent_config(
                        self._critic_config,
                        TaskDefinition(
                            instruction=task.instruction,
                            payload={"solution": result.output, "attempt": attempt},
                        ),
                        output_type=Verdict,
                    )

                    attempt_span.update(
                        output={
                            "approved": verdict.approved,
                            "feedback": verdict.feedback,
                            "issues": verdict.issues,
                        }
                    )

                    if verdict.approved:
                        log.info("Critic approved on attempt %d", attempt)
                        agent_span.update(output={"final_text": result.final_text})
                        return result

                    log.info("Critic rejected: %s", verdict.feedback)
                    task = TaskDefinition(
                        instruction=task.instruction,
                        payload={
                            **task.payload,
                            "previous_attempt": result.output,
                            "feedback": verdict.feedback,
                            "issues": verdict.issues,
                        },
                    )

            assert result is not None
            agent_span.update(output={"final_text": result.final_text})
            return result
