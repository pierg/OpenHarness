"""Public run orchestration API."""

from openharness.runs.specs import (
    AgentSpec,
    HarborAgentRunSpec,
    HarborJobResult,
    InlineTaskSpec,
    LocalAgentRunSpec,
    RunLaunchResult,
    TrialResult,
)


def __getattr__(name: str):
    if name == "RunContext":
        from openharness.runs.context import RunContext

        return RunContext
    if name == "run_local_agent":
        from openharness.runs.local import run_local_agent

        return run_local_agent
    if name == "run_harbor_agent":
        from openharness.runs.harbor import run_harbor_agent

        return run_harbor_agent
    raise AttributeError(name)


__all__ = [
    "AgentSpec",
    "HarborAgentRunSpec",
    "HarborJobResult",
    "InlineTaskSpec",
    "LocalAgentRunSpec",
    "RunLaunchResult",
    "RunContext",
    "TrialResult",
    "run_harbor_agent",
    "run_local_agent",
]
