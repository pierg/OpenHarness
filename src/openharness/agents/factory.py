"""Agent factory — load YAML definitions and create agent instances."""

from __future__ import annotations

import logging
from pathlib import Path

from openharness.agents.architectures import (
    PlannerExecutorAgent,
    ReActAgent,
    ReflectionAgent,
    SimpleAgent,
)
from openharness.agents.catalog import iter_catalog_agent_configs
from openharness.agents.config import AgentConfig
from openharness.agents.contracts import Agent

log = logging.getLogger(__name__)


_ARCHITECTURES: dict[str, type] = {
    "simple": SimpleAgent,
    "planner_executor": PlannerExecutorAgent,
    "reflection": ReflectionAgent,
    "react": ReActAgent,
}


class AgentFactory:
    """Registry of named agent configurations loaded from YAML files.

    Usage::

        factory = AgentFactory.with_default_configs()
        agent   = factory.create("basic")
    """

    def __init__(self, agents_dir: str | Path | None = None) -> None:
        self._configs: dict[str, AgentConfig] = {}
        if agents_dir is not None:
            self.load_dir(agents_dir)

    @classmethod
    def with_default_configs(cls) -> AgentFactory:
        """Return a factory pre-loaded with the framework's default agent configs."""
        default_dir = Path(__file__).resolve().parent / "configs"
        return cls(agents_dir=default_dir)

    @classmethod
    def with_catalog_configs(cls, cwd: str | Path | None = None) -> AgentFactory:
        """Return a factory loaded from the merged built-in/user/project catalog."""
        factory = cls()
        for item in iter_catalog_agent_configs(cwd):
            factory.register(item.config)
        return factory

    def load_dir(self, agents_dir: str | Path) -> None:
        """Discover and load all ``*.yaml`` / ``*.yml`` configs in *agents_dir*."""
        root = Path(agents_dir)
        if not root.is_dir():
            raise FileNotFoundError(f"Agents directory not found: {root}")
        for ext in ("*.yaml", "*.yml"):
            for path in sorted(root.glob(ext)):
                self.load_file(path)

    def load_file(self, path: str | Path) -> AgentConfig:
        """Load a single YAML config and register it by name."""
        config = AgentConfig.from_yaml(path)
        self._configs[config.name] = config
        log.debug("Loaded agent config %r from %s", config.name, path)
        return config

    def register(self, config: AgentConfig) -> None:
        """Register a programmatically-built config."""
        self._configs[config.name] = config

    def get_config(self, name: str) -> AgentConfig:
        """Return a registered config by name, or raise ``KeyError``."""
        return self._configs[name]

    def list_agents(self) -> list[str]:
        """Return registered agent names in sorted order."""
        return sorted(self._configs)

    @classmethod
    def register_architecture(cls, name: str, arch_class: type) -> None:
        """Register a custom architecture class for use in YAML configs."""
        _ARCHITECTURES[name] = arch_class

    def create(self, name: str) -> Agent:
        """Instantiate an agent from a registered config (recursively)."""
        config = self.get_config(name)
        return _build_agent(config)


def _build_agent(config: AgentConfig) -> Agent:
    """Recursively construct an agent tree from a config."""
    subagents = {
        sub_name: _build_agent(sub_config) for sub_name, sub_config in config.subagents.items()
    }

    arch_class = _ARCHITECTURES.get(config.architecture)
    if arch_class is None:
        raise ValueError(
            f"Unknown architecture '{config.architecture}' in config '{config.name}'. "
            f"Available: {sorted(_ARCHITECTURES)}"
        )

    return arch_class(config, **subagents)
