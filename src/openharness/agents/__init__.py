"""Agent definitions, configuration, and factory."""

from __future__ import annotations

from typing import Any

from openharness.agents.architectures import (
    PlannerExecutorAgent,
    ReActAgent,
    ReflectionAgent,
    SimpleAgent,
)
from openharness.agents.catalog import (
    CatalogAgentConfig,
    get_catalog_agent_config,
    get_catalog_agent_configs,
    iter_catalog_agent_configs,
)
from openharness.agents.config import AgentConfig, AgentDefinitionMetadata
from openharness.agents.contracts import (
    Agent,
    AgentRunResult,
    AgentWorkspace,
    CommandResult,
    TaskDefinition,
)
from openharness.agents.factory import AgentFactory

try:
    from openharness.engine.conversation import Conversation
except ImportError:  # pragma: no cover - compatibility fallback
    Conversation = Any  # type: ignore[misc,assignment]

try:
    from openharness.engine.query import TurnResult
except ImportError:  # pragma: no cover - compatibility fallback
    TurnResult = Any  # type: ignore[misc,assignment]

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentDefinitionMetadata",
    "AgentFactory",
    "AgentRunResult",
    "AgentWorkspace",
    "CatalogAgentConfig",
    "CommandResult",
    "Conversation",
    "PlannerExecutorAgent",
    "ReActAgent",
    "ReflectionAgent",
    "SimpleAgent",
    "TaskDefinition",
    "TurnResult",
    "get_catalog_agent_config",
    "get_catalog_agent_configs",
    "iter_catalog_agent_configs",
]
