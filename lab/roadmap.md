# Roadmap

## Up next

> **Methodology contract.** Every entry below declares its
> **Slice** (METHODOLOGY §2), **Legs** (§3), **Repetitions** (§4),
> and **Control** (§5) explicitly so the design phase can be
> audited before code is written. The implement phase always runs
> a small `--profile smoke` exec for wiring validation; the run
> phase always runs the full slice. There are no separate `-smoke`
> / `-full-sweep` roadmap entries.

### extended-budget-paired-on-trunk

-   **Idea:** [`extended-budget`](ideas.md#extended-budget)
-   **Hypothesis:** the 22.5% baseline is meaningfully budget-bound on the near-miss slice; raising `max_turns` from 30 → 60 → 120 (with `max_tokens` scaled 8192 → 16384 → 32768) lifts pass-rate by ≥10pp on tasks that pinned `n_turns=30` in `tb2-baseline-full-sweep`.
-   **Slice:** `near-miss` — *predicate*: every task in the `basic` leg of `tb2-baseline-20260417-234913` whose terminal trial logged `n_turns=30` (i.e. exhausted the 30-turn budget). The implement phase MUST resolve the predicate against recorded artefacts and encode the resulting list as `task_filter.include_tasks:` in the spec — count is whatever the predicate yields (expected ~15-30 tasks; do NOT hard-code a number). `n_trials/leg = n_tasks × 1` (single-shot). Floor §6 cleared as long as predicate yields ≥ 5 tasks.
-   **Legs:** 3-leg multi-arm (METHODOLOGY §3 — variable has > 2 levels). Leg A: `basic` @ 30/8192 (current trunk). Leg B: `basic` @ 60/16384. Leg C: `basic` @ 120/32768. **Differs in exactly one axis** (the budget pair).
-   **Repetitions:** `single-shot` — pure config tweak (deterministic mechanism), slice ≥ floor (predicate is expected to yield ≥ 15 tasks vs floor 5; if it yields fewer than 5 the implement phase MUST refuse with a precise blocker — that's a real signal the slice doesn't exist).
-   **Control:** `fresh` — required by selection bias on the near-miss slice (METHODOLOGY §5 RTM warning).
-   **Why first:** cheapest experiment in the queue, answers a foundational question that informs every future variant ("is the 22.5% baseline budget-bound or capability-bound?"). Pure YAML tweak — no implementation work.
-   **Cost:** ~$2-3 (3 legs × 15 trials × ~$0.05/trial).

### planner-executor-cluster-confirmation

-   **Idea:** confirms the live `AddBranch` from `tb2-baseline-full-sweep`; folds in the `grounded-planner-tools` ablation as Leg C for marginal cost.
-   **Hypothesis:** (a) the `add_branch` predicate `{python_data, system_administration, security_certificates}` for `planner_executor` survives a re-run with adequate per-cluster `n` (the original verdict rests on n=7/3/1); (b) the planner subagent's read-only tools materially contribute to the win — removing them on Leg C demonstrates the dependency.
-   **Slice:** `cluster_combined: python_data, system_administration, security_certificates` (DEFERRED slice shape — until it lands, declare as `cluster: python_data, system_administration, security_certificates` and rely on `paired-double` to clear the floor). `n_tasks/leg = 7+3+1 = 11`. `n_trials/leg = 22` (with `n_attempts=2`).
-   **Legs:** 3-leg multi-arm (METHODOLOGY §3 — two questions share the slice). Leg A: `basic` (trunk). Leg B: `planner_executor` (current YAML). Leg C: `planner_executor` with planner subagent `tools: []`. Each pairwise contrast differs in exactly one axis.
-   **Repetitions:** `paired-double` (n_attempts=2) — small slice (11 tasks), planner has stochastic internal state (sampled plan), and the original verdict rests on n=1/3/7 so we want noise-bounded per-cell estimates.
-   **Control:** `fresh`.
-   **Why second:** retires the highest-value piece of methodological debt (the n=1/3/7 wobble); folds in `grounded-planner-tools` for ~50% extra cost; gates every future planner_executor variant.
-   **Depends on:** `tb2-baseline-full-sweep`
-   **Cost:** ~$5-8 (3 legs × 22 trials × ~$0.07-0.10/trial).

### loop-guard-paired-ablation

-   **Idea:** [`loop-guard`](ideas.md#loop-guard)
-   **Hypothesis:** enabling `LoopGuardConfig.enabled` on `planner_executor` reduces wasted turns on the near-miss slice — tasks where the original `planner_executor` leg hit the turn budget without progress — by ≥5pp.
-   **Slice:** `near-miss` — tasks where `planner_executor` hit `n_turns=30` in `tb2-baseline-20260417-234913` (TBD: extract exact list during design phase; expect ~15-25 tasks). `n_tasks/leg ≈ 20`, `n_trials/leg ≈ 40` (with `n_attempts=2`).
-   **Legs:** 2-leg paired ablation. Leg A: `planner_executor` (loop-guard off — byte-identical to current YAML). Leg B: `planner_executor` (loop-guard on). **Trunk-anchor caveat:** the design phase MUST verify that `tree_ops.evaluate` accepts a non-trunk anchor without complaining; if it doesn't, add `basic` (trunk) as Leg C purely so the comparator has a trunk reference.
-   **Repetitions:** `paired-double` (n_attempts=2) — loop-guard's nudges fire stochastically on observed empty turns / repeated calls; cell variance is its core characteristic.
-   **Control:** `fresh` — required by selection bias on the near-miss slice.
-   **Why third:** stronger evidence after #1 tells us if budget alone explains near-miss failures, and after #2 confirms the planner_executor predicate.
-   **Depends on:** `tb2-baseline-full-sweep`, `extended-budget-paired-on-trunk` (informative but not blocking)
-   **Cost:** ~$5-10 (2 legs × ~40 trials × ~$0.07/trial; planner_executor cost band).

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
-   **When to run:** before or alongside `planner-executor-cluster-confirmation` to calibrate how much we should trust the cluster-level deltas it produces.

#### loop-guard-on-basic-near-miss

-   **Hypothesis:** tests whether the 64/84 repeated_failed_command or timeout_no_recovery failures in extended-budget-paired-on-trunk are recoverable by steering basic off no-progress loops instead of buying more turns
-   **Source:** lab-reflect-and-plan@2026-04-23
-   **Cost:** ~$3-5

#### stronger-model-baseline

-   **Hypothesis:** tests whether the 24/28 all-leg failures that ignored extra budget are capability-bound rather than budget-bound by swapping trunk basic to gemini-2.5-pro on the same near-miss slice
-   **Source:** lab-reflect-and-plan@2026-04-23
-   **Cost:** ~$10-20

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

## Done

### tb2-baseline-full-sweep

-   **Idea:** baseline snapshot
-   **Hypothesis:** the post-reset baseline runs cleanly across all of `terminal-bench@2.0` and produces a real per-agent pass-rate distribution to anchor every future ablation.
-   **Plan:** `uv run exec tb2-baseline` (no `--profile`); 3 legs × ~89 tasks. Launch via `scripts/exp/start.sh exec tb2-baseline` so it survives an SSH disconnect. Watch `events.jsonl` for 429s on the ~30 RPM Gemini cap and adjust `n_concurrent` if needed.
-   **Cost:** ~$15-25, a few hours wall-clock.
