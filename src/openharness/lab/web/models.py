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
class PhaseView:
    """One step inside the orchestrator's 7-phase pipeline.

    Mirrors :class:`openharness.lab.phase_state.PhaseRecord` plus the
    fields the cockpit's pipeline strip needs to render a status pill
    without re-parsing JSON in Jinja.

    ``status`` is one of ``pending`` / ``running`` / ``ok`` / ``failed``
    / ``skipped``. ``summary`` is a single-line human-readable digest
    of ``payload`` — for example "3 commits", "instance=20260422…", or
    "PR #123" — so the cockpit can show what each phase produced
    without expanding to the raw JSON.
    """

    name: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_sec: float | None
    error: str | None
    summary: str | None
    is_active: bool


@dataclass(eq=False, slots=True)
class PipelineView:
    """The "what is the daemon doing right now" snapshot.

    Built by :meth:`LabReader.pipeline_view`. When the daemon is in
    flight, ``slug`` is the active tick and ``is_active`` is true; when
    the daemon is idle, the most recently advanced slug is surfaced
    so the operator still sees a meaningful narrative ("here's where
    the last run got to").

    Always carries seven :class:`PhaseView` rows in
    :data:`openharness.lab.phase_state.PHASE_ORDER` so the template
    can render the strip without conditional bookkeeping.
    """

    slug: str
    hypothesis: str | None
    is_active: bool
    needs_variant: bool
    worktree_path: str | None
    branch: str | None
    spawn_pid: int | None
    spawn_log_path: str | None
    spawn_log_basename: str | None
    note: str | None
    started_at: datetime | None
    last_updated_at: datetime | None
    current_phase: str | None
    phases: list[PhaseView]


@dataclass(eq=False, slots=True)
class ProcessNode:
    """One node in the live process tree under the orchestrator daemon.

    Returned by ``LabReader.process_tree``. Children are nested
    recursively so the template can render an indented tree without
    flattening / sorting in Jinja. ``cmdline_short`` is a single-line
    truncation of argv suitable for table cells; ``cmdline_full`` is
    the untruncated version exposed via tooltip / detail pane.

    ``can_kill`` mirrors the safety check in
    ``commands._precheck_kill_process``: only descendants of the
    daemon main_pid (and not the daemon itself) are eligible.
    """

    pid: int
    ppid: int
    name: str
    username: str | None
    status: str
    started_at: datetime | None
    cpu_percent: float
    mem_rss_mb: float
    cmdline_short: str
    cmdline_full: str
    is_daemon_root: bool
    can_kill: bool
    children: list["ProcessNode"]


@dataclass(eq=False, slots=True)
class SpawnRow:
    spawn_id: str
    skill: str
    provider: str | None
    model: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_sec: float | None
    exit_code: int | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_output_tokens: int | None
    total_tokens: int | None
    cost_usd_estimate: float | None
    log_path: str | None
    args: Json
    notes: str | None


@dataclass(eq=False, slots=True)
class UsageSummaryRow:
    source: str
    provider: str | None
    model: str | None
    step: str
    calls: int
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_output_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None


@dataclass(eq=False, slots=True)
class EvaluationRow:
    instance_id: str
    slug: str
    verdict: str  # accept | reject | no_op
    target_id: str
    rationale: str | None
    confidence: float | None
    applied: bool
    applied_by: str | None
    applied_at: datetime | None


@dataclass(eq=False, slots=True)
class AgentLadderRow:
    rank: int
    model_id: str
    dataset: str
    evidence_scope: str
    agent_id: str
    status: str  # top ranked | ranked | ineligible
    evaluated_at: datetime | None
    accepting_instance_id: str | None
    pass_rate_pct: float | None
    cost_per_task_usd: float | None
    cost_per_pass_usd: float | None
    tokens_per_task: float | None
    median_duration_sec: float | None
    n_trials: int
    n_passed: int
    eligible: bool
    eligibility_reason: str
    reason: str | None


