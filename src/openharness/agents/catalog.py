"""Shared YAML agent catalog loading for workflows and coordinator projection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openharness.agents.config import AgentConfig
from openharness.config.paths import get_config_dir, get_project_config_dir

CatalogSource = Literal["builtin", "user", "project"]


@dataclass(frozen=True)
class CatalogAgentConfig:
    """One YAML agent config plus its catalog provenance."""

    config: AgentConfig
    source: CatalogSource
    path: Path


def get_builtin_agent_configs_dir() -> Path:
    """Return the built-in YAML agent config directory."""
    return Path(__file__).resolve().parent / "configs"


def get_user_agent_configs_dir() -> Path:
    """Return the user-level YAML agent config directory."""
    return get_config_dir() / "agent_configs"


def get_project_agent_configs_dir(cwd: str | Path) -> Path:
    """Return the project-level YAML agent config directory.

    Read-only lookup: does not create ``.openharness/`` on disk.
    """
    return get_project_config_dir(cwd, create=False) / "agent_configs"


def load_agent_configs_dir(
    directory: str | Path,
    *,
    source: CatalogSource,
) -> list[CatalogAgentConfig]:
    """Load all YAML agent configs from *directory*."""
    root = Path(directory)
    if not root.is_dir():
        return []

    loaded: list[CatalogAgentConfig] = []
    for ext in ("*.yaml", "*.yml"):
        for path in sorted(root.glob(ext)):
            config = AgentConfig.from_yaml(path)
            loaded.append(CatalogAgentConfig(config=config, source=source, path=path))
    return loaded


def iter_catalog_agent_configs(cwd: str | Path | None = None) -> list[CatalogAgentConfig]:
    """Return the merged YAML config catalog in ascending override order."""
    items: list[CatalogAgentConfig] = []
    items.extend(
        load_agent_configs_dir(
            get_builtin_agent_configs_dir(),
            source="builtin",
        )
    )
    items.extend(
        load_agent_configs_dir(
            get_user_agent_configs_dir(),
            source="user",
        )
    )
    if cwd is not None:
        items.extend(
            load_agent_configs_dir(
                get_project_agent_configs_dir(cwd),
                source="project",
            )
        )
    return items


def get_catalog_agent_configs(cwd: str | Path | None = None) -> dict[str, CatalogAgentConfig]:
    """Return the merged YAML config catalog keyed by config name."""
    catalog: dict[str, CatalogAgentConfig] = {}
    for item in iter_catalog_agent_configs(cwd):
        catalog[item.config.name] = item
    return catalog


def get_catalog_agent_config(
    name: str,
    cwd: str | Path | None = None,
) -> CatalogAgentConfig | None:
    """Return one YAML agent config by name from the merged catalog, or load from path if name is a file."""
    # If name looks like a path and exists, load it directly
    if name.endswith(".yaml") or name.endswith(".yml"):
        path = Path(name)
        if path.is_file():
            config = AgentConfig.from_yaml(path)
            return CatalogAgentConfig(config=config, source="project", path=path)

    return get_catalog_agent_configs(cwd).get(name)
