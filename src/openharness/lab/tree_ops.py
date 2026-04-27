"""Decision helpers for the autonomous lab.

The simplified lab no longer derives verdicts from deterministic
thresholds. `experiment-critic` owns the judgment call and writes a
structured recommendation to `critic/experiment-critic.json`. This
module validates that payload and mirrors it into the `decisions`
query cache.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from openharness.lab import critic_io
from openharness.lab import db as labdb
from openharness.lab import lab_docs

DecisionVerdict = Literal["accept", "reject", "no_op"]


@dataclass(slots=True)
class LegStats:
    leg_id: str
    agent_id: str
    n_trials: int
    n_passed: int
    cost_usd: float

    @property
    def pass_rate(self) -> float:
        return self.n_passed / self.n_trials if self.n_trials else 0.0

    @property
    def cost_per_pass(self) -> float | None:
        return (self.cost_usd / self.n_passed) if self.n_passed else None


@dataclass(slots=True)
class ExperimentDecision:
    """The experiment-critic recommendation for one experiment instance."""

    verdict: DecisionVerdict
    target_id: str
    rationale: str
    evidence_paths: list[Path] = field(default_factory=list)
    confidence: float = 0.0
    instance_id: str | None = None
    baseline_leg: str | None = None
    candidate_leg: str | None = None
    promotability_notes: str | None = None
    cluster_evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "target_id": self.target_id,
            "rationale": self.rationale,
            "evidence_paths": [str(p) for p in self.evidence_paths],
            "confidence": self.confidence,
            "instance_id": self.instance_id,
            "baseline_leg": self.baseline_leg,
            "candidate_leg": self.candidate_leg,
            "promotability_notes": self.promotability_notes,
            "cluster_evidence": self.cluster_evidence,
        }


def _leg_stats(conn: Any, instance_id: str) -> list[LegStats]:
    rows = conn.execute(
        """
        SELECT l.leg_id, l.agent_id,
               count(t.trial_id)                       AS n_trials,
               sum(CAST(t.passed AS INT))              AS n_passed,
               coalesce(sum(t.cost_usd), 0.0)          AS cost_usd
        FROM legs l
        LEFT JOIN trials t USING (instance_id, leg_id)
        WHERE l.instance_id = ?
        GROUP BY l.leg_id, l.agent_id
        ORDER BY l.leg_id
        """,
        [instance_id],
    ).fetchall()
    return [
        LegStats(
            leg_id=r[0],
            agent_id=r[1] or r[0],
            n_trials=int(r[2] or 0),
            n_passed=int(r[3] or 0),
            cost_usd=float(r[4] or 0.0),
        )
        for r in rows
    ]


def current_best_id() -> str:
    """Return the current best agent id from `lab/configs.md`."""
    return lab_docs.tree_snapshot().current_best_id


def load_decision(
    instance_id: str,
    *,
    db_conn: Any | None = None,
    run_dir: Path | None = None,
) -> ExperimentDecision:
    """Load and validate the experiment-critic decision for `instance_id`."""
    own_conn = db_conn is None
    conn = labdb.connect(read_only=True) if own_conn else db_conn
    try:
        resolved_run_dir = run_dir or critic_io.run_dir_from_instance(
            instance_id,
            db_conn=conn,
        )
        if resolved_run_dir is None:
            raise FileNotFoundError(f"Could not resolve run dir for {instance_id!r}")
        payload = _load_experiment_critic_payload(resolved_run_dir)
        legs = _leg_stats(conn, instance_id)
        return _decision_from_payload(
            payload,
            instance_id=instance_id,
            run_dir=resolved_run_dir,
            legs=legs,
        )
    finally:
        if own_conn:
            conn.close()


def _load_experiment_critic_payload(run_dir: Path) -> dict[str, Any]:
    path = critic_io.experiment_critic_path(run_dir)
    if not path.is_file():
        raise FileNotFoundError(f"experiment-critic output missing: {path}")
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"experiment-critic output is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"experiment-critic output must be a JSON object: {path}")
    return payload


def _decision_from_payload(
    payload: dict[str, Any],
    *,
    instance_id: str,
    run_dir: Path,
    legs: list[LegStats],
) -> ExperimentDecision:
    raw_verdict = _first_str(payload, "verdict")
    verdict = _normalize_verdict(raw_verdict)
    if verdict is None:
        raise ValueError(
            "experiment-critic output must include verdict "
            "with one of: accept, reject, no_op"
        )

    baseline_leg = _first_str(payload, "baseline_leg")
    candidate_leg = _first_str(payload, "candidate_leg")
    target_id = _first_str(payload, "target_id")
    if target_id is None:
        target_id = _target_from_legs(legs, candidate_leg=candidate_leg)

    rationale = _first_str(payload, "rationale")
    if rationale is None:
        rationale = "(experiment-critic did not provide a rationale)"

    confidence = _coerce_confidence(payload.get("confidence"))
    promotability_notes = _first_str(payload, "promotability_notes")
    cluster_evidence = _cluster_evidence_from_payload(payload)

    return ExperimentDecision(
        verdict=verdict,
        target_id=target_id,
        rationale=rationale,
        evidence_paths=list(_evidence_paths_for_instance(instance_id, run_dir=run_dir)),
        confidence=confidence,
        instance_id=instance_id,
        baseline_leg=baseline_leg,
        candidate_leg=candidate_leg,
        promotability_notes=promotability_notes,
        cluster_evidence=cluster_evidence,
    )


def _normalize_verdict(raw: str | None) -> DecisionVerdict | None:
    if raw is None:
        return None
    value = raw.strip().lower().replace("-", "_")
    allowed: dict[str, DecisionVerdict] = {
        "accept": "accept",
        "reject": "reject",
        "no_op": "no_op",
    }
    return allowed.get(value)


def _first_str(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _coerce_confidence(value: Any) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    if isinstance(value, str):
        try:
            return max(0.0, min(1.0, float(value.strip())))
        except ValueError:
            return 0.0
    return 0.0


def _target_from_legs(legs: list[LegStats], *, candidate_leg: str | None) -> str:
    if candidate_leg:
        for leg in legs:
            if leg.leg_id == candidate_leg:
                return leg.agent_id
    if legs:
        best = max(legs, key=lambda leg: (leg.pass_rate, -leg.cost_usd))
        return best.agent_id
    return "(unknown)"


def _cluster_evidence_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("cluster_evidence") or payload.get("clusters") or []
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _evidence_paths_for_instance(
    instance_id: str,
    *,
    run_dir: Path | None = None,
    db_conn: Any | None = None,
) -> Iterable[Path]:
    """Yield the on-disk evidence files most relevant to a decision."""
    resolved = run_dir or critic_io.run_dir_from_instance(instance_id, db_conn=db_conn)
    if resolved is None:
        return
    exp_critic = critic_io.experiment_critic_path(resolved)
    if exp_critic.is_file():
        yield exp_critic
    cmp_dir = resolved / critic_io.CRITIC_DIRNAME / "comparisons"
    if cmp_dir.is_dir():
        yield cmp_dir
    summary = resolved / "results" / "critic_summary.md"
    if summary.is_file():
        yield summary


def upsert_decision(
    conn: Any,
    *,
    slug: str,
    decision: ExperimentDecision,
    applied: bool,
    applied_by: str,
    applied_at: Any,
) -> None:
    """Mirror a decision into the `decisions` query cache."""
    conn.execute(
        """
        INSERT INTO decisions (
            instance_id, slug, verdict, target_id, rationale,
            confidence, evidence_paths, applied, applied_by, applied_at,
            promotability_notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (instance_id) DO UPDATE SET
            slug = EXCLUDED.slug,
            verdict = EXCLUDED.verdict,
            target_id = EXCLUDED.target_id,
            rationale = EXCLUDED.rationale,
            confidence = EXCLUDED.confidence,
            evidence_paths = EXCLUDED.evidence_paths,
            applied = EXCLUDED.applied,
            applied_by = EXCLUDED.applied_by,
            applied_at = EXCLUDED.applied_at,
            promotability_notes = EXCLUDED.promotability_notes
        """,
        [
            decision.instance_id or "",
            slug,
            decision.verdict,
            decision.target_id,
            decision.rationale,
            decision.confidence,
            json.dumps([str(p) for p in decision.evidence_paths]),
            bool(applied),
            applied_by,
            applied_at,
            decision.promotability_notes,
        ],
    )


def insert_current_best_change(
    conn: Any,
    *,
    at_ts: Any,
    from_id: str | None,
    to_id: str,
    reason: str,
    applied_by: str,
    instance_id: str | None = None,
) -> None:
    """Write a current-best history row."""
    conn.execute(
        """
        INSERT INTO current_best_changes (
            at_ts, from_id, to_id, reason, applied_by, instance_id
        ) VALUES (?,?,?,?,?,?)
        ON CONFLICT (at_ts, to_id) DO UPDATE SET
            from_id = EXCLUDED.from_id,
            reason = EXCLUDED.reason,
            applied_by = EXCLUDED.applied_by,
            instance_id = EXCLUDED.instance_id
        """,
        [at_ts, from_id, to_id, reason, applied_by, instance_id],
    )
