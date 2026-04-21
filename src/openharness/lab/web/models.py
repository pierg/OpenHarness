"""Frozen view-models rendered by the web UI templates.

These exist so the data layer hands templates a typed surface
instead of raw DuckDB tuples. Anything dynamic (counts, deltas,
timestamps) is computed in ``data.py`` and frozen here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


type Json = dict[str, object] | list[object] | str | int | float | bool | None


# We use ``eq=False, slots=True`` rather than ``frozen=True`` because some
# fields hold ``dict`` / ``list`` values (JSON blobs, component lists),
# which would make frozen instances un-hashable and break Jinja's equality
# tests. ``slots=True`` keeps the same memory shape; tooling treats them
# as immutable by convention (see python-engineering skill).


@dataclass(eq=False, slots=True)
class DaemonStatus:
    running: bool
    pid: int | None
    started_at: datetime | None
    lock_corrupted: bool
    lock_path: str
    last_log_line: str | None
    log_path: str | None


@dataclass(eq=False, slots=True)
class SpawnRow:
    spawn_id: str
    skill: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_sec: float | None
    exit_code: int | None
    cost_usd_estimate: float | None
    log_path: str | None
    args: Json
    notes: str | None


@dataclass(eq=False, slots=True)
class TreeDiffRow:
    instance_id: str
    slug: str
    kind: str  # graduate | add_branch | reject | no_op
    target_id: str
    rationale: str | None
    use_when: Json
    confidence: float | None
    applied: bool
    applied_by: str | None
    applied_at: datetime | None


@dataclass(eq=False, slots=True)
class TrunkChangeRow:
    at_ts: datetime
    from_id: str | None
    to_id: str
    reason: str | None
    applied_by: str
    instance_id: str | None


@dataclass(eq=False, slots=True)
class ExperimentSummary:
    instance_id: str
    experiment_id: str
    created_at: datetime | None
    git_sha: str | None
    n_legs: int
    n_trials: int
    n_passed: int
    pass_rate_pct: float | None
    cost_usd: float | None
    verdict: TreeDiffRow | None


@dataclass(eq=False, slots=True)
class LegSummary:
    instance_id: str
    leg_id: str
    agent_id: str | None
    model: str | None
    components_active: list[str]
    n_trials: int
    n_passed: int
    n_errored: int
    pass_rate_pct: float | None
    cost_usd: float | None
    tokens_total: int | None
    median_dur_sec: float | None


@dataclass(eq=False, slots=True)
class TrialRow:
    trial_id: str
    instance_id: str
    leg_id: str
    task_name: str
    task_checksum: str | None
    passed: bool | None
    score: float | None
    status: str | None
    error_phase: str | None
    cost_usd: float | None
    duration_sec: float | None
    n_turns: int | None
    trial_dir: str


@dataclass(eq=False, slots=True)
class RoadmapEntryView:
    slug: str
    idea_id: str | None
    hypothesis: str
    plan: str
    depends_on: list[str]
    cost: str | None
    body_md: str
    deps_satisfied: bool


@dataclass(eq=False, slots=True)
class SuggestedEntryView:
    slug: str
    hypothesis: str
    source: str | None
    cost: str | None
    body_md: str


@dataclass(eq=False, slots=True)
class DoneEntryView:
    slug: str
    body_md: str
    ran_link: str | None
    outcome: str | None


@dataclass(eq=False, slots=True)
class IdeaEntryView:
    idea_id: str
    section: str  # Proposed | Trying | Graduated | Rejected | Auto-proposed
    theme: str | None
    motivation: str | None
    sketch: str | None
    cross_refs: list[str]


@dataclass(eq=False, slots=True)
class JournalEntryView:
    slug: str
    date: str  # YYYY-MM-DD as it appears in the heading
    type_: str | None
    trunk_at_runtime: str | None
    mutation: str | None
    hypothesis: str | None
    run_link: str | None
    body_md: str
    sections: dict[str, str] = field(default_factory=dict)
    instance_id: str | None = None  # parsed from run_link if present


@dataclass(eq=False, slots=True)
class PendingActions:
    """Aggregated human-gate inbox (the right-rail drawer)."""

    staged_graduates: list[TreeDiffRow]
    suggested: list[SuggestedEntryView]
    auto_proposed: list[IdeaEntryView]
    misconfig_recent: int
    failed_spawns_recent: int

    @property
    def total(self) -> int:
        return (
            len(self.staged_graduates)
            + len(self.suggested)
            + len(self.auto_proposed)
            + (1 if self.misconfig_recent else 0)
            + (1 if self.failed_spawns_recent else 0)
        )


@dataclass(eq=False, slots=True)
class TaskClusterRow:
    cluster: str
    n_trials: int
    n_passed: int
    pass_rate_pct: float


@dataclass(eq=False, slots=True)
class ComponentPerfRow:
    component_id: str
    task_cluster: str
    n_trials: int
    win_rate: float | None
    cost_delta_pct: float | None
    notes: str | None
    supporting_experiments: list[str]


# ---------------------------------------------------------------------------
# Phase 2 — trial drill-down + planning surfaces
# ---------------------------------------------------------------------------


@dataclass(eq=False, slots=True)
class TrialCritique:
    trial_id: str
    task_summary: str | None
    agent_strategy: str | None
    key_actions: list[str]
    outcome: str | None
    root_cause: str | None
    success_factor: str | None
    anti_patterns: list[str]
    components_active: list[str]
    task_features: Json
    surprising_observations: list[str]
    confidence: float | None
    critic_model: str | None
    created_at: datetime | None


@dataclass(eq=False, slots=True)
class VerifierTest:
    name: str
    status: str
    duration_sec: float | None
    message: str | None


@dataclass(eq=False, slots=True)
class VerifierReport:
    tool_name: str | None
    summary: dict[str, int]
    tests: list[VerifierTest]
    reward_text: str | None
    stdout_excerpt: str | None


@dataclass(eq=False, slots=True)
class TurnCard:
    """One agent step rendered as a card.

    ``role`` is one of ``user`` / ``assistant`` / ``tool_result`` /
    ``system``. ``texts`` are plain text blocks; ``tool_calls`` are
    fully-resolved bash/edit/etc invocations the agent made; the
    matching tool outputs come on the *next* card with role
    ``tool_result``.
    """

    index: int
    role: str
    texts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Json]] = field(default_factory=list)
    tool_results: list[dict[str, Json]] = field(default_factory=list)
    n_chars: int = 0


@dataclass(eq=False, slots=True)
class TrialDetail:
    trial_id: str
    instance_id: str
    leg_id: str
    task_name: str
    task_checksum: str | None
    passed: bool | None
    score: float | None
    status: str | None
    error_phase: str | None
    cost_usd: float | None
    duration_sec: float | None
    n_turns: int | None
    trial_dir: str
    agent_id: str | None
    model: str | None
    components_active: list[str]
    user_prompt: str | None
    critique: TrialCritique | None
    verifier: VerifierReport | None
    turns: list[TurnCard]
    raw_files: list[str]


@dataclass(eq=False, slots=True)
class TaskFeatureView:
    task_checksum: str
    task_name: str
    category: str | None
    required_tools: list[str]
    env_complexity: str | None
    output_shape: str | None
    keywords: list[str]


@dataclass(eq=False, slots=True)
class TaskLeaderboardRow:
    """One trial-on-this-task across experiments, ranked best→worst."""

    instance_id: str
    leg_id: str
    trial_id: str
    agent_id: str | None
    model: str | None
    passed: bool | None
    score: float | None
    cost_usd: float | None
    duration_sec: float | None
    n_turns: int | None
    components_active: list[str]
    created_at: datetime | None


@dataclass(eq=False, slots=True)
class TaskAggregateRow:
    """One row on the /tasks index — one task across the whole lab."""

    task_checksum: str
    task_name: str
    category: str | None
    n_trials: int
    n_passed: int
    pass_rate_pct: float | None
    n_legs: int
    n_experiments: int
    last_seen: datetime | None


@dataclass(eq=False, slots=True)
class ComparisonRow:
    instance_id: str
    task_name: str
    winning_leg: str
    runner_up_leg: str | None
    delta_score: float | None
    why: str | None
    legs_compared: list[str]
    critic_model: str | None
    created_at: datetime | None


@dataclass(eq=False, slots=True)
class ComponentDetail:
    component_id: str
    kind: str | None
    status: str | None
    description: str | None
    used_by: list[str]
    evidence: list[str]
    perf_rows: list[ComponentPerfRow]
    experiments_active_in: list[str]
    experiments_count: int
