"""Planner-Executor agent architecture.

Composes two ``Agent`` instances: a *planner* that produces a structured
plan, and an *executor* that carries it out using tools.  Because both
are opaque ``Agent`` objects, either can itself be a composite.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from openharness.agents.config import AgentConfig
from openharness.agents.contracts import Agent, AgentRunResult, TaskDefinition
from openharness.runtime.session import AgentRuntime

log = logging.getLogger(__name__)


class Plan(BaseModel):
    """Structured plan produced by the planner agent."""

    reasoning: str
    steps: list[str]


class PlannerExecutorAgent:
    """Compose a planner and an executor agent sequentially.

    The planner produces a structured ``Plan`` (via ``output_type``).
    The executor receives the plan steps and executes them with tools.
    """

    def __init__(self, config: AgentConfig, *, planner: Agent, executor: Agent, **_rest: Any) -> None:
        self._config = config
        self._planner = planner
        self._executor = executor

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
            metadata={"architecture": self._config.architecture},
        ) as agent_span:
            log.info("Running planner...")
            with trace_observer.span(
                name="planner",
                input={"instruction": task.instruction},
                metadata={"agent": self._config.subagents["planner"].name},
            ) as planner_span:
                plan_result = await self._planner.run(task, runtime)
                planner_span.update(output=plan_result.output)

            log.info("Running executor with plan...")
            executor_task = TaskDefinition(
                instruction=task.instruction,
                payload={**task.payload, "plan": plan_result.output},
            )
            with trace_observer.span(
                name="executor",
                input={
                    "instruction": task.instruction,
                    "plan": plan_result.output,
                },
                metadata={"agent": self._config.subagents["executor"].name},
            ) as executor_span:
                result = await self._executor.run(executor_task, runtime)
                executor_span.update(output={"final_text": result.final_text})

            agent_span.update(output={"final_text": result.final_text})
            return result
