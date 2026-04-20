"""Tree operations — translate experiment results into TreeDiffs.

The autonomous lab keeps two artefacts with completely different
lifetimes:

- ``lab/configs.md`` is **the configuration tree** — trunk + branches
  + rejected + proposed agent configs. Persistent state, mutated
  over time.
- ``lab/components.md`` is **the catalog** of building-block
  components (architecture, runtime, tools, prompt, model). Statuses
  are bumped as a *side-effect* of tree mutations on configs.md.
- ``lab/experiments.md`` is **the journal** — append-only log of
  dated events; each entry records exactly one ``TreeDiff`` applied
  to the tree.

This module is the bridge: it reads the per-experiment evidence
(comparisons + trial rows + experiment-critic summary) and returns
the single ``TreeDiff`` that the experiment justifies.

``evaluate(instance_id) -> TreeDiff`` is the only public entry
point. It is **deterministic**: same DB + same critic JSONs in,
same ``TreeDiff`` out. The verdict is then either auto-applied
(``add_branch`` / ``reject`` / ``no_op``) by the daemon or staged
for human confirmation (``graduate``) via
``uv run lab graduate confirm``.

The thresholds below are module-level constants so they can be
tuned without touching the rest of the pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from openharness.lab import critic_io
from openharness.lab import db as labdb
from openharness.lab.paths import REPO_ROOT

# ---------------------------------------------------------------------------
# Thresholds (tunable)
# ---------------------------------------------------------------------------

# Graduate: the mutation leg is unambiguously better than trunk and
# safe to swap in as the new default agent.
GRADUATE_PASS_RATE_DELTA_PP: float = 5.0       # pp = percentage points
GRADUATE_MAX_COST_PER_PASS_DELTA_PCT: float = 10.0
GRADUATE_MAX_PER_CLUSTER_REGRESSION_PP: float = 3.0

# AddBranch: positive on a coherent task subset (≥ N clusters with
# non-trivial Δ), but trunk still wins overall.
ADD_BRANCH_MIN_CLUSTERS: int = 2
ADD_BRANCH_PER_CLUSTER_DELTA_PP: float = 5.0

# Reject: the mutation regresses overall or blows up cost without any
# positive cluster.
REJECT_PASS_RATE_DELTA_PP: float = -2.0
REJECT_COST_PER_PASS_DELTA_PCT: float = 50.0

# NoOp confidence is `1 - (effect_size / smallest_meaningful_effect)`,
# clamped to [0, 1]. Drives `lab-reflect-and-plan`'s decision on
# whether to re-run at higher N.
SMALLEST_MEANINGFUL_EFFECT_PP: float = 5.0


TreeDiffKind = Literal["graduate", "add_branch", "reject", "no_op"]


@dataclass(slots=True)
class TreeDiff:
    """A single proposed mutation of the configuration tree.

    Produced by ``evaluate(instance_id)`` and consumed by
    ``uv run lab tree apply <slug>``. Applying a TreeDiff:

    - ``graduate``: ``trunk.yaml`` swaps to ``target_id``; the old
      trunk moves to a branch (or is rejected if regressions were
      observed). HUMAN-CONFIRMED.
    - ``add_branch``: ``target_id`` is appended to ``configs.md
      > ## Branches`` with ``use_when`` as the predicate. AUTO.
    - ``reject``: ``target_id`` is appended to ``configs.md
      > ## Rejected`` with ``rationale`` as the reason. AUTO.
    - ``no_op``: nothing changes; ``confidence`` records how
      surprised we'd be by a different verdict on a re-run.
    """

    kind: TreeDiffKind
    target_id: str
    rationale: str
    evidence_paths: list[Path] = field(default_factory=list)
    use_when: dict[str, Any] | None = None
    confidence: float = 0.0

    # Optional context for the renderer / journal:
    instance_id: str | None = None
    trunk_leg: str | None = None
    mutation_leg: str | None = None
    pass_rate_delta_pp: float | None = None
    cost_per_pass_delta_pct: float | None = None
    cluster_evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target_id": self.target_id,
            "rationale": self.rationale,
            "evidence_paths": [str(p) for p in self.evidence_paths],
            "use_when": self.use_when,
            "confidence": self.confidence,
            "instance_id": self.instance_id,
            "trunk_leg": self.trunk_leg,
            "mutation_leg": self.mutation_leg,
            "pass_rate_delta_pp": self.pass_rate_delta_pp,
            "cost_per_pass_delta_pct": self.cost_per_pass_delta_pct,
            "cluster_evidence": self.cluster_evidence,
        }


# ---------------------------------------------------------------------------
# Per-leg aggregates
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Trunk discovery
# ---------------------------------------------------------------------------


_TRUNK_YAML_REL = Path("src/openharness/agents/configs/trunk.yaml")


def current_trunk_id(*, db_conn: Any | None = None) -> str:
    """Return the current trunk agent id.

    Resolution order:
      1. ``trunk_changes.to_id`` from the most recent row.
      2. The agent ``name:`` declared in ``trunk.yaml`` (if present).
      3. ``"basic"`` as the historical default (anchored by tb2-baseline).
    """

    def _from_db(conn: Any) -> str | None:
        try:
            row = conn.execute(
                "SELECT to_id FROM trunk_changes ORDER BY at_ts DESC LIMIT 1"
            ).fetchone()
        except Exception:
            return None
        return row[0] if row else None

    if db_conn is not None:
        from_db = _from_db(db_conn)
        if from_db:
            return from_db
    else:
        try:
            with labdb.reader() as conn:
                from_db = _from_db(conn)
        except Exception:
            from_db = None
        if from_db:
            return from_db

    trunk_path = REPO_ROOT / _TRUNK_YAML_REL
    if trunk_path.is_file():
        try:
            import yaml  # local import: yaml may not be available everywhere
            data = yaml.safe_load(trunk_path.read_text()) or {}
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
        except Exception:
            pass

    return "basic"


def _pick_trunk_leg(legs: list[LegStats], trunk_id: str) -> LegStats | None:
    """The trunk leg is the one whose agent_id matches the current trunk."""
    for leg in legs:
        if leg.agent_id == trunk_id or leg.leg_id == trunk_id:
            return leg
    return None


# ---------------------------------------------------------------------------
# Cluster aggregation (uses task-features to define clusters)
# ---------------------------------------------------------------------------


def _cluster_for_task(conn: Any, task_checksum: str | None) -> str:
    """Return the cluster name for a task. Falls back to ``unknown``.

    Today's cluster signal is ``task_features.category``; later we may
    add multi-key clustering (category × env_complexity, etc.).
    """
    if not task_checksum:
        return "unknown"
    row = conn.execute(
        "SELECT category FROM task_features WHERE task_checksum = ?",
        [task_checksum],
    ).fetchone()
    if row and row[0]:
        return str(row[0])
    return "unknown"


def _per_cluster_stats(
    conn: Any, instance_id: str, leg_id: str
) -> dict[str, tuple[int, int]]:
    """Return ``{cluster: (n_trials, n_passed)}`` for one leg."""
    rows = conn.execute(
        """
        SELECT t.task_checksum, count(*), sum(CAST(t.passed AS INT))
        FROM trials t
        WHERE t.instance_id = ? AND t.leg_id = ?
        GROUP BY t.task_checksum
        """,
        [instance_id, leg_id],
    ).fetchall()
    out: dict[str, list[int]] = {}
    for checksum, n_trials, n_passed in rows:
        cluster = _cluster_for_task(conn, checksum)
        bucket = out.setdefault(cluster, [0, 0])
        bucket[0] += int(n_trials or 0)
        bucket[1] += int(n_passed or 0)
    return {k: (v[0], v[1]) for k, v in out.items()}


def _cluster_deltas(
    trunk_buckets: dict[str, tuple[int, int]],
    mutation_buckets: dict[str, tuple[int, int]],
) -> list[dict[str, Any]]:
    """Return per-cluster delta rows sorted by descending |delta|."""
    clusters = sorted(set(trunk_buckets) | set(mutation_buckets))
    rows: list[dict[str, Any]] = []
    for cluster in clusters:
        tn, tp = trunk_buckets.get(cluster, (0, 0))
        mn, mp = mutation_buckets.get(cluster, (0, 0))
        if tn == 0 or mn == 0:
            continue
        trunk_pr = tp / tn
        mut_pr = mp / mn
        rows.append({
            "cluster": cluster,
            "trunk_n": tn,
            "trunk_pass": tp,
            "trunk_pass_rate": trunk_pr,
            "mut_n": mn,
            "mut_pass": mp,
            "mut_pass_rate": mut_pr,
            "delta_pp": (mut_pr - trunk_pr) * 100.0,
        })
    rows.sort(key=lambda r: abs(r["delta_pp"]), reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------


def _classify_pair(
    trunk: LegStats,
    mutation: LegStats,
    cluster_rows: list[dict[str, Any]],
) -> tuple[TreeDiffKind, str, dict[str, Any] | None, float]:
    """Decide the verdict for a single (trunk, mutation) pair.

    Returns ``(kind, rationale, use_when, confidence)``.
    """
    delta_pp = (mutation.pass_rate - trunk.pass_rate) * 100.0
    cost_delta_pct = _cost_per_pass_delta_pct(trunk, mutation)

    positive_clusters = [
        c for c in cluster_rows if c["delta_pp"] >= ADD_BRANCH_PER_CLUSTER_DELTA_PP
    ]
    regressed_clusters = [
        c for c in cluster_rows if c["delta_pp"] <= -GRADUATE_MAX_PER_CLUSTER_REGRESSION_PP
    ]

    # Graduate: big overall win, no per-cluster blowups, cost in budget.
    if (
        delta_pp >= GRADUATE_PASS_RATE_DELTA_PP
        and (cost_delta_pct is None or cost_delta_pct <= GRADUATE_MAX_COST_PER_PASS_DELTA_PCT)
        and not regressed_clusters
    ):
        rationale = (
            f"Δ pass-rate = +{delta_pp:.1f}pp ({trunk.pass_rate:.1%} → "
            f"{mutation.pass_rate:.1%}) over {trunk.n_trials} trials; "
            f"Δ $/pass = "
            + (f"{cost_delta_pct:+.0f}%" if cost_delta_pct is not None else "n/a")
            + "; no per-cluster regression ≥ "
            f"{GRADUATE_MAX_PER_CLUSTER_REGRESSION_PP:.0f}pp."
        )
        return ("graduate", rationale, None, _confidence_from(delta_pp))

    # Reject: clear regression OR large cost spike with no upside.
    cost_blowup = (
        cost_delta_pct is not None
        and cost_delta_pct >= REJECT_COST_PER_PASS_DELTA_PCT
    )
    if (delta_pp <= REJECT_PASS_RATE_DELTA_PP or cost_blowup) and not positive_clusters:
        bits: list[str] = [
            f"Δ pass-rate = {delta_pp:+.1f}pp",
        ]
        if cost_delta_pct is not None:
            bits.append(f"Δ $/pass = {cost_delta_pct:+.0f}%")
        if cost_blowup:
            bits.append(f"cost spike ≥ {REJECT_COST_PER_PASS_DELTA_PCT:.0f}%")
        bits.append("no positive cluster")
        return ("reject", "; ".join(bits) + ".", None, _confidence_from(delta_pp))

    # AddBranch: trunk wins overall but mutation wins on a coherent subset.
    if len(positive_clusters) >= ADD_BRANCH_MIN_CLUSTERS:
        cluster_names = [c["cluster"] for c in positive_clusters]
        use_when = {
            "any_of": [
                {"task_features.category": c["cluster"]}
                for c in positive_clusters
            ],
            "derived_from": "tree_ops.evaluate cluster deltas",
        }
        rationale = (
            f"Trunk wins overall (Δ = {delta_pp:+.1f}pp), but mutation "
            f"wins ≥ +{ADD_BRANCH_PER_CLUSTER_DELTA_PP:.0f}pp on "
            f"{len(positive_clusters)} cluster(s): "
            + ", ".join(
                f"{c['cluster']} ({c['delta_pp']:+.0f}pp, n={c['mut_n']})"
                for c in positive_clusters
            )
            + "."
        )
        # Confidence is high when cluster wins are large; mid otherwise.
        max_cluster_delta = max(c["delta_pp"] for c in positive_clusters)
        return (
            "add_branch",
            rationale,
            use_when,
            _confidence_from(max_cluster_delta),
        )

    # NoOp: neither side moved meaningfully on aggregate or any cluster.
    rationale = (
        f"Inconclusive: Δ pass-rate = {delta_pp:+.1f}pp "
        f"(trunk {trunk.pass_rate:.1%} vs mutation {mutation.pass_rate:.1%}); "
        f"{len(positive_clusters)} positive cluster(s) "
        f"(threshold {ADD_BRANCH_MIN_CLUSTERS}); "
        + (f"Δ $/pass = {cost_delta_pct:+.0f}%." if cost_delta_pct is not None else "no cost data.")
    )
    return ("no_op", rationale, None, _confidence_from(delta_pp))


def _cost_per_pass_delta_pct(trunk: LegStats, mutation: LegStats) -> float | None:
    if trunk.cost_per_pass is None or mutation.cost_per_pass is None:
        return None
    if trunk.cost_per_pass == 0:
        return None
    return ((mutation.cost_per_pass - trunk.cost_per_pass) / trunk.cost_per_pass) * 100.0


def _confidence_from(effect_pp: float) -> float:
    """Map an absolute delta in pp to a [0,1] confidence band.

    Effect ≥ ``SMALLEST_MEANINGFUL_EFFECT_PP`` → 1.0 (high confidence
    in the verdict; a re-run would likely not flip it). Effect of 0
    → 0.0 (very low confidence; reflect-and-plan should re-run at
    higher N before acting).
    """
    if SMALLEST_MEANINGFUL_EFFECT_PP <= 0:
        return 1.0
    return max(0.0, min(1.0, abs(effect_pp) / SMALLEST_MEANINGFUL_EFFECT_PP))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate(instance_id: str, *, db_conn: Any | None = None) -> TreeDiff:
    """Return the TreeDiff justified by experiment ``instance_id``.

    A TreeDiff is computed against the *current trunk*. The default
    experiment shape (paired ablation: trunk leg + 1 mutation leg)
    yields exactly one diff. For broad-sweep experiments (e.g.
    tb2-baseline), the highest-impact non-trunk leg drives the diff;
    the others are summarised in ``cluster_evidence``.
    """
    own_conn = db_conn is None
    conn = labdb.connect(read_only=True) if own_conn else db_conn
    try:
        legs = _leg_stats(conn, instance_id)
        if not legs:
            return TreeDiff(
                kind="no_op",
                target_id="(unknown)",
                rationale=f"instance_id {instance_id!r} has no legs in the DB.",
                instance_id=instance_id,
            )

        trunk_id = current_trunk_id(db_conn=conn)
        trunk_leg = _pick_trunk_leg(legs, trunk_id)
        mutations = [leg for leg in legs if leg is not trunk_leg]

        # Sole-leg experiments: nothing to compare against trunk.
        if trunk_leg is None and len(legs) == 1:
            return TreeDiff(
                kind="no_op",
                target_id=legs[0].agent_id,
                rationale=(
                    f"single-leg experiment ({legs[0].leg_id}); current "
                    f"trunk={trunk_id!r} not present, nothing to compare."
                ),
                instance_id=instance_id,
                mutation_leg=legs[0].leg_id,
                confidence=0.0,
            )

        # Trunk absent but multiple legs present (e.g. broad-sweep with
        # all three architectures + no `basic`-named leg). Pick the
        # best-performing leg as the ad-hoc trunk so the analysis can
        # still produce a verdict.
        if trunk_leg is None:
            trunk_leg = max(legs, key=lambda leg: leg.pass_rate)
            mutations = [leg for leg in legs if leg is not trunk_leg]

        if not mutations:
            return TreeDiff(
                kind="no_op",
                target_id=trunk_leg.agent_id,
                rationale="only trunk leg present; nothing to mutate.",
                instance_id=instance_id,
                trunk_leg=trunk_leg.leg_id,
                confidence=0.0,
            )

        # Pre-compute trunk per-cluster stats once.
        trunk_clusters = _per_cluster_stats(conn, instance_id, trunk_leg.leg_id)

        per_mutation: list[tuple[LegStats, TreeDiffKind, str, dict | None, float, list[dict]]] = []
        for mutation in mutations:
            mut_clusters = _per_cluster_stats(conn, instance_id, mutation.leg_id)
            cluster_rows = _cluster_deltas(trunk_clusters, mut_clusters)
            kind, rationale, use_when, conf = _classify_pair(
                trunk_leg, mutation, cluster_rows
            )
            per_mutation.append(
                (mutation, kind, rationale, use_when, conf, cluster_rows)
            )

        # Single mutation → return its verdict directly.
        if len(per_mutation) == 1:
            mutation, kind, rationale, use_when, conf, cluster_rows = per_mutation[0]
            return _build_diff(
                instance_id=instance_id,
                trunk=trunk_leg,
                mutation=mutation,
                kind=kind,
                rationale=rationale,
                use_when=use_when,
                confidence=conf,
                cluster_rows=cluster_rows,
            )

        # Multiple mutations (broad-sweep): pick the most decisive one.
        # Priority: graduate > add_branch > reject > no_op; tie-break by
        # absolute pass-rate delta vs trunk.
        order = {"graduate": 0, "add_branch": 1, "reject": 2, "no_op": 3}
        per_mutation.sort(
            key=lambda t: (
                order[t[1]],
                -abs((t[0].pass_rate - trunk_leg.pass_rate) * 100.0),
            )
        )
        primary = per_mutation[0]
        mutation, kind, rationale, use_when, conf, cluster_rows = primary

        # Append a one-line summary for each non-primary mutation so the
        # journal entry is self-contained.
        rationale_extra: list[str] = [rationale]
        for other in per_mutation[1:]:
            o_mut, o_kind, o_rat, _, _, _ = other
            rationale_extra.append(
                f"(also: {o_mut.leg_id} → {o_kind}: {o_rat})"
            )
        full_rationale = " ".join(rationale_extra)

        return _build_diff(
            instance_id=instance_id,
            trunk=trunk_leg,
            mutation=mutation,
            kind=kind,
            rationale=full_rationale,
            use_when=use_when,
            confidence=conf,
            cluster_rows=cluster_rows,
        )
    finally:
        if own_conn:
            conn.close()


def _build_diff(
    *,
    instance_id: str,
    trunk: LegStats,
    mutation: LegStats,
    kind: TreeDiffKind,
    rationale: str,
    use_when: dict[str, Any] | None,
    confidence: float,
    cluster_rows: list[dict[str, Any]],
) -> TreeDiff:
    delta_pp = (mutation.pass_rate - trunk.pass_rate) * 100.0
    cost_delta_pct = _cost_per_pass_delta_pct(trunk, mutation)

    # Target: the agent id of the leg the diff is "about".
    if kind == "graduate":
        target = mutation.agent_id
    elif kind == "add_branch":
        target = mutation.agent_id
    elif kind == "reject":
        target = mutation.agent_id
    else:  # no_op
        target = mutation.agent_id

    evidence_paths = list(_evidence_paths_for_instance(instance_id))

    return TreeDiff(
        kind=kind,
        target_id=target,
        rationale=rationale,
        evidence_paths=evidence_paths,
        use_when=use_when,
        confidence=round(confidence, 3),
        instance_id=instance_id,
        trunk_leg=trunk.leg_id,
        mutation_leg=mutation.leg_id,
        pass_rate_delta_pp=round(delta_pp, 2),
        cost_per_pass_delta_pct=(
            None if cost_delta_pct is None else round(cost_delta_pct, 1)
        ),
        cluster_evidence=cluster_rows,
    )


def _evidence_paths_for_instance(instance_id: str) -> Iterable[Path]:
    """Yield the on-disk evidence files most relevant to a verdict.

    Used by the journal entry's ``Tree effect`` block so a human
    can click straight to the supporting data.
    """
    run_dir = critic_io.run_dir_from_instance(instance_id)
    if run_dir is None:
        return
    exp_critic = critic_io.experiment_critic_path(run_dir)
    if exp_critic.is_file():
        yield exp_critic
    cmp_dir = run_dir / critic_io.CRITIC_DIRNAME / "comparisons"
    if cmp_dir.is_dir():
        yield cmp_dir
    summary = run_dir / "results" / "critic_summary.md"
    if summary.is_file():
        yield summary


# ---------------------------------------------------------------------------
# DB cache helpers (used by `tree apply` and `ingest-critiques`)
# ---------------------------------------------------------------------------


def upsert_tree_diff(
    conn: Any,
    *,
    slug: str,
    diff: TreeDiff,
    applied: bool,
    applied_by: str,
    applied_at: Any,
) -> None:
    """Mirror a TreeDiff into the ``tree_diffs`` cache table."""
    conn.execute(
        """
        INSERT INTO tree_diffs (
            instance_id, slug, kind, target_id, rationale,
            use_when, confidence, evidence_paths,
            applied, applied_by, applied_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (instance_id) DO UPDATE SET
            slug = EXCLUDED.slug,
            kind = EXCLUDED.kind,
            target_id = EXCLUDED.target_id,
            rationale = EXCLUDED.rationale,
            use_when = EXCLUDED.use_when,
            confidence = EXCLUDED.confidence,
            evidence_paths = EXCLUDED.evidence_paths,
            applied = EXCLUDED.applied,
            applied_by = EXCLUDED.applied_by,
            applied_at = EXCLUDED.applied_at
        """,
        [
            diff.instance_id or "",
            slug,
            diff.kind,
            diff.target_id,
            diff.rationale,
            json.dumps(diff.use_when) if diff.use_when else None,
            diff.confidence,
            json.dumps([str(p) for p in diff.evidence_paths]),
            bool(applied),
            applied_by,
            applied_at,
        ],
    )


def insert_trunk_change(
    conn: Any,
    *,
    at_ts: Any,
    from_id: str | None,
    to_id: str,
    reason: str,
    applied_by: str,
    instance_id: str | None = None,
) -> None:
    """Append a row to ``trunk_changes``. Append-only audit log."""
    conn.execute(
        """
        INSERT INTO trunk_changes (
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
