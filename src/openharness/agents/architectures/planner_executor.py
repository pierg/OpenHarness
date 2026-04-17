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
from openharness.observability import trace_agent_run
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

    def __init__(
        self, config: AgentConfig, *, planner: Agent, executor: Agent, **_rest: Any
    ) -> None:
        self._config = config
        self._planner = planner
        self._executor = executor

    @property
    def config(self) -> AgentConfig:
        return self._config

    @trace_agent_run
    async def run(self, task: TaskDefinition, runtime: AgentRuntime) -> AgentRunResult:
        log.info("Running planner...")
        plan_result = await self._planner.run(task, runtime)

        log.info("Running executor with plan...")
        executor_task = TaskDefinition(
            instruction=task.instruction,
            payload={**task.payload, "plan": plan_result.output},
        )
        return await self._executor.run(executor_task, runtime)
