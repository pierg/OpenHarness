"""CRUD over `lab/components.md` — the catalog of building-block atoms.

Each entry is one ingredient that can be combined into an agent config:

- ``Architecture``: top-level harness shape (single-loop, planner-executor,
  react-loop, reflection-loop, …).
- ``Runtime``: cross-cutting runtime mechanisms (loop-guard,
  context-compaction, …).
- ``Tools``: tool bundles available to a (sub)agent.
- ``Prompt``: notable prompt strategies that we want to track separately
  from the architecture they live in.
- ``Model``: the underlying LLM SKU.

The status lattice is ``proposed → experimental → validated``
(forward-only via auto bumps from decision application), with two
terminal states ``rejected`` and ``superseded`` reachable via explicit
human / verdict gestures.

Markdown is the source of truth. Helpers here are read-then-write
idempotent: re-applying the same upsert produces the same bytes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from openharness.lab.lab_docs import (
    LabDocError,
    _parse_md_table,
    _read,
    _split_top_sections,
    _strip_md_link,
    _write,
)
from openharness.lab.paths import LAB_ROOT

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

CATALOG_KINDS: tuple[str, ...] = (
    "Architecture",
    "Runtime",
    "Tools",
    "Prompt",
    "Model",
)

STATUS_ORDER: tuple[str, ...] = (
    "proposed",
    "experimental",
    "validated",
)
TERMINAL_STATUSES: frozenset[str] = frozenset({"rejected", "superseded"})
ALL_STATUSES: frozenset[str] = frozenset(STATUS_ORDER) | TERMINAL_STATUSES


@dataclass(slots=True)
class ComponentEntry:
    component_id: str
    kind: str
    status: str
    description: str
    used_by: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ComponentsCatalog:
    by_kind: dict[str, list[ComponentEntry]]

    def find(self, component_id: str) -> ComponentEntry | None:
        for entries in self.by_kind.values():
            for e in entries:
                if e.component_id == component_id:
                    return e
        return None


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _components_path(lab_root: Path) -> Path:
    return lab_root / "components.md"


def read_catalog(*, lab_root: Path = LAB_ROOT) -> ComponentsCatalog:
    """Parse `lab/components.md` into a ComponentsCatalog."""
    path = _components_path(lab_root)
    text = _read(path)
    parts = dict(_split_top_sections(text, level=2))
    by_kind: dict[str, list[ComponentEntry]] = {k: [] for k in CATALOG_KINDS}
    for kind in CATALOG_KINDS:
        for row in _parse_md_table(parts.get(kind, "")):
            if len(row) < 3:
                continue
            cid = _strip_md_link(row[0])
            status = (row[1] or "").strip().lower() or "proposed"
            description = (row[2] or "").strip()
            used_by = _split_csv(row[3]) if len(row) > 3 else []
            evidence = _split_evidence(row[4]) if len(row) > 4 else []
            by_kind[kind].append(
                ComponentEntry(
                    component_id=cid,
                    kind=kind,
                    status=status,
                    description=description,
                    used_by=used_by,
                    evidence=evidence,
                )
            )
    return ComponentsCatalog(by_kind=by_kind)


def write_catalog(catalog: ComponentsCatalog, *, lab_root: Path = LAB_ROOT) -> str:
    """Re-render the whole catalog to `lab/components.md`."""
    path = _components_path(lab_root)
    text = render_catalog(catalog)
    _write(path, text)
    return text


def render_catalog(catalog: ComponentsCatalog) -> str:
    sections = ["# Components"]
    for kind in CATALOG_KINDS:
        sections.append(f"## {kind}")
        sections.append(_render_kind_table(catalog.by_kind.get(kind, [])))
    return "\n\n".join(sections).rstrip() + "\n"


def _render_kind_table(entries: list[ComponentEntry]) -> str:
    if not entries:
        return "_(none)_"
    lines = [
        "| ID | Status | Description | Used by | Evidence |",
        "|----|--------|-------------|---------|----------|",
    ]
    for e in entries:
        used = ", ".join(f"`{u}`" for u in e.used_by) if e.used_by else "—"
        ev = ", ".join(e.evidence) if e.evidence else "—"
        lines.append(f"| `{e.component_id}` | {e.status} | {e.description} | {used} | {ev} |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def upsert(
    *,
    component_id: str,
    kind: str,
    description: str | None = None,
    status: str | None = None,
    used_by: list[str] | None = None,
    evidence: list[str] | None = None,
    lab_root: Path = LAB_ROOT,
) -> ComponentEntry:
    """Insert or update a component entry. Bumps status forward only.

    Status transitions:
      - ``proposed`` → ``experimental`` → ``validated`` (forward only).
      - Terminal states (``rejected`` / ``superseded``) override anything,
        but a terminal entry will not be auto-bumped back to a non-terminal
        state — that requires a manual ``set-status``.
    """
    if kind not in CATALOG_KINDS:
        raise LabDocError(f"Unknown component kind {kind!r}; expected one of {CATALOG_KINDS}")
    if status is not None and status not in ALL_STATUSES:
        raise LabDocError(f"Unknown status {status!r}; expected one of {sorted(ALL_STATUSES)}")
    catalog = read_catalog(lab_root=lab_root)
    existing = catalog.find(component_id)
    if existing is None:
        entry = ComponentEntry(
            component_id=component_id,
            kind=kind,
            status=status or "proposed",
            description=description or "",
            used_by=list(used_by or []),
            evidence=list(evidence or []),
        )
        catalog.by_kind.setdefault(kind, []).append(entry)
    else:
        if existing.kind != kind:
            raise LabDocError(
                f"Component {component_id!r} already exists under kind "
                f"{existing.kind!r}; refusing to move it to {kind!r}."
            )
        if description is not None:
            existing.description = description
        if status is not None:
            existing.status = _bumped(existing.status, status)
        if used_by is not None:
            for u in used_by:
                if u not in existing.used_by:
                    existing.used_by.append(u)
        if evidence is not None:
            for ev in evidence:
                if ev not in existing.evidence:
                    existing.evidence.append(ev)
        entry = existing
    write_catalog(catalog, lab_root=lab_root)
    return entry


def bump_status(
    *,
    component_id: str,
    target: str,
    evidence: str | None = None,
    lab_root: Path = LAB_ROOT,
) -> ComponentEntry:
    """Forward-only status bump; raises if the component is unknown."""
    catalog = read_catalog(lab_root=lab_root)
    entry = catalog.find(component_id)
    if entry is None:
        raise LabDocError(f"Component {component_id!r} not found in lab/components.md.")
    entry.status = _bumped(entry.status, target)
    if evidence and evidence not in entry.evidence:
        entry.evidence.append(evidence)
    write_catalog(catalog, lab_root=lab_root)
    return entry


def set_status(
    *,
    component_id: str,
    status: str,
    evidence: str | None = None,
    lab_root: Path = LAB_ROOT,
) -> ComponentEntry:
    """Unconditional status set (humans only — bypasses the bump lattice)."""
    if status not in ALL_STATUSES:
        raise LabDocError(f"Unknown status {status!r}; expected one of {sorted(ALL_STATUSES)}")
    catalog = read_catalog(lab_root=lab_root)
    entry = catalog.find(component_id)
    if entry is None:
        raise LabDocError(f"Component {component_id!r} not found in lab/components.md.")
    entry.status = status
    if evidence and evidence not in entry.evidence:
        entry.evidence.append(evidence)
    write_catalog(catalog, lab_root=lab_root)
    return entry


def add_used_by(
    *,
    component_id: str,
    agent_ids: list[str],
    lab_root: Path = LAB_ROOT,
) -> ComponentEntry:
    """Append agent ids to the Used-by column (deduped)."""
    catalog = read_catalog(lab_root=lab_root)
    entry = catalog.find(component_id)
    if entry is None:
        raise LabDocError(f"Component {component_id!r} not found in lab/components.md.")
    for a in agent_ids:
        if a not in entry.used_by:
            entry.used_by.append(a)
    write_catalog(catalog, lab_root=lab_root)
    return entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bumped(current: str, target: str) -> str:
    """Forward-only bump. Terminal states are sticky; explicit demotions
    require ``set_status``."""
    if current in TERMINAL_STATUSES:
        return current
    if target in TERMINAL_STATUSES:
        return target
    cur_idx = STATUS_ORDER.index(current) if current in STATUS_ORDER else -1
    tgt_idx = STATUS_ORDER.index(target) if target in STATUS_ORDER else -1
    return target if tgt_idx > cur_idx else current


_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _split_csv(cell: str) -> list[str]:
    cell = (cell or "").strip()
    if not cell or cell == "—":
        return []
    out: list[str] = []
    for p in cell.split(","):
        p = _strip_md_link(p.strip())
        # strip stray backticks the renderer would re-add anyway
        p = p.replace("`", "").strip()
        if p:
            out.append(p)
    return out


def _split_evidence(cell: str) -> list[str]:
    cell = (cell or "").strip()
    if not cell or cell == "—":
        return []
    return [p.strip() for p in cell.split(",") if p.strip()]
