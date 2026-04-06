"""ReAct (Reasoning + Acting) agent architecture.

Alternates between structured *reasoning* turns (no tools) and *action*
turns (tools enabled):

1. **Think** — LLM produces a structured ``Thought`` (reasoning + action
   to take, or a final answer).
2. **Act** — a separate agent config executes the planned action with tools.
3. **Observe** — the action result is fed back into the next Think step.

Demonstrates:
- Structured output for reasoning (``output_type=Thought``)
- Alternating tool / no-tool modes within one architecture
- Accumulated observation history
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from openharness.agents.config import AgentConfig
from openharness.agents.contracts import AgentRunResult, TaskDefinition
from openharness.runtime.session import AgentRuntime

log = logging.getLogger(__name__)


class Thought(BaseModel):
    """Structured output from the reasoning step."""

    reasoning: str
    action: str = ""
    is_finished: bool = False
    final_answer: str = ""


class ReActAgent:
    """ReAct: explicit Think → Act → Observe loop.

    Requires two subagents in the config:
    - ``thinker``: no tools, produces structured ``Thought``
    - ``actor``: has tools, executes the planned action
    """

    def __init__(self, config: AgentConfig, **_rest: Any) -> None:
        self._config = config
        if "thinker" not in config.subagents:
            raise ValueError(f"ReActAgent config '{config.name}' must define a 'thinker' subagent.")
        if "actor" not in config.subagents:
            raise ValueError(f"ReActAgent config '{config.name}' must define an 'actor' subagent.")
        self._thinker_config = config.subagents["thinker"]
        self._actor_config = config.subagents["actor"]
        self._max_steps = max(1, config.max_turns)

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
                "max_steps": self._max_steps,
            },
        ) as agent_span:
            observations: list[dict[str, str]] = []

            for step in range(1, self._max_steps + 1):
                with trace_observer.span(
                    name=f"step:{step}",
                    input={"instruction": task.instruction},
                    metadata={"agent": self._config.name},
                ) as step_span:
                    log.info("ReAct step %d/%d — thinking", step, self._max_steps)
                    thought: Thought = await runtime.run_agent_config(
                        self._thinker_config,
                        TaskDefinition(
                            instruction=task.instruction,
                            payload={**task.payload, "observations": observations, "step": step},
                        ),
                        output_type=Thought,
                    )

                    if thought.is_finished:
                        log.info("ReAct finished at step %d", step)
                        result = runtime.build_result(thought.final_answer)
                        step_span.update(
                            output={"is_finished": True, "final_answer": thought.final_answer}
                        )
                        agent_span.update(output={"final_text": result.final_text})
                        return result

                    log.info("ReAct step %d — acting: %s", step, thought.action)
                    action_result = await runtime.run_agent_config(
                        self._actor_config,
                        TaskDefinition(
                            instruction=thought.action,
                            payload=task.payload,
                        ),
                    )

                    observations.append({
                        "step": str(step),
                        "reasoning": thought.reasoning,
                        "action": thought.action,
                        "observation": action_result,
                    })
                    step_span.update(
                        output={
                            "reasoning": thought.reasoning,
                            "action": thought.action,
                            "observation": action_result,
                        }
                    )

            last_obs = observations[-1]["observation"] if observations else ""
            result = runtime.build_result(last_obs)
            agent_span.update(output={"final_text": result.final_text})
            return result
