"""Apply experiment decisions to the lab markdown + DB cache."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openharness.lab import components_doc as cdoc
from openharness.lab import db as labdb
from openharness.lab import lab_docs
from openharness.lab.paths import REPO_ROOT
from openharness.lab.tree_ops import ExperimentDecision, upsert_decision


@dataclass(slots=True)
class ApplyResult:
    slug: str
    decision: ExperimentDecision
    applied: bool
    applied_by: str
    journal_block_written: bool
    notes: list[str]


def apply_decision(
    *,
    slug: str,
    decision: ExperimentDecision,
    applied_by: str = "auto:daemon",
    lab_root: Path | None = None,
    mark_applied: bool = False,
    **_: object,
) -> ApplyResult:
    """Apply an experiment-critic decision to the current checkout."""
    notes: list[str] = []
    branch_applied = True
    lr = {"lab_root": lab_root} if lab_root is not None else {}

    if decision.verdict == "accept":
        journal_link = f"[`{slug}`](experiments.md#{slug})"
        lab_docs.set_current_best(
            agent_id=decision.target_id,
            reason=decision.rationale[:200],
            journal_link=journal_link,
            **lr,
        )
        notes.append(f"configs.md > ## Current best now points at `{decision.target_id}`")
    elif decision.verdict == "reject":
        evidence = ", ".join(str(p) for p in decision.evidence_paths[:2]) or "(see journal)"
        lab_docs.add_rejected(
            branch_id=decision.target_id,
            reason=decision.rationale[:200],
            evidence=evidence,
            **lr,
        )
        notes.append(f"appended `{decision.target_id}` to configs.md > ## Rejected")
    else:
        notes.append("no_op: no config mutation; recorded in journal only")

    block = render_decision_block(decision, slug=slug)
    try:
        lab_docs.set_section(slug=slug, section="Tree effect", body=block, **lr)
        journal_written = True
    except lab_docs.LabDocError as exc:
        notes.append(f"journal write skipped: {exc}")
        journal_written = False

    notes.extend(_bump_components_for_decision(decision, lab_root=lab_root))

    applied_at = datetime.now(timezone.utc) if mark_applied else None
    try:
        with labdb.writer() as conn:
            upsert_decision(
                conn,
                slug=slug,
                decision=decision,
                applied=mark_applied,
                applied_by=applied_by if mark_applied else "pending_finalize",
                applied_at=applied_at,
            )
    except Exception as exc:  # best-effort cache; markdown is canonical
        notes.append(f"DB cache update skipped: {exc}")

    return ApplyResult(
        slug=slug,
        decision=decision,
        applied=branch_applied,
        applied_by=applied_by,
        journal_block_written=journal_written,
        notes=notes,
    )


def mark_decision_merged(
    *,
    instance_id: str,
    applied_by: str,
    pr_url: str | None = None,
    branch_sha: str | None = None,
    applied_at: datetime | None = None,
) -> None:
    """Flip a branch-applied decision to `applied=true` after PR merge."""
    applied_at = applied_at or datetime.now(timezone.utc)
    with labdb.writer() as conn:
        conn.execute(
            """
            UPDATE decisions
               SET applied = TRUE,
                   applied_by = ?,
                   applied_at = ?,
                   pr_url = COALESCE(?, pr_url),
                   branch_sha = COALESCE(?, branch_sha)
             WHERE instance_id = ?
            """,
            [applied_by, applied_at, pr_url, branch_sha, instance_id],
        )


def render_decision_block(decision: ExperimentDecision, *, slug: str) -> str:
    """Render the `### Tree effect` body for the journal entry."""
    _ = slug
    badge = {
        "accept": "**Accept** — experiment outcome supports making this the current best",
        "reject": "**Reject** — experiment outcome argues against this variant",
        "no_op": "**No-op** — recorded for trend analysis",
    }[decision.verdict]

    lines: list[str] = [
        f"-   **Verdict:** {badge}",
        f"-   **Target:** `{decision.target_id}`",
    ]
    if decision.baseline_leg or decision.candidate_leg:
        lines.append(
            f"-   **Pair:** baseline leg `{decision.baseline_leg or '?'}` "
            f"vs candidate `{decision.candidate_leg or '?'}`"
        )
    lines.append(f"-   **Confidence:** {decision.confidence:.2f}")
    lines.append(f"-   **Rationale:** {decision.rationale}")
    if decision.promotability_notes:
        lines.append(f"-   **Generalization notes:** {decision.promotability_notes}")
    if decision.evidence_paths:
        ev = ", ".join(
            f"[`{Path(p).name}`]({_journal_relpath(Path(p))})"
            for p in decision.evidence_paths[:4]
        )
        lines.append(f"-   **Evidence:** {ev}")
    if decision.cluster_evidence:
        lines.append("")
        lines.append("| Cluster | Evidence |")
        lines.append("|---------|----------|")
        for row in decision.cluster_evidence[:8]:
            cluster = row.get("cluster") or row.get("category") or "(unknown)"
            detail = row.get("summary") or row.get("evidence") or json.dumps(row, sort_keys=True)
            lines.append(f"| `{cluster}` | {detail} |")
    return "\n".join(lines)

def _journal_relpath(path: Path) -> str:
    """Render a repo-relative link suitable for `lab/experiments.md`."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except (ValueError, OSError):
        return str(path)
    return "../" + str(rel)


_ARCHITECTURE_TO_COMPONENT = {
    None: "single-loop",
    "": "single-loop",
    "single-loop": "single-loop",
    "planner_executor": "planner-executor",
    "react": "react-loop",
    "reflection": "reflection-loop",
}


def _bump_components_for_decision(
    decision: ExperimentDecision,
    *,
    lab_root: Path | None,
) -> list[str]:
    """Best-effort component status updates for accepted/measured variants."""
    notes: list[str] = []
    target_components = _components_from_agent_yaml(decision.target_id)
    if target_components is None:
        return notes

    if decision.verdict == "accept":
        bumped = _safe_bump(target_components, "validated", lab_root=lab_root)
        if bumped:
            notes.append(f"components.md: bumped {sorted(bumped)} → validated")
    elif decision.verdict == "no_op":
        bumped = _safe_bump(target_components, "experimental", lab_root=lab_root)
        if bumped:
            notes.append(f"components.md: bumped {sorted(bumped)} → experimental")
    return notes


def _safe_bump(
    component_ids: set[str],
    target_status: str,
    *,
    lab_root: Path | None,
) -> list[str]:
    out: list[str] = []
    for component_id in sorted(component_ids):
        try:
            kwargs = {"lab_root": lab_root} if lab_root is not None else {}
            cdoc.bump_status(component_id=component_id, target=target_status, **kwargs)
            out.append(component_id)
        except cdoc.LabDocError:
            continue
    return out


def _components_from_agent_yaml(agent_id: str | None) -> set[str] | None:
    if not agent_id:
        return None
    yaml_path = REPO_ROOT / "src" / "openharness" / "agents" / "configs" / f"{agent_id}.yaml"
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
        out.update(c for c in explicit if isinstance(c, str) and c)
    return out
