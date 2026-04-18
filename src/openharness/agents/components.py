"""Components registry — lightweight loader for `components: [...]`.

Each entry under `components/<id>.yaml` declares a reusable agent
building block (see ``components/README.md`` for the full schema).
When an agent YAML lists ``components: [<id>, ...]``,
``AgentConfig.from_mapping`` calls :func:`apply_components` to:

1. resolve each id against the registry (raising on unknowns),
2. enforce ``conflicts_with`` and ``applies_to`` rules,
3. merge each component's ``wires:`` payload into the raw mapping
   *before* pydantic validates it, so the resolved config still
   passes the same schema as a hand-written YAML.

The registry is intentionally minimal in this phase: only
``tools_add``, ``prompts_append``, and ``extras`` are honored.
``provides.runtime_flags`` is recorded into ``extras["_components"]``
for downstream consumption. Phase 3b will add a JSON Schema, a
pre-commit validator, and a misconfiguration check that runs on
ingest.
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REPO_ROOT: Path = Path(__file__).resolve().parents[3]
COMPONENTS_DIR: Path = REPO_ROOT / "components"

VALID_STATUSES = {"proposed", "active", "retired"}


class ComponentError(ValueError):
    """Any registry / resolution failure."""


@dataclass(slots=True)
class ComponentSpec:
    id: str
    description: str = ""
    status: str = "proposed"
    version: str = "0.0.1"
    applies_to_architectures: tuple[str, ...] = ()
    applies_to_agents: tuple[str, ...] = ()
    runtime_flags: dict[str, Any] = field(default_factory=dict)
    conflicts_with: tuple[str, ...] = ()
    cost: tuple[str, ...] = ()
    evidence: dict[str, list[str]] = field(default_factory=dict)
    tools_add: tuple[str, ...] = ()
    prompts_append: dict[str, str] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None

    @classmethod
    def from_yaml_file(cls, path: Path) -> ComponentSpec:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ComponentError(f"{path}: invalid YAML — {exc}") from exc
        if not isinstance(raw, dict):
            raise ComponentError(f"{path}: top-level must be a mapping")
        cid = raw.get("id")
        if not isinstance(cid, str) or not cid:
            raise ComponentError(f"{path}: missing string `id`")
        if cid != path.stem:
            raise ComponentError(
                f"{path}: id {cid!r} must match filename {path.stem!r}"
            )
        status = raw.get("status", "proposed")
        if status not in VALID_STATUSES:
            raise ComponentError(
                f"{path}: status {status!r} not in {sorted(VALID_STATUSES)}"
            )
        applies_to = raw.get("applies_to") or {}
        provides = raw.get("provides") or {}
        wires = raw.get("wires") or {}
        evidence_raw = raw.get("evidence") or {}
        evidence = {
            "ideas": list(evidence_raw.get("ideas") or []),
            "experiments": list(evidence_raw.get("experiments") or []),
        }
        return cls(
            id=cid,
            description=str(raw.get("description") or "").strip(),
            status=status,
            version=str(raw.get("version") or "0.0.1"),
            applies_to_architectures=tuple(applies_to.get("architectures") or ()),
            applies_to_agents=tuple(applies_to.get("agents") or ()),
            runtime_flags=dict(provides.get("runtime_flags") or {}),
            conflicts_with=tuple(raw.get("conflicts_with") or ()),
            cost=tuple(raw.get("cost") or ()),
            evidence=evidence,
            tools_add=tuple(wires.get("tools_add") or ()),
            prompts_append=dict(wires.get("prompts_append") or {}),
            extras=dict(wires.get("extras") or {}),
            source_path=path,
        )


# ----- registry ------------------------------------------------------------


@lru_cache(maxsize=1)
def load_registry() -> dict[str, ComponentSpec]:
    """Return ``{id: ComponentSpec}`` for every YAML in ``components/``.

    Cached for the process; call :func:`reset_registry_cache` after a
    file rename / new component during long-lived workers (the
    orchestrator and tests).
    """
    registry: dict[str, ComponentSpec] = {}
    if not COMPONENTS_DIR.is_dir():
        return registry
    for path in sorted(COMPONENTS_DIR.glob("*.yaml")):
        spec = ComponentSpec.from_yaml_file(path)
        registry[spec.id] = spec
    _validate_conflict_graph(registry)
    return registry


def reset_registry_cache() -> None:
    load_registry.cache_clear()


def _validate_conflict_graph(registry: dict[str, ComponentSpec]) -> None:
    """Symmetric check: A.conflicts_with(B) ⇒ B.conflicts_with(A)."""
    issues: list[str] = []
    for cid, spec in registry.items():
        for other in spec.conflicts_with:
            if other not in registry:
                issues.append(
                    f"{cid}: conflicts_with unknown component {other!r}"
                )
                continue
            if cid not in registry[other].conflicts_with:
                issues.append(
                    f"asymmetric conflict: {cid!r} ↔ {other!r} "
                    f"(declared on {cid}, missing on {other})"
                )
    if issues:
        raise ComponentError("; ".join(issues))


# ----- resolution at AgentConfig load -------------------------------------


def apply_components(
    raw: dict[str, Any],
    *,
    source_name: str,
    registry: dict[str, ComponentSpec] | None = None,
) -> dict[str, Any]:
    """Mutate-and-return ``raw`` with `components:` resolved.

    Called by :meth:`AgentConfig.from_mapping` *before* pydantic
    validation, so any merged tools/prompts pass the same schema as
    if they had been written into the YAML directly.
    """
    requested: list[str] = list(raw.get("components") or [])
    if not requested:
        return raw

    reg = registry if registry is not None else load_registry()
    architecture = raw.get("architecture", "simple")
    agent_name = raw.get("name") or source_name

    selected: list[ComponentSpec] = []
    seen: set[str] = set()
    for cid in requested:
        if cid in seen:
            continue
        seen.add(cid)
        if cid not in reg:
            raise ComponentError(
                f"agent {agent_name!r}: unknown component {cid!r}. "
                f"Known: {sorted(reg)}"
            )
        spec = reg[cid]
        if spec.applies_to_architectures and architecture not in spec.applies_to_architectures:
            raise ComponentError(
                f"agent {agent_name!r}: component {cid!r} does not apply to "
                f"architecture {architecture!r}; supports "
                f"{list(spec.applies_to_architectures)}"
            )
        if spec.applies_to_agents and agent_name not in spec.applies_to_agents:
            raise ComponentError(
                f"agent {agent_name!r}: component {cid!r} is restricted to "
                f"{list(spec.applies_to_agents)}"
            )
        selected.append(spec)

    chosen_ids = {s.id for s in selected}
    for spec in selected:
        clashing = chosen_ids & set(spec.conflicts_with)
        if clashing:
            raise ComponentError(
                f"agent {agent_name!r}: component {spec.id!r} conflicts with "
                f"{sorted(clashing)} which are also requested"
            )

    existing_tools: list[str] = list(raw.get("tools") or [])
    existing_prompts: dict[str, str] = dict(raw.get("prompts") or {})
    existing_extras: dict[str, Any] = dict(raw.get("extras") or {})

    component_meta: dict[str, dict[str, Any]] = {}
    for spec in selected:
        for tool in spec.tools_add:
            if tool not in existing_tools:
                existing_tools.append(tool)
        for pname, addition in spec.prompts_append.items():
            base = existing_prompts.get(pname, "")
            joiner = "\n\n" if base and not base.endswith("\n\n") else ""
            existing_prompts[pname] = f"{base}{joiner}{addition}"
        for k, v in spec.extras.items():
            existing_extras[k] = v
        component_meta[spec.id] = {
            "version": spec.version,
            "status": spec.status,
            "runtime_flags": spec.runtime_flags,
        }

    existing_extras.setdefault("_components", {}).update(component_meta)

    raw["tools"] = existing_tools
    if existing_prompts:
        raw["prompts"] = existing_prompts
    raw["extras"] = existing_extras
    return raw


# ----- CLI: `python -m openharness.agents.components --validate` ---------


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Components registry tools.")
    parser.add_argument("--validate", action="store_true",
                        help="Load every component and run conflict checks.")
    parser.add_argument("--list", action="store_true",
                        help="Print one line per registered component.")
    args = parser.parse_args(argv)

    try:
        registry = load_registry()
    except ComponentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.list:
        for cid, spec in sorted(registry.items()):
            print(f"{cid}\t{spec.status}\tv{spec.version}\t{spec.description.splitlines()[0] if spec.description else ''}")

    if args.validate:
        print(f"OK: validated {len(registry)} component(s).")

    if not args.validate and not args.list:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
