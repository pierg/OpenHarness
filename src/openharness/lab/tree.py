"""Apply experiment evaluations to the lab markdown + DB cache."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from openharness.lab import components_doc as cdoc
from openharness.lab import db as labdb
from openharness.lab import lab_docs
from openharness.lab.paths import REPO_ROOT
from openharness.lab.evaluation import ExperimentEvaluation, upsert_evaluation


@dataclass(slots=True)
class ApplyResult:
    slug: str
    evaluation: ExperimentEvaluation
    applied: bool
    applied_by: str
    journal_block_written: bool
    notes: list[str]


def apply_evaluation(
    *,
    slug: str,
    evaluation: ExperimentEvaluation,
    applied_by: str = "auto:daemon",
    lab_root: Path | None = None,
    mark_applied: bool = False,
    **_: object,
) -> ApplyResult:
    """Record an experiment-critic evaluation in the current checkout."""
    notes: list[str] = []
    branch_applied = True
    lr = {"lab_root": lab_root} if lab_root is not None else {}

    if evaluation.verdict == "accept":
        notes.append(
            "accept: experiment implementation is worth preserving; "
            "leaderboard ranking is recomputed separately"
        )
    elif evaluation.verdict == "reject":
        evidence = ", ".join(str(p) for p in evaluation.evidence_paths[:2]) or "(see journal)"
        lab_docs.add_rejected(
            branch_id=evaluation.target_id,
            reason=evaluation.rationale[:200],
            evidence=evidence,
            **lr,
        )
        notes.append(f"appended `{evaluation.target_id}` to configs.md > ## Rejected")
    else:
        notes.append("no_op: no config mutation; recorded in journal only")

    block = render_evaluation_block(evaluation, slug=slug)
    try:
        lab_docs.set_section(slug=slug, section="Experiment evaluation", body=block, **lr)
        journal_written = True
    except lab_docs.LabDocError as exc:
        notes.append(f"journal write skipped: {exc}")
        journal_written = False

    notes.extend(_bump_components_for_evaluation(evaluation, lab_root=lab_root))

    applied_at = datetime.now(timezone.utc) if mark_applied else None
    try:
        with labdb.writer() as conn:
            upsert_evaluation(
                conn,
                slug=slug,
                evaluation=evaluation,
                applied=mark_applied,
                applied_by=applied_by if mark_applied else "pending_finalize",
                applied_at=applied_at,
            )
    except Exception as exc:  # best-effort cache; markdown is canonical
        notes.append(f"DB cache update skipped: {exc}")

    return ApplyResult(
        slug=slug,
        evaluation=evaluation,
        applied=branch_applied,
        applied_by=applied_by,
        journal_block_written=journal_written,
        notes=notes,
    )


def mark_evaluation_finalized(
    *,
    instance_id: str,
    applied_by: str,
    pr_url: str | None = None,
    branch_sha: str | None = None,
    applied_at: datetime | None = None,
) -> None:
    """Flip a branch-recorded evaluation to `applied=true` after finalize."""
    applied_at = applied_at or datetime.now(timezone.utc)
    with labdb.writer() as conn:
        conn.execute(
            """
            UPDATE experiment_evaluations
               SET applied = TRUE,
                   applied_by = ?,
                   applied_at = ?,
                   pr_url = COALESCE(?, pr_url),
                   branch_sha = COALESCE(?, branch_sha)
             WHERE instance_id = ?
            """,
            [applied_by, applied_at, pr_url, branch_sha, instance_id],
        )


def render_evaluation_block(evaluation: ExperimentEvaluation, *, slug: str) -> str:
    """Render the `### Experiment evaluation` body for the journal entry."""
    _ = slug
    badge = {
        "accept": "**Accept** — experiment implementation is worth preserving",
        "reject": "**Reject** — experiment outcome argues against this variant",
        "no_op": "**No-op** — recorded for trend analysis",
    }[evaluation.verdict]

    lines: list[str] = [
        f"-   **Verdict:** {badge}",
        f"-   **Target:** `{evaluation.target_id}`",
        "-   **Ranking:** not assigned here; compare via the dynamic leaderboard "
        "within the same model/dataset group.",
    ]
    if evaluation.baseline_leg or evaluation.candidate_leg:
        lines.append(
            f"-   **Pair:** baseline leg `{evaluation.baseline_leg or '?'}` "
            f"vs candidate `{evaluation.candidate_leg or '?'}`"
        )
    lines.append(f"-   **Confidence:** {evaluation.confidence:.2f}")
    lines.append(f"-   **Rationale:** {evaluation.rationale}")
    if evaluation.promotability_notes:
        lines.append(f"-   **Generalization notes:** {evaluation.promotability_notes}")
    if evaluation.evidence_paths:
        ev = ", ".join(
            f"[`{Path(p).name}`]({_journal_relpath(Path(p))})"
            for p in evaluation.evidence_paths[:4]
        )
        lines.append(f"-   **Evidence:** {ev}")
    if evaluation.cluster_evidence:
        lines.append("")
        lines.append("| Cluster | Evidence |")
        lines.append("|---------|----------|")
        for row in evaluation.cluster_evidence[:8]:
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


def _bump_components_for_evaluation(
    evaluation: ExperimentEvaluation,
    *,
    lab_root: Path | None,
) -> list[str]:
    """Best-effort component status updates for accepted/measured variants."""
    notes: list[str] = []
    target_components = _components_from_agent_yaml(evaluation.target_id)
    if target_components is None:
        return notes

    if evaluation.verdict == "accept":
        bumped = _safe_bump(target_components, "validated", lab_root=lab_root)
        if bumped:
            notes.append(f"components.md: bumped {sorted(bumped)} → validated")
    elif evaluation.verdict == "no_op":
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
