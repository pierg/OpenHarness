# Roadmap

## Up next

> **Methodology contract.** Every entry below declares its
> **Slice** (METHODOLOGY §2), **Legs** (§3), **Repetitions** (§4),
> and **Control** (§5) explicitly so the design phase can be
> audited before code is written. The implement phase always runs
> a small `--profile smoke` exec for wiring validation; the run
> phase always runs the full slice. There are no separate `-smoke`
> / `-full-sweep` roadmap entries.

### timeout-aware-retry-on-needs-network

-   **Idea:** [`executor-bash-timeout-aware-retry`](ideas.md#executor-bash-timeout-aware-retry)
-   **Hypothesis:** timeout-aware retry / background polling recovers a meaningful share of the `needs_network` + `high_env_complexity` failures that currently collapse into repeated command loops or unrecovered bash timeouts.
-   **Slice:** derived `needs_network + high_env_complexity` slice from the current bench, restricted to tasks whose recent failed trials skewed toward `repeated_failed_command` or `timeout_no_recovery`. The implement phase MUST resolve the predicate from recorded artefacts and encode the final task list in `task_filter.include_tasks:`; floor §6 requires at least 5 tasks.
-   **Legs:** 2-leg paired ablation. Leg A: trunk `basic`. Leg B: `basic` + executor timeout-aware retry / background polling. One axis only: timeout recovery path on/off.
-   **Repetitions:** `paired-double` (n_attempts=2) — the recovery path is runtime-sensitive, and the derived slice is composed of unstable long-running tasks where single-shot noise would be hard to interpret.
-   **Control:** `fresh`.
-   **Why first:** four completed experiments still concentrate failures in repeated command loops and unrecovered timeouts, while `planner-schema-guard-paired` only reduced spend on the planner slice without recovering score. This is now the strongest trunk-facing mechanism question with cross-experiment support.
-   **Cost:** ~$5-8

### Suggested

> Auto-proposed by `lab-reflect-and-plan`. Promote to a `### <slug>`
> entry under `## Up next` (above this `### Suggested` subsection)
> to queue for the daemon.

#### trunk-noise-floor-calibration

-   **Hypothesis:** measures pure stochastic swing on the planner-executor confirmation slice — running `basic` (trunk) twice on the same 11-task `cluster_combined` slice with `n_attempts=2` shows how much delta is just noise. If the pure-noise swing exceeds the AddBranch threshold (5pp/cluster), the §6 verdict floor is too lax for cluster-based decisions and should be tightened.
-   **Slice:** same 11 tasks as `planner-executor-cluster-confirmation`. `n_tasks/leg=11`, `n_trials/leg=22`.
-   **Legs:** 2-leg. Leg A: `basic`. Leg B: `basic` (byte-identical). Both run independently (no shared seed).
-   **Repetitions:** `paired-double` (n_attempts=2).
-   **Control:** `fresh`.
-   **Source:** lab-reflect-and-plan@2026-04-22 (methodology revision)
-   **Cost:** ~$2 (2 legs × 22 trials × ~$0.05/trial).
-   **When to run:** only if `planner-schema-guard-paired` and `planner-executor-cluster-confirmation` still leave the cluster-level signal borderline; not a front-of-queue item while larger behavioral failures remain unresolved.


#### stronger-model-baseline

-   **Hypothesis:** tests whether the 24/28 all-leg failures that ignored extra budget are capability-bound rather than guardrail-bound by swapping trunk basic to gemini-2.5-pro on the same near-miss slice.
-   **Source:** lab-reflect-and-plan@2026-04-23
-   **Cost:** ~$10-20
-   **When to run:** after loop / retry / verification guardrails if the same slice still washes out; model spend is lower-priority than mechanism fixes right now.

#### planner-executor-cluster-confirmation

-   **Idea:** confirms the live `AddBranch` from `tb2-baseline-full-sweep`, but only after repairing the planner schema failure mode; still folds in the `grounded-planner-tools` ablation as Leg C for marginal cost.
-   **Hypothesis:** (a) `planner_executor` with the schema guard still beats trunk on `{python_data, system_administration, security_certificates}` with adequate `n`; (b) the planner subagent's read-only tools materially contribute to any recovered win, so removing them on Leg C should hurt.
-   **Slice:** same `cluster_combined: python_data, system_administration, security_certificates` slice as `planner-schema-guard-paired`. Current counts are 11 tasks total, so with `n_attempts=2`, `n_trials/leg = 22`.
-   **Legs:** 3-leg multi-arm (METHODOLOGY §3 — two questions share one slice). Leg A: trunk `basic`. Leg B: `planner_executor` + schema guard. Leg C: Leg B plus planner subagent `tools: []`. Each pairwise contrast differs in exactly one axis.
-   **Repetitions:** `paired-double` (n_attempts=2) — small slice, planner behavior is stochastic, and the original add-branch evidence rested on n=1/3/7.
-   **Control:** `fresh`.
-   **Why third:** once the schema guard decontaminates the planner branch, this run answers the actual branch question: keep `planner_executor` as a specialization, or stop spending on it.
-   **Depends on:** `planner-schema-guard-paired`
-   **Cost:** ~$5-8 (3 legs × 22 trials × ~$0.07-0.10/trial).

## Done

### planner-schema-guard-paired

-   **Idea:** [`planner-schema-guardrail`](ideas.md#planner-schema-guardrail)
-   **Hypothesis:** forcing `planner_executor` to repair invalid or empty planner JSON before executor handoff cuts planner-side `ValidationError` / `structured-output-failure` enough to recover trustworthy signal on the planner-positive slice.
-   **Slice:** `cluster_combined: python_data, system_administration, security_certificates` from `tb2-baseline-full-sweep`. Current counts are 7 + 3 + 1 tasks = 11 tasks, so with `n_attempts=2` this yields `n_trials/leg = 22`.
-   **Legs:** 2-leg paired ablation. Leg A: current `planner_executor`. Leg B: `planner_executor` + planner schema guard. No tool or model changes in this experiment; isolate the guardrail itself.
-   **Repetitions:** `paired-double` (n_attempts=2) — small slice, planner behavior is stochastic, and the baseline branch evidence was contaminated by planner-output failures.
-   **Control:** `fresh`.
-   **Why second:** the current planner branch is not interpretable until planner-side schema breakage is separated from real execution quality. This is the decontamination run before either confirming or retiring the branch.
-   **Depends on:** `tb2-baseline-full-sweep`
-   **Cost:** ~$4-6.

-   **Ran:** [runs/experiments/planner-schema-guard-paired-20260424-154436](../runs/experiments/planner-schema-guard-paired-20260424-154436)
-   **Outcome:** no_op: schema guard matched control at 8/22 passes and only lowered cost, so the branch stays unpromoted.

### loop-guard-on-basic-near-miss

-   **Idea:** [`loop-guard`](ideas.md#loop-guard)
-   **Hypothesis:** enabling `LoopGuardConfig.enabled` on trunk `basic` recovers a meaningful share of the loop-heavy near-miss failures from `extended-budget-paired-on-trunk` by breaking repeated command / timeout spirals without the cost blow-up of longer budgets.
-   **Slice:** `near-miss` — tasks from `extended-budget-paired-on-trunk` where all three budget legs failed and at least one trial logged `repeated_failed_command` or `timeout_no_recovery`. Current evidence suggests `n_tasks ≈ 20-24`; with `n_attempts=2`, expect `n_trials/leg ≈ 40-48`.
-   **Legs:** 2-leg paired ablation. Leg A: trunk `basic`. Leg B: `basic` + loop-guard enabled. One axis only: loop-guard on/off.
-   **Repetitions:** `paired-double` (n_attempts=2) — the mechanism is stochastic, and the slice is a derived near-miss population where single-shot noise would be hard to read.
-   **Control:** `fresh`.
-   **Why first:** both completed experiments say "more budget" is not the answer, while no-progress loops are the dominant shared failure shape. This is the cheapest trunk-facing test of the strongest current hypothesis.
-   **Depends on:** `extended-budget-paired-on-trunk`
-   **Cost:** ~$3-5.

-   **Ran:** [runs/experiments/loop-guard-on-basic-near-miss-20260424-021810](../runs/experiments/loop-guard-on-basic-near-miss-20260424-021810)
-   **Outcome:** reject: loop-guard on basic scored 1/46 vs trunk 2/46 on the near-miss slice and did not recover loop-heavy failures.

### extended-budget-paired-on-trunk

-   **Idea:** [`extended-budget`](ideas.md#extended-budget)
-   **Hypothesis:** the 22.5% baseline is meaningfully budget-bound on the near-miss slice; raising `max_turns` from 30 → 60 → 120 (with `max_tokens` scaled 8192 → 16384 → 32768) lifts pass-rate by ≥10pp on tasks that pinned `n_turns=30` in `tb2-baseline-full-sweep`.
-   **Slice:** `near-miss` — *predicate*: every task in the `basic` leg of `tb2-baseline-20260417-234913` whose terminal trial logged `n_turns=30` (i.e. exhausted the 30-turn budget). The implement phase MUST resolve the predicate against recorded artefacts and encode the resulting list as `task_filter.include_tasks:` in the spec — count is whatever the predicate yields (expected ~15-30 tasks; do NOT hard-code a number). `n_trials/leg = n_tasks × 1` (single-shot). Floor §6 cleared as long as predicate yields ≥ 5 tasks.
-   **Legs:** 3-leg multi-arm (METHODOLOGY §3 — variable has > 2 levels). Leg A: `basic` @ 30/8192 (current trunk). Leg B: `basic` @ 60/16384. Leg C: `basic` @ 120/32768. **Differs in exactly one axis** (the budget pair).
-   **Repetitions:** `single-shot` — pure config tweak (deterministic mechanism), slice ≥ floor (predicate is expected to yield ≥ 15 tasks vs floor 5; if it yields fewer than 5 the implement phase MUST refuse with a precise blocker — that's a real signal the slice doesn't exist).
-   **Control:** `fresh` — required by selection bias on the near-miss slice (METHODOLOGY §5 RTM warning).
-   **Why first:** cheapest experiment in the queue, answers a foundational question that informs every future variant ("is the 22.5% baseline budget-bound or capability-bound?"). Pure YAML tweak — no implementation work.
-   **Cost:** ~$2-3 (3 legs × 15 trials × ~$0.05/trial).

-   **Ran:** [extended-budget-paired-on-trunk](experiments.md#2026-04-23--extended-budget-paired-on-trunk)
-   **Outcome:** Reject: 10.7% trunk pass rate vs 14.3% on both extended-budget legs; budget increases helped one narrow task but did not justify promotion.

### tb2-baseline-full-sweep

-   **Idea:** baseline snapshot
-   **Hypothesis:** the post-reset baseline runs cleanly across all of `terminal-bench@2.0` and produces a real per-agent pass-rate distribution to anchor every future ablation.
-   **Plan:** `uv run exec tb2-baseline` (no `--profile`); 3 legs × ~89 tasks. Launch via `scripts/exp/start.sh exec tb2-baseline` so it survives an SSH disconnect. Watch `events.jsonl` for 429s on the ~30 RPM Gemini cap and adjust `n_concurrent` if needed.
-   **Cost:** ~$15-25, a few hours wall-clock.

## Deferred

> Entries that have been considered and intentionally pushed out of
> `## Up next`. Each one names *why* — usually because the outcome
> doesn't unblock anything else on the queue, or because the
> question has been folded into a different entry. Promote back to
> `## Up next` only when the rationale is no longer current.

### react-tentative-cluster-retest

-   **Hypothesis:** Re-running trunk vs react on react's one positive cluster (`system_administration`, +33pp on n=3) with `paired-double` flips the current `no_op` (1 positive cluster, threshold 2, Δ $/pass +546%) into either a clean `add_branch` or `reject`.
-   **Slice:** `cluster: system_administration`, n_attempts=2, n_trials/leg=6.
-   **Legs:** 2-leg paired ablation (`basic` vs `react`).
-   **Repetitions:** `paired-double`.
-   **Control:** `fresh`.
-   **Why deferred:** outcome doesn't unblock anything else on the roadmap. React is already excluded from trunk; the verdict here would only confirm whether to formally `reject` (low-value action) or pin a narrow `add_branch` on a single n=3 cluster (weak signal). Promote when the queue is otherwise empty.
-   **Source:** lab-reflect-and-plan@2026-04-18, deferred 2026-04-22 (methodology revision)
-   **Cost:** ~$2-3.

### grounded-planner-tools-ablation (FOLDED into `planner-executor-cluster-confirmation`)

-   **Status:** dropped as standalone entry; the question is now answered by Leg C of `planner-executor-cluster-confirmation` (`planner_executor` with planner subagent `tools: []`). Folding in saves ~$10 of full-bench spend and keeps the comparison anchored on the slice where `planner_executor` actually routes.
