"""Agent definitions, configuration, and factory."""

from __future__ import annotations

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
from openharness.agents.config import AgentConfig, AgentDefinitionMetadata, QuickEvaluation
from openharness.agents.contracts import (
    Agent,
    AgentRunResult,
    AgentWorkspace,
    CommandResult,
    TaskDefinition,
)
from openharness.agents.factory import AgentFactory
from openharness.engine.conversation import Conversation
from openharness.engine.query import TurnResult

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
    "QuickEvaluation",
    "ReActAgent",
    "ReflectionAgent",
    "SimpleAgent",
    "TaskDefinition",
    "TurnResult",
    "get_catalog_agent_config",
    "get_catalog_agent_configs",
    "iter_catalog_agent_configs",
]