@dataclass(eq=False, slots=True)
class ExperimentDeltaRow:
    instance_id: str
    slug: str
    verdict: str
    target_id: str
    decided_at: datetime | None
    baseline_leg: str | None
    candidate_leg: str | None
    baseline_pass_rate_pct: float | None
    candidate_pass_rate_pct: float | None
    delta_pp: float | None
    cost_per_task_delta_usd: float | None
    n_trials: int
    rationale: str | None
    confidence: float | None


@dataclass(eq=False, slots=True)
class ImprovementPoint:
    at_ts: datetime
    agent_id: str
    instance_id: str | None
    pass_rate_pct: float | None
    delta_pp: float | None
    rationale: str | None


@dataclass(eq=False, slots=True)
class LeaderboardView:
    top_agent_id: str
    top_model_id: str | None
    top_dataset: str | None
    top_pass_rate_pct: float | None
    top_cost_per_task_usd: float | None
    top_evaluated_at: datetime | None
    policy_label: str
    ladder: list[AgentLadderRow]
    deltas: list[ExperimentDeltaRow]
    trajectory: list[ImprovementPoint]


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
    verdict: EvaluationRow | None


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
    section: str  # Proposed | Trying | Accepted | Rejected | Auto-proposed
    theme: str | None
    motivation: str | None
    sketch: str | None
    cross_refs: list[str]


@dataclass(eq=False, slots=True)
class JournalEntryView:
    slug: str
    date: str  # YYYY-MM-DD as it appears in the heading
    type_: str | None
    baseline_at_runtime: str | None
    mutation: str | None
    hypothesis: str | None
    run_link: str | None
    body_md: str
    sections: dict[str, str] = field(default_factory=dict)
    instance_id: str | None = None  # parsed from run_link if present


