"""Simple single-agent architecture.

Delegates entirely to ``runtime.run_agent_config`` — the simplest
possible architecture and the leaf node for any composition tree.
"""

from __future__ import annotations

from typing import Any

from openharness.agents.config import AgentConfig
from openharness.agents.contracts import AgentRunResult, TaskDefinition
from openharness.runtime.session import AgentRuntime


class SimpleAgent:
    """Run a single agent config end-to-end."""

    def __init__(self, config: AgentConfig, **_subagents: Any) -> None:
        self._config = config

    @property
    def config(self) -> AgentConfig:
        return self._config

    async def run(self, task: TaskDefinition, runtime: AgentRuntime) -> AgentRunResult[str]:
        text = await runtime.run_agent_config(self._config, task)
        return runtime.build_result(text)
