# Roadmap

## Up next

> **Methodology contract.** Every entry below declares its
> **Slice** (METHODOLOGY §2), **Legs** (§3), **Repetitions** (§4),
> and **Control** (§5) explicitly so the design phase can be
> audited before code is written. The implement phase always runs
> a small `--profile smoke` exec for wiring validation; the run
> phase always runs the full slice. There are no separate `-smoke`
> / `-full-sweep` roadmap entries.

### tb2-gemini3-model-baseline

-   **Idea:** refresh the trunk model baseline before spending more daemon budget on component ablations.
-   **Hypothesis:** The current trunk score is partly model-bound: replacing `gemini-3.1-flash-lite-preview` with the stronger Gemini 3 Flash / 3.1 Pro coding models on the same `basic` harness will raise full-suite pass rate enough to change which runtime and prompt mechanisms are worth pursuing next.
-   **Slice:** full `terminal-bench@2.0` task set used by `tb2-baseline-full-sweep` (currently 89 tasks). Keep the same task filter, verifier behavior, timeout budget, and concurrency policy unless the design phase discovers a hard provider quota blocker and records the exact blocker before refusing.
-   **Legs:** 3-leg model-only baseline. Leg A: trunk `basic` with `gemini-3.1-flash-lite-preview`. Leg B: cloned `basic` with `gemini-3-flash-preview` (official Flash preview text model). Leg C: cloned `basic` with `gemini-3.1-pro-preview`. One axis only: model ID.
-   **Repetitions:** `single-shot` because the slice is broad and the mechanism is a pure provider/model swap.
-   **Control:** `fresh`.
-   **Why first:** every completed component experiment is being interpreted against the Lite baseline. If Flash or Pro materially lifts the baseline, the daemon should reprioritize follow-ups around residual failures from the stronger trunk instead of overfitting guardrails to Lite-specific behavior.
-   **Depends on:** none
-   **Cost:** smoke-gated; reserve ~$40-120, with Pro expected to dominate spend.

### artifact-first-output-policy

