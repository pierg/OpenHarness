"""Apply-side of the tree: TreeDiff → markdown + DB cache + (maybe) trunk swap.

`tree_ops.evaluate(instance_id)` produces a `TreeDiff`. This module
turns that diff into actual edits:

- AddBranch / Reject / NoOp: auto-applied. We update
  `lab/configs.md` (Branches / Rejected sections), bump the status
  of any uniquely-introduced atoms in `lab/components.md`, write
  the `### Tree effect` block into the `lab/experiments.md` journal
  entry, and mirror the diff into the `tree_diffs` cache table.
- Graduate: STAGED, not applied. We write the `### Tree effect`
  block as a "PROPOSED" badge, mark `tree_diffs.applied = false`,
  and require `uv run lab graduate confirm <slug>` (a human gesture)
  before swapping `trunk.yaml` and writing a `trunk_changes` row.

Idempotent at the markdown layer (re-applying a diff with the same
slug rewrites the section in place), and at the DB layer
(`upsert_tree_diff`).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from openharness.lab import components_doc as cdoc
from openharness.lab import db as labdb
from openharness.lab import lab_docs
from openharness.lab.paths import REPO_ROOT
from openharness.lab.tree_ops import (
    TreeDiff,
    insert_trunk_change,
    upsert_tree_diff,
)

_AGENT_CONFIGS_DIR = REPO_ROOT / "src" / "openharness" / "agents" / "configs"
_TRUNK_YAML = _AGENT_CONFIGS_DIR / "trunk.yaml"


@dataclass(slots=True)
class ApplyResult:
    slug: str
    diff: TreeDiff
    applied: bool
    applied_by: str
    journal_block_written: bool
    notes: list[str]


def apply_diff(
    *,
    slug: str,
    diff: TreeDiff,
    applied_by: str = "auto:daemon",
    today: date | None = None,
    lab_root: Path | None = None,
) -> ApplyResult:
    """Apply a TreeDiff to the on-disk lab + DB cache.

    `graduate` diffs are *staged*, not applied: the caller (or the
    human via `lab graduate confirm <slug>`) is responsible for the
    trunk swap. Everything else is auto-applied.
    """
    today = today or date.today()
    notes: list[str] = []
    is_graduate = diff.kind == "graduate"
    auto_apply = not is_graduate
    lr = {"lab_root": lab_root} if lab_root is not None else {}

    if diff.kind == "add_branch":
        body_use_when = _format_use_when(diff.use_when)
        lab_docs.add_branch(
            branch_id=diff.target_id,
            mutation=diff.rationale.split(";")[0][:120].strip() or "(see journal)",
            use_when=body_use_when,
            last_verified=today.isoformat(),
            **lr,
        )
        notes.append(
            f"appended branch `{diff.target_id}` to configs.md > ## Branches"
        )
    elif diff.kind == "reject":
        evidence = ", ".join(str(p) for p in diff.evidence_paths[:2]) or "(see journal)"
        lab_docs.add_rejected(
            branch_id=diff.target_id,
            reason=diff.rationale[:200],
            evidence=evidence,
            **lr,
        )
        notes.append(
            f"appended `{diff.target_id}` to configs.md > ## Rejected"
        )
    elif diff.kind == "no_op":
        notes.append("no_op: no tree mutation; recorded in journal only")
    elif diff.kind == "graduate":
        notes.append(
            f"STAGED graduate of `{diff.target_id}` "
            f"(run `uv run lab graduate confirm {slug}` to apply)"
        )

    block = render_tree_effect_block(diff, slug=slug, applied=auto_apply)
    try:
        lab_docs.set_section(slug=slug, section="Tree effect", body=block, **lr)
        journal_written = True
    except lab_docs.LabDocError as exc:
        notes.append(f"journal write skipped: {exc}")
        journal_written = False

    bump_notes = _bump_components_for_diff(diff, lab_root=lab_root)
    notes.extend(bump_notes)

    applied_at = datetime.now(timezone.utc) if auto_apply else None
    try:
        with labdb.writer() as conn:
            upsert_tree_diff(
                conn,
                slug=slug,
                diff=diff,
                applied=auto_apply,
                applied_by=applied_by if auto_apply else "proposed",
                applied_at=applied_at,
            )
    except Exception as exc:
        notes.append(f"DB cache update skipped: {exc}")

    return ApplyResult(
        slug=slug,
        diff=diff,
        applied=auto_apply,
        applied_by=applied_by if auto_apply else "proposed",
        journal_block_written=journal_written,
        notes=notes,
    )


def confirm_graduate(
    *,
    slug: str,
    diff: TreeDiff,
    applied_by: str,
    reason: str | None = None,
    today: date | None = None,
) -> ApplyResult:
    """Promote a staged Graduate diff: swap trunk.yaml + audit + tree update.

    Effects:
      1. `trunk.yaml` becomes a copy of `<target_id>.yaml`.
      2. The previous trunk id (if any) is moved to `## Branches`
         with `use_when: legacy trunk anchor` (preserves history;
         a human can re-categorise to ## Rejected later if needed).
      3. The `### Tree effect` block is rewritten with the APPLIED
         badge and the timestamp.
      4. `trunk_changes` gets a new row.
      5. `tree_diffs.applied` flips to true.
    """
    if diff.kind != "graduate":
        raise ValueError(f"confirm_graduate called with kind={diff.kind!r}")
    today = today or date.today()
    notes: list[str] = []

    target_yaml = _AGENT_CONFIGS_DIR / f"{diff.target_id}.yaml"
    if not target_yaml.is_file():
        raise FileNotFoundError(
            f"Cannot graduate `{diff.target_id}`: {target_yaml} not found."
        )

    prev_trunk_id: str | None = None
    if _TRUNK_YAML.is_file():
        try:
            import yaml
            data = yaml.safe_load(_TRUNK_YAML.read_text()) or {}
            n = data.get("name")
            if isinstance(n, str):
                prev_trunk_id = n
        except Exception:
            prev_trunk_id = None

    shutil.copyfile(target_yaml, _TRUNK_YAML)
    notes.append(f"trunk.yaml ← copy of {target_yaml.name}")

    journal_link = f"[`{slug}`](experiments.md#{slug})"
    lab_docs.set_trunk(
        trunk_id=diff.target_id,
        reason=reason or diff.rationale[:200],
        journal_link=journal_link,
    )
    notes.append(f"configs.md > ## Trunk now points at `{diff.target_id}`")

    if prev_trunk_id and prev_trunk_id != diff.target_id:
        try:
            lab_docs.add_branch(
                branch_id=prev_trunk_id,
                mutation="(former trunk; preserved for history)",
                use_when="legacy trunk anchor",
                last_verified=today.isoformat(),
            )
            notes.append(f"former trunk `{prev_trunk_id}` archived as a branch")
        except lab_docs.LabDocError as exc:
            notes.append(f"could not archive former trunk: {exc}")

    block = render_tree_effect_block(diff, slug=slug, applied=True)
    try:
        lab_docs.set_section(slug=slug, section="Tree effect", body=block)
    except lab_docs.LabDocError as exc:
        notes.append(f"journal rewrite skipped: {exc}")

    applied_at = datetime.now(timezone.utc)
    try:
        with labdb.writer() as conn:
            upsert_tree_diff(
                conn,
                slug=slug,
                diff=diff,
                applied=True,
                applied_by=applied_by,
                applied_at=applied_at,
            )
            insert_trunk_change(
                conn,
                at_ts=applied_at,
                from_id=prev_trunk_id,
                to_id=diff.target_id,
                reason=reason or diff.rationale[:200],
                applied_by=applied_by,
                instance_id=diff.instance_id,
            )
    except Exception as exc:
        notes.append(f"DB audit write skipped: {exc}")

    return ApplyResult(
        slug=slug,
        diff=diff,
        applied=True,
        applied_by=applied_by,
        journal_block_written=True,
        notes=notes,
    )


def render_tree_effect_block(diff: TreeDiff, *, slug: str, applied: bool) -> str:
    """Render the `### Tree effect` body for the journal entry."""
    badge = {
        "graduate": "**GRADUATE** — APPLIED" if applied else "**GRADUATE** — STAGED (awaits human confirmation)",
        "add_branch": "**Add branch** — auto-applied",
        "reject": "**Reject** — auto-applied",
        "no_op": "**No-op** — recorded for trend analysis",
    }[diff.kind]

    lines: list[str] = [
        f"-   **Verdict:** {badge}",
        f"-   **Target:** `{diff.target_id}`",
    ]
    if diff.trunk_leg or diff.mutation_leg:
        lines.append(
            f"-   **Pair:** trunk leg `{diff.trunk_leg or '?'}` vs mutation `{diff.mutation_leg or '?'}`"
        )
    if diff.pass_rate_delta_pp is not None:
        lines.append(f"-   **Δ pass-rate:** {diff.pass_rate_delta_pp:+.2f} pp")
    if diff.cost_per_pass_delta_pct is not None:
        lines.append(f"-   **Δ $/pass:** {diff.cost_per_pass_delta_pct:+.1f}%")
    lines.append(f"-   **Confidence:** {diff.confidence:.2f}")
    lines.append(f"-   **Rationale:** {diff.rationale}")
    if diff.use_when:
        lines.append(f"-   **Use-when:** `{json.dumps(diff.use_when)}`")
    if diff.evidence_paths:
        ev = ", ".join(f"[`{Path(p).name}`]({_journal_relpath(Path(p))})"
                       for p in diff.evidence_paths[:4])
        lines.append(f"-   **Evidence:** {ev}")
    if diff.cluster_evidence:
        lines.append("")
        lines.append("| Cluster | trunk pass | mut pass | Δ pp |")
        lines.append("|---------|-----------:|---------:|-----:|")
        for c in diff.cluster_evidence[:8]:
            lines.append(
                f"| `{c['cluster']}` | "
                f"{int(c['trunk_pass'])}/{int(c['trunk_n'])} | "
                f"{int(c['mut_pass'])}/{int(c['mut_n'])} | "
                f"{c['delta_pp']:+.1f} |"
            )
    return "\n".join(lines)


def _format_use_when(use_when: dict[str, Any] | None) -> str:
    if not use_when:
        return "(no predicate)"
    rows = use_when.get("any_of") or []
    parts: list[str] = []
    for r in rows:
        for k, v in r.items():
            parts.append(f"{k}={v}")
    if not parts:
        return f"`{json.dumps(use_when)}`"
    return " OR ".join(parts)


def _journal_relpath(path: Path) -> str:
    """Render a repo-relative link suitable for `lab/experiments.md`."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except (ValueError, OSError):
        return str(path)
    return "../" + str(rel)


# ---------------------------------------------------------------------------
# Verdict → component-status side-effects
# ---------------------------------------------------------------------------

# Map an `architecture:` value in an agent YAML to the catalog component id.
_ARCHITECTURE_TO_COMPONENT = {
    None: "single-loop",            # absent ⇒ single-loop
    "": "single-loop",
    "single-loop": "single-loop",
    "planner_executor": "planner-executor",
    "react": "react-loop",
    "reflection": "reflection-loop",
}


def _bump_components_for_diff(
    diff: TreeDiff, *, lab_root: Path | None
) -> list[str]:
    """Bump the catalog status of components implicated by a TreeDiff.

    Best-effort: any failure is logged into the returned notes list but
    does not abort `apply_diff`. The catalog is the *secondary* artefact;
    `configs.md` is canonical for verdicts.

    Bump rules (forward-only — see `components_doc.STATUS_ORDER`):
      - ``add_branch(target)``: components unique to the target (not in
        trunk) → ``branch``.
      - ``graduate(target)``: all components of the new trunk → ``validated``.
      - ``no_op``: components present on the mutation leg → ``experimental``
        (we ran them, even if the verdict was inconclusive).
      - ``reject``: no auto-bump (conservative — a rejected agent may have
        valid components that just don't help in this composition).
    """
    notes: list[str] = []
    target_id = diff.target_id
    target_components = _components_from_agent_yaml(target_id)
    if target_components is None:
        return notes

    if diff.kind == "add_branch":
        trunk_components = (
            _components_from_agent_yaml(diff.trunk_leg) if diff.trunk_leg else set()
        ) or set()
        unique = target_components - trunk_components
        bumped = _safe_bump(unique, "branch", lab_root=lab_root)
        if bumped:
            notes.append(
                f"components.md: bumped {sorted(bumped)} → branch"
            )
    elif diff.kind == "graduate":
        bumped = _safe_bump(target_components, "validated", lab_root=lab_root)
        if bumped:
            notes.append(
                f"components.md: bumped {sorted(bumped)} → validated"
            )
    elif diff.kind == "no_op":
        bumped = _safe_bump(target_components, "experimental", lab_root=lab_root)
        if bumped:
            notes.append(
                f"components.md: bumped {sorted(bumped)} → experimental"
            )
    return notes


def _safe_bump(
    component_ids: set[str], target_status: str, *, lab_root: Path | None
) -> list[str]:
    """Try to bump each id; silently skip ids missing from the catalog."""
    out: list[str] = []
    for cid in sorted(component_ids):
        try:
            kwargs = {"lab_root": lab_root} if lab_root is not None else {}
            cdoc.bump_status(component_id=cid, target=target_status, **kwargs)
            out.append(cid)
        except cdoc.LabDocError:
            continue
        except Exception:
            continue
    return out


def _components_from_agent_yaml(agent_id: str | None) -> set[str] | None:
    """Read the agent YAML and infer which catalog components it composes.

    Returns ``None`` if the YAML can't be read (the bumping path is
    best-effort and silent on failure). Returns an empty set if the YAML
    is readable but yields no recognised components.
    """
    if not agent_id:
        return None
    yaml_path = _AGENT_CONFIGS_DIR / f"{agent_id}.yaml"
    if not yaml_path.is_file():
        return None
    try:
        import yaml
        data = yaml.safe_load(yaml_path.read_text()) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    out: set[str] = set()
    arch = data.get("architecture")
    component_id = _ARCHITECTURE_TO_COMPONENT.get(
        arch if isinstance(arch, str) else None,
        arch if isinstance(arch, str) else None,
    )
    if component_id:
        out.add(component_id)
    model = data.get("model")
    if isinstance(model, str) and model:
        out.add(model)
    explicit = data.get("components") or ()
    if isinstance(explicit, (list, tuple)):
        for c in explicit:
            if isinstance(c, str) and c:
                out.add(c)
    return out
