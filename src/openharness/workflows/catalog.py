"""Shared YAML workflow catalog loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openharness.config.paths import get_config_dir, get_project_config_dir
from openharness.workflows.specs import WorkflowSpec

CatalogSource = Literal["builtin", "user", "project"]


@dataclass(frozen=True)
class CatalogWorkflowSpec:
    """One workflow spec plus its catalog provenance."""

    spec: WorkflowSpec
    source: CatalogSource
    path: Path


def get_builtin_workflow_configs_dir() -> Path:
    """Return the built-in workflow config directory."""
    return Path(__file__).resolve().parent / "configs"


def get_user_workflow_configs_dir() -> Path:
    """Return the user-level workflow config directory."""
    return get_config_dir() / "workflow_configs"


def get_project_workflow_configs_dir(cwd: str | Path) -> Path:
    """Return the project-level workflow config directory."""
    return get_project_config_dir(cwd) / "workflow_configs"


def load_workflow_configs_dir(
    directory: str | Path,
    *,
    source: CatalogSource,
) -> list[CatalogWorkflowSpec]:
    """Load all workflow specs from a directory."""
    root = Path(directory)
    if not root.is_dir():
        return []

    loaded: list[CatalogWorkflowSpec] = []
    for ext in ("*.yaml", "*.yml"):
        for path in sorted(root.glob(ext)):
            spec = WorkflowSpec.from_yaml(path)
            loaded.append(CatalogWorkflowSpec(spec=spec, source=source, path=path))
    return loaded


def iter_catalog_workflow_specs(cwd: str | Path | None = None) -> list[CatalogWorkflowSpec]:
    """Return the merged workflow catalog in ascending override order."""
    items: list[CatalogWorkflowSpec] = []
    items.extend(load_workflow_configs_dir(get_builtin_workflow_configs_dir(), source="builtin"))
    items.extend(load_workflow_configs_dir(get_user_workflow_configs_dir(), source="user"))
    if cwd is not None:
        items.extend(
            load_workflow_configs_dir(
                get_project_workflow_configs_dir(cwd),
                source="project",
            )
        )
    return items


def get_catalog_workflow_specs(cwd: str | Path | None = None) -> dict[str, CatalogWorkflowSpec]:
    """Return the merged workflow catalog keyed by workflow name."""
    catalog: dict[str, CatalogWorkflowSpec] = {}
    for item in iter_catalog_workflow_specs(cwd):
        catalog[item.spec.name] = item
    return catalog


def get_catalog_workflow_spec(
    name: str,
    cwd: str | Path | None = None,
) -> CatalogWorkflowSpec | None:
    """Return one workflow spec by name from the merged catalog."""
    return get_catalog_workflow_specs(cwd).get(name)