-   **Idea:** [`artifact-first-output-policy`](ideas.md#artifact-first-output-policy)
-   **Hypothesis:** A prompt/runtime policy that forces early creation or incremental updating of the task's required output artifact will recover file-output failures where the agent made partial progress but never left the verifier-consumable result in place.
-   **Slice:** file-output-heavy all-leg failures and near-misses from prior runs, prioritizing tasks tagged or inferred as `creates_new_file`, `single_output_file`, or artifact-portability sensitive. Include known failures such as `db-wal-recovery`, `password-recovery`, and `write-compressor` if they still reproduce on the chosen trunk model; floor §6 requires at least 10 trials per leg.
-   **Legs:** 2-leg paired ablation. Leg A: chosen trunk `basic` after `tb2-gemini3-model-baseline`. Leg B: same trunk plus artifact-first output policy. One axis only: output-artifact policy on or off.
-   **Repetitions:** `paired-double` because the slice is narrow and prior failures include stochastic late-task abandonment.
-   **Control:** `fresh`.
-   **Why second:** prior critiques repeatedly show partial progress without a durable answer artifact; this is a direct harness-facing mechanism after the model floor is known.
-   **Depends on:** `tb2-gemini3-model-baseline`
-   **Cost:** ~$5-10

### portable-artifact-clean-env-gate

-   **Idea:** [`portable-artifact-clean-env-gate`](ideas.md#portable-artifact-clean-env-gate)
-   **Hypothesis:** Adding a clean-environment / portable-artifact completion gate will reduce `hallucinated_success`, sample-only validation, and environment-mutation failures without changing the agent architecture.
-   **Slice:** clean-env-sensitive failures from prior runs, especially tasks where the agent passed a local/sample check but failed the verifier or left non-portable state. Prefer `python_packaging`, `python_data`, and artifact-heavy tasks such as `openssl-selfsigned-cert`, `reshard-c4-data`, and `raman-fitting`; floor §6 requires at least 10 trials per leg.
-   **Legs:** 2-leg paired ablation. Leg A: chosen trunk `basic` after `tb2-gemini3-model-baseline`. Leg B: same trunk plus portable artifact / clean verifier gate. One axis only: completion gate on or off.
-   **Repetitions:** `paired-double`.
-   **Control:** `fresh`.
-   **Why third:** `hallucinated_success` and invalid local validation remain high-frequency critic labels; this tests a verifier-aligned guardrail before adding more architecture complexity.
-   **Depends on:** `tb2-gemini3-model-baseline`
-   **Cost:** ~$5-10

### timeout-aware-retry-needs-network-confirmation

-   **Idea:** [`timeout-aware-retry-needs-network-confirmation`](ideas.md#timeout-aware-retry-needs-network-confirmation)
-   **Hypothesis:** The timeout-aware retry branch needs a verdict-bearing rerun on the intended network-dependent, high-env-complexity slice; the smoke run tied control at 2/4 passes per leg and is below the evidence floor, so the needs_network timeout hypothesis remains open.
-   **Slice:** verdict-bearing full slice from `timeout-aware-retry-on-needs-network`: network-dependent / `extra.needs_network = true` tasks with `high_env_complexity` and recent `repeated_failed_command` or `timeout_no_recovery` failures. Prefer the existing 18-task materialized list from the spec; floor §6 requires at least 10 trials per leg.
-   **Legs:** 2-leg paired ablation. Leg A: chosen trunk `basic` after `tb2-gemini3-model-baseline`. Leg B: same trunk plus `basic_timeout_aware_retry`. One axis only: executor timeout-aware retry / background polling on or off.
-   **Repetitions:** `paired-double` because the smoke run was under-powered and the recovery path depends on runtime timing.
-   **Control:** `fresh`.
-   **Why fourth:** the smoke run proved wiring but not value. Keep the confirmation, but run it after the model baseline so timeout recovery is measured against the trunk we actually intend to operate.
-   **Depends on:** `tb2-gemini3-model-baseline`, `timeout-aware-retry-on-needs-network`
-   **Cost:** ~$4-7

### toolchain-fallback-playbooks-on-c-build

-   **Idea:** [`toolchain-fallback-playbooks-on-c-build`](ideas.md#toolchain-fallback-playbooks-on-c-build)
-   **Hypothesis:** C/build and bootstrap failures are not solved by more turns or generic loop guards; explicit toolchain fallback playbooks should reduce repeated failed commands on build-system and dependency-resolution tasks.
-   **Slice:** `c_build` plus closely related network/toolchain/bootstrap failures where critiques logged `repeated_failed_command`, `wrong_tool_family`, or `timeout_no_recovery`. Use the prior `c_build` failures first and expand with task-feature matches until floor §6 clears at at least 10 trials per leg.
-   **Legs:** 2-leg paired ablation. Leg A: chosen trunk `basic` after `tb2-gemini3-model-baseline`. Leg B: same trunk plus toolchain fallback playbooks. One axis only: build/toolchain playbook guidance on or off.
-   **Repetitions:** `paired-double`.
-   **Control:** `fresh`.
-   **Why fifth:** `c_build` is one of the clearest low-pass, repeated-failure clusters across completed runs, but the right trunk model should be chosen before building specialized playbooks around it.
-   **Depends on:** `tb2-gemini3-model-baseline`
-   **Cost:** ~$5-10

### Suggested

_(none)_

## Done

### timeout-aware-retry-on-needs-network

-   **Idea:** [`executor-bash-timeout-aware-retry`](ideas.md#executor-bash-timeout-aware-retry)
-   **Hypothesis:** timeout-aware retry / background polling recovers a meaningful share of the `needs_network` + `high_env_complexity` failures that currently collapse into repeated command loops or unrecovered bash timeouts.
-   **Slice:** derived `needs_network + high_env_complexity` slice from the current bench, restricted to tasks whose recent failed trials skewed toward `repeated_failed_command` or `timeout_no_recovery`. The implement phase MUST resolve the predicate from recorded artefacts and encode the final task list in `task_filter.include_tasks:`; floor §6 requires at least 5 tasks.
-   **Legs:** 2-leg paired ablation. Leg A: trunk `basic`. Leg B: `basic` + executor timeout-aware retry / background polling. One axis only: timeout recovery path on/off.
-   **Repetitions:** `paired-double` (n_attempts=2) — the recovery path is runtime-sensitive, and the derived slice is composed of unstable long-running tasks where single-shot noise would be hard to interpret.
-   **Control:** `fresh`.
-   **Why first:** four completed experiments still concentrate failures in repeated command loops and unrecovered timeouts, while `planner-schema-guard-paired` only reduced spend on the planner slice without recovering score. This is now the strongest trunk-facing mechanism question with cross-experiment support.
-   **Cost:** ~$5-8

-   **Ran:** [runs/experiments/timeout-aware-retry-on-needs-network-smoke-20260424-193153](../runs/experiments/timeout-aware-retry-on-needs-network-smoke-20260424-193153)
-   **Outcome:** no_op: smoke run tied at 2/4 passes per leg and fell below the evidence floor; run the full network-heavy slice before drawing a verdict.

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

### stronger-model-baseline

-   **Status:** superseded by [`tb2-gemini3-model-baseline`](roadmap.md#tb2-gemini3-model-baseline).
-   **Hypothesis:** originally proposed swapping trunk basic to `gemini-2.5-pro` on a near-miss slice to test whether failures were capability-bound.
-   **Why deferred:** stale model target and too narrow for the current decision. The daemon now needs a current Gemini 3 full-suite baseline before interpreting more component ablations.
-   **Source:** lab-reflect-and-plan@2026-04-23, deferred 2026-04-24.

### planner-executor-cluster-confirmation

-   **Idea:** confirms the live `AddBranch` from `tb2-baseline-full-sweep`, but only after repairing the planner schema failure mode; still folds in the `grounded-planner-tools` ablation as Leg C for marginal cost.
-   **Hypothesis:** (a) `planner_executor` with the schema guard still beats trunk on `{python_data, system_administration, security_certificates}` with adequate `n`; (b) the planner subagent's read-only tools materially contribute to any recovered win, so removing them on Leg C should hurt.
-   **Slice:** same `cluster_combined: python_data, system_administration, security_certificates` slice as `planner-schema-guard-paired`. Current counts are 11 tasks total, so with `n_attempts=2`, `n_trials/leg = 22`.
-   **Legs:** 3-leg multi-arm (METHODOLOGY §3 — two questions share one slice). Leg A: trunk `basic`. Leg B: `planner_executor` + schema guard. Leg C: Leg B plus planner subagent `tools: []`. Each pairwise contrast differs in exactly one axis.
-   **Repetitions:** `paired-double` (n_attempts=2) — small slice, planner behavior is stochastic, and the original add-branch evidence rested on n=1/3/7.
-   **Control:** `fresh`.
-   **Why deferred:** `planner-schema-guard-paired` matched control on score and only lowered cost, so planner confirmation no longer deserves front-of-queue budget before the trunk model floor is refreshed.
-   **Depends on:** `planner-schema-guard-paired`
-   **Cost:** ~$5-8 (3 legs × 22 trials × ~$0.07-0.10/trial).

### trunk-noise-floor-calibration

-   **Hypothesis:** measures pure stochastic swing on the planner-executor confirmation slice by running `basic` twice on the same 11-task `cluster_combined` slice with `n_attempts=2`.
-   **Slice:** same 11 tasks as `planner-executor-cluster-confirmation`. `n_tasks/leg=11`, `n_trials/leg=22`.
-   **Legs:** 2-leg. Leg A: `basic`. Leg B: `basic` (byte-identical). Both run independently.
-   **Repetitions:** `paired-double` (n_attempts=2).
-   **Control:** `fresh`.
-   **Why deferred:** useful only if planner-related cluster decisions return to the queue. Larger trunk-facing failure modes are higher-value right now.
-   **Source:** lab-reflect-and-plan@2026-04-22 (methodology revision), deferred 2026-04-24.
-   **Cost:** ~$2.

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