@dataclass(eq=False, slots=True)
class PendingActions:
    """Aggregated human-gate inbox (the right-rail drawer)."""

    suggested: list[SuggestedEntryView]
    auto_proposed: list[IdeaEntryView]
    misconfig_recent: int
    failed_spawns_recent: int

    @property
    def total(self) -> int:
        return (
            len(self.suggested)
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
    input_tokens: int | None
    cache_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
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
    """One row on the /catalog tasks tab — one task across the whole lab."""

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


# ---------------------------------------------------------------------------
# Phase 3 — PR-aware redesign view models
#
# Surface the methodology dimensions documented in lab/METHODOLOGY.md:
# every accepted/rejected/no-op evaluation is bound to the canonical
# experiment PR. Accepted PRs land on `main`; rejected/no-op PRs are
# closed unmerged after an evaluation comment. The web UI needs to know, per
# row, whether that PR is open, merged, or rejected.
# ---------------------------------------------------------------------------


@dataclass(eq=False, slots=True)
class PRStateRow:
    """Summarised state of one experiment PR.

    Built by :meth:`LabReader.pr_states`. Composes
    ``experiment_evaluations.pr_url`` (cheap, always present) with optional
    ``gh pr view`` data (slow, may be None when ``gh`` is missing or
    the worker host has no GitHub token). When ``state`` is None the
    UI renders a "PR open · CI status unknown" fallback rather than
    pretending the PR doesn't exist.

    ``slug`` and ``instance_id`` are duplicated so callers can render
    a row without re-joining evaluations.
    """

    slug: str
    instance_id: str
    verdict: str
    pr_url: str
    pr_number: int | None
    state: str | None  # OPEN | MERGED | CLOSED
    is_merged: bool
    mergeable: str | None  # MERGEABLE | CONFLICTING | UNKNOWN
    checks_status: str | None  # SUCCESS | FAILURE | PENDING
    auto_merge_enabled: bool
    title: str | None
    head_sha: str | None
    checked_at: datetime | None
    error: str | None  # populated when gh refused / wasn't on PATH


@dataclass(eq=False, slots=True)
class DaemonIdleReason:
    """Why the daemon is not (or is) advancing.

    Computed by :meth:`LabReader.idle_reason` from the live
    DaemonState and the PR cache. Surfaced by the homepage's "Now /
    Waiting on" zones so the operator can tell at a glance whether
    the lab is genuinely idle, paused on purpose, or blocked on
    something they need to resolve.

    ``code`` values:
      - ``running``        — there is an active tick; ``slug`` set
      - ``paused``         — daemon mode == "paused"
      - ``manual_no_appr`` — manual mode, no approved+ready entries
      - ``no_queue``       — autonomous (or manual) but nothing ready
      - ``blocked``        — ready entries exist but hit the failure gate
      - ``stopped``        — daemon is not running at all
      - ``unknown``        — fallback so the UI never renders "" badge
    """

    code: str
    detail: str
    slug: str | None = None
    blocking_prs: list[str] = field(default_factory=list)


@dataclass(eq=False, slots=True)
class ActivityLogEntry:
    """One row in the unified ``/activity`` timeline.

    Fold-in of audit-log entries (``runs/lab/web_commands.jsonl``),
    tick history (from daemon_state.history), spawn finishes (from
    DuckDB ``spawns``), experiment evaluations, and dynamic rankings.
    Lets the operator see "what changed
    in the last hour" without bouncing between four pages.

    ``kind`` is one of:
      - ``cmd``            — web /api/cmd execution
      - ``tick``           — daemon tick finished
      - ``spawn``          — codex skill spawn finished
      - ``verdict``        — evaluation row appeared
      - ``ranking``        — dynamic ranking context
    """

    at_ts: datetime
    kind: str
    actor: str
    title: str
    detail: str | None = None
    slug: str | None = None
    instance_id: str | None = None
    success: bool | None = None
    href: str | None = None


@dataclass(eq=False, slots=True)
class CellRow:
    """One cell in the per-task × per-leg evidence matrix.

    Built for the ``/runs/<id> Cells`` tab. In addition to per-trial
    scalars, this row also exposes:

    - ``cluster``          — task-feature category, used for grouping
    - ``trial_dir``        — drawer link target
    - ``status_glyph``     — pre-computed icon class for the cell
    - ``border_color``     — pre-computed Tailwind class so the
      template doesn't have to ladder through if/elif by status
    """

    task_name: str
    task_checksum: str | None
    leg_id: str
    cluster: str
    trial_id: str
    passed: bool | None
    score: float | None
    status: str | None
    cost_usd: float | None
    duration_sec: float | None
    n_turns: int | None
    trial_dir: str
    status_glyph: str = "?"
    border_color: str = "border-slate-200"


@dataclass(eq=False, slots=True)
class ClusterDeltaRow:
    """Per-cluster paired-Δ across the experiment's legs.

    Drives the ``/runs/<id> Δ`` tab's headline numbers. ``warning``
    fires when the per-cluster trial count falls below the
    methodology's "minimum power" floor, so the operator can flag
    the row as "noise, not signal" without re-reading
    ``METHODOLOGY.md``.
    """

    cluster: str
    n_tasks: int
    leg_a: str
    leg_b: str
    delta_pp: float | None
    warning: bool


@dataclass(eq=False, slots=True)
class TreeVizNode:
    """One node in the configuration-tree visualisation.

    Joined view: ``lab/configs.md`` (baseline + rejected +
    proposed) + the canonical ``experiment_evaluations.pr_url`` + the live PR
    cache. The template uses this to render the SVG with per-node
    badges indicating which branch has an open PR (dashed outline), a
    merged or closed PR (solid border), or no PR yet (no badge).
    """

    node_id: str
    role: str  # operational_baseline | rejected | proposed
    mutation: str | None = None
    linked_idea: str | None = None
    sketch: str | None = None
    reason: str | None = None
    last_verified: str | None = None
    pr: PRStateRow | None = None
