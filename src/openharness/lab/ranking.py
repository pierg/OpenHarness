"""Dynamic experiment rankings.

Rankings are derived from immutable run facts plus experiment
evaluations. They are not written back as mutable best-state; callers
can choose a policy and recompute when new experiments arrive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

FULL_SUITE_MIN_TASKS = 80


@dataclass(frozen=True, slots=True)
class RankingRow:
    rank: int
    model_id: str
    dataset: str
    evidence_scope: str
    instance_id: str
    experiment_id: str
    leg_id: str
    agent_id: str
    verdict: str | None
    evaluation_target_id: str | None
    baseline_leg: str | None
    candidate_leg: str | None
    n_trials: int
    n_passed: int
    pass_rate_pct: float | None
    cost_usd: float | None
    cost_per_task_usd: float | None
    cost_per_pass_usd: float | None
    total_tokens: int | None
    tokens_per_task: float | None
    median_duration_sec: float | None
    created_at: datetime | None
    eligible: bool
    eligibility_reason: str
    rationale: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "rank": self.rank,
            "model_id": self.model_id,
            "dataset": self.dataset,
            "evidence_scope": self.evidence_scope,
            "instance_id": self.instance_id,
            "experiment_id": self.experiment_id,
            "leg_id": self.leg_id,
            "agent_id": self.agent_id,
            "verdict": self.verdict,
            "evaluation_target_id": self.evaluation_target_id,
            "baseline_leg": self.baseline_leg,
            "candidate_leg": self.candidate_leg,
            "n_trials": self.n_trials,
            "n_passed": self.n_passed,
            "pass_rate_pct": self.pass_rate_pct,
            "cost_usd": self.cost_usd,
            "cost_per_task_usd": self.cost_per_task_usd,
            "cost_per_pass_usd": self.cost_per_pass_usd,
            "total_tokens": self.total_tokens,
            "tokens_per_task": self.tokens_per_task,
            "median_duration_sec": self.median_duration_sec,
            "created_at": self.created_at,
            "eligible": self.eligible,
            "eligibility_reason": self.eligibility_reason,
            "rationale": self.rationale,
        }


def rankings(
    conn: Any,
    *,
    model_id: str | None = None,
    dataset: str | None = None,
) -> list[RankingRow]:
    """Return ranked leg-level experiment results.

    Rows are grouped by `(model_id, dataset, evidence_scope)` before
    assigning ranks. A rejected target leg remains visible but is not
    eligible for best selection.
    """
    params: list[object] = []
    where: list[str] = []
    if model_id:
        where.append("COALESCE(l.model, t.model, '(unknown)') = ?")
        params.append(model_id)
    if dataset:
        where.append("COALESCE(e.dataset, '(unknown)') = ?")
        params.append(dataset)
    where_sql = "WHERE " + " AND ".join(where) if where else ""

    rows = conn.execute(
        f"""
        SELECT
            e.instance_id,
            e.experiment_id,
            COALESCE(e.dataset, '(unknown)') AS dataset,
            MAX(e.created_at) AS created_at,
            l.leg_id,
            COALESCE(l.agent_id, l.leg_id) AS agent_id,
            COALESCE(l.model, MAX(t.model), '(unknown)') AS model_id,
            COUNT(t.trial_id) AS n_trials,
            SUM(CAST(t.passed AS INT)) AS n_passed,
            ROUND(100.0 * AVG(CAST(t.passed AS DOUBLE)), 4) AS pass_rate_pct,
            SUM(t.cost_usd) AS cost_usd,
            SUM(t.total_tokens) AS total_tokens,
            MEDIAN(t.duration_sec) AS median_duration_sec,
            ev.verdict,
            ev.target_id,
            ev.baseline_leg,
            ev.candidate_leg,
            ev.rationale
        FROM experiments e
        JOIN legs l USING (instance_id)
        LEFT JOIN trials t USING (instance_id, leg_id)
        LEFT JOIN experiment_evaluations ev USING (instance_id)
        {where_sql}
        GROUP BY
            e.instance_id,
            e.experiment_id,
            e.dataset,
            l.leg_id,
            l.agent_id,
            l.model,
            ev.verdict,
            ev.target_id,
            ev.baseline_leg,
            ev.candidate_leg,
            ev.rationale
        """,
        params,
    ).fetchall()

    unranked = [_row_from_db(row) for row in rows]
    grouped: dict[tuple[str, str, str], list[RankingRow]] = {}
    for row in unranked:
        grouped.setdefault((row.model_id, row.dataset, row.evidence_scope), []).append(row)

    ranked: list[RankingRow] = []
    for key in sorted(grouped):
        ordered = sorted(grouped[key], key=_sort_key)
        for idx, row in enumerate(ordered, start=1):
            ranked.append(_replace_rank(row, idx))
    return ranked


def best_by_model(
    conn: Any,
    *,
    dataset: str | None = None,
    evidence_scope: str = "full_suite",
) -> list[RankingRow]:
    """Return the best eligible row per model for one evidence scope."""
    best: dict[str, RankingRow] = {}
    for row in rankings(conn, dataset=dataset):
        if row.evidence_scope != evidence_scope or not row.eligible:
            continue
        current = best.get(row.model_id)
        if current is None or _sort_key(row) < _sort_key(current):
            best[row.model_id] = row
    return sorted(best.values(), key=_sort_key)


def _row_from_db(row: tuple[object, ...]) -> RankingRow:
    (
        instance_id,
        experiment_id,
        dataset,
        created_at,
        leg_id,
        agent_id,
        model_id,
        n_trials_raw,
        n_passed_raw,
        pass_rate_pct_raw,
        cost_usd_raw,
        total_tokens_raw,
        median_duration_raw,
        verdict,
        evaluation_target_id,
        baseline_leg,
        candidate_leg,
        rationale,
    ) = row
    n_trials = int(n_trials_raw or 0)
    n_passed = int(n_passed_raw or 0)
    cost_usd = _opt_float(cost_usd_raw)
    total_tokens = _opt_int(total_tokens_raw)
    evidence_scope = "full_suite" if n_trials >= FULL_SUITE_MIN_TASKS else "slice"
    target_matches = _identifier_matches(
        str(evaluation_target_id) if evaluation_target_id else None, str(agent_id), str(leg_id)
    ) or _identifier_matches(
        str(candidate_leg) if candidate_leg else None, str(agent_id), str(leg_id)
    )
    eligible, reason = _eligibility(
        verdict=str(verdict) if verdict else None,
        target_matches=target_matches,
        n_trials=n_trials,
    )
    return RankingRow(
        rank=0,
        model_id=str(model_id),
        dataset=str(dataset),
        evidence_scope=evidence_scope,
        instance_id=str(instance_id),
        experiment_id=str(experiment_id),
        leg_id=str(leg_id),
        agent_id=str(agent_id),
        verdict=str(verdict) if verdict else None,
        evaluation_target_id=str(evaluation_target_id) if evaluation_target_id else None,
        baseline_leg=str(baseline_leg) if baseline_leg else None,
        candidate_leg=str(candidate_leg) if candidate_leg else None,
        n_trials=n_trials,
        n_passed=n_passed,
        pass_rate_pct=_opt_float(pass_rate_pct_raw),
        cost_usd=cost_usd,
        cost_per_task_usd=(cost_usd / n_trials) if cost_usd is not None and n_trials else None,
        cost_per_pass_usd=(cost_usd / n_passed) if cost_usd is not None and n_passed else None,
        total_tokens=total_tokens,
        tokens_per_task=(total_tokens / n_trials)
        if total_tokens is not None and n_trials
        else None,
        median_duration_sec=_opt_float(median_duration_raw),
        created_at=created_at if isinstance(created_at, datetime) else None,
        eligible=eligible,
        eligibility_reason=reason,
        rationale=str(rationale) if rationale else None,
    )


def _identifier_matches(value: str | None, agent_id: str, leg_id: str) -> bool:
    if value is None:
        return False
    needles = {
        token.strip().strip("`") for token in value.replace(",", " ").replace(";", " ").split()
    }
    return bool(needles & {agent_id, leg_id})


def _eligibility(
    *,
    verdict: str | None,
    target_matches: bool,
    n_trials: int,
) -> tuple[bool, str]:
    if n_trials == 0:
        return False, "no trials"
    if verdict == "reject" and target_matches:
        return False, "rejected experiment target"
    return True, "eligible"


def _sort_key(row: RankingRow) -> tuple[bool, float, float, float, float, int, str]:
    cost = row.cost_per_task_usd if row.cost_per_task_usd is not None else float("inf")
    tokens = row.tokens_per_task if row.tokens_per_task is not None else float("inf")
    duration = row.median_duration_sec if row.median_duration_sec is not None else float("inf")
    return (
        not row.eligible,
        -(row.pass_rate_pct if row.pass_rate_pct is not None else -1.0),
        cost,
        tokens,
        duration,
        -row.n_trials,
        row.agent_id,
    )


def _replace_rank(row: RankingRow, rank: int) -> RankingRow:
    return RankingRow(
        rank=rank,
        model_id=row.model_id,
        dataset=row.dataset,
        evidence_scope=row.evidence_scope,
        instance_id=row.instance_id,
        experiment_id=row.experiment_id,
        leg_id=row.leg_id,
        agent_id=row.agent_id,
        verdict=row.verdict,
        evaluation_target_id=row.evaluation_target_id,
        baseline_leg=row.baseline_leg,
        candidate_leg=row.candidate_leg,
        n_trials=row.n_trials,
        n_passed=row.n_passed,
        pass_rate_pct=row.pass_rate_pct,
        cost_usd=row.cost_usd,
        cost_per_task_usd=row.cost_per_task_usd,
        cost_per_pass_usd=row.cost_per_pass_usd,
        total_tokens=row.total_tokens,
        tokens_per_task=row.tokens_per_task,
        median_duration_sec=row.median_duration_sec,
        created_at=row.created_at,
        eligible=row.eligible,
        eligibility_reason=row.eligibility_reason,
        rationale=row.rationale,
    )


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
