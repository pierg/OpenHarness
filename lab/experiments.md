# Experiments

## 2026-04-24 — loop-guard-on-basic-near-miss

-   **Type:** paired-ablation
-   **Trunk at run-time:** [`trunk`](../src/openharness/agents/configs/trunk.yaml)
-   **Hypothesis:** enabling `LoopGuardConfig.enabled` on trunk `basic` recovers a meaningful share of the loop-heavy near-miss failures from `extended-budget-paired-on-trunk` by breaking repeated command / timeout spirals without the cost blow-up of longer budgets.
-   **Run:** [`runs/experiments/loop-guard-on-basic-near-miss-20260424-021810`](../runs/experiments/loop-guard-on-basic-near-miss-20260424-021810)
-   **Branch:** `lab/loop-guard-on-basic-near-miss`

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `basic` | `basic` | 46 | 2 | 44 | 4.3% | $4.01 |
| `basic_loop_guard` | `basic_loop_guard` | 46 | 1 | 45 | 2.2% | $3.98 |
### Mutation impact
-   **Best leg:** `basic` (4.3%, $4.01)
-   **Worst leg:** `basic_loop_guard` (2.2%, $3.98)
-   **Spread:** +2.2 pp
-   _(experiment-critic JSON missing a `mutation_impact` field; this is a DB-only fallback.)_
### Failure modes

_(pending)_

### Tree effect
-   **Verdict:** **Reject** — experiment outcome supports rejection
-   **Target:** `basic_loop_guard`
-   **Pair:** trunk leg `basic` vs mutation `basic_loop_guard`
-   **Δ pass-rate:** -2.17 pp
-   **Δ $/pass:** +98.9%
-   **Confidence:** 0.43
-   **Rationale:** Δ pass-rate = -2.2pp; Δ $/pass = +99%; cost spike ≥ 50%; no positive cluster.

| Cluster | trunk pass | mut pass | Δ pp |
|---------|-----------:|---------:|-----:|
| `c_build` | 1/6 | 0/6 | -16.7 |
| `binary_emulation` | 0/2 | 0/2 | +0.0 |
| `c_graphics` | 0/2 | 0/2 | +0.0 |
| `c_ml_inference` | 0/2 | 0/2 | +0.0 |
| `c_runtime_debugging` | 0/2 | 0/2 | +0.0 |
| `compression_reverse_engineering` | 0/2 | 0/2 | +0.0 |
| `coq_theorem_proving` | 0/2 | 0/2 | +0.0 |
| `corewars_redcode` | 0/2 | 0/2 | +0.0 |
### Linked follow-ups

_(pending)_

## 2026-04-23 — extended-budget-paired-on-trunk

-   **Type:** paired-ablation
-   **Trunk at run-time:** [`trunk`](../src/openharness/agents/configs/trunk.yaml)
-   **Hypothesis:** the 22.5% baseline is meaningfully budget-bound on the near-miss slice; raising `max_turns` from 30 → 60 → 120 (with `max_tokens` scaled 8192 → 16384 → 32768) lifts pass-rate by ≥10pp on tasks that pinned `n_turns=30` in `tb2-baseline-full-sweep`.
-   **Run:** [`runs/experiments/extended-budget-paired-on-trunk-20260423-184410`](../runs/experiments/extended-budget-paired-on-trunk-20260423-184410)
-   **Branch:** `lab/extended-budget-paired-on-trunk` — not opened (reject: verdict rejected by critique; head=`dd03751`)

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `basic_120_32768` | `basic` | 28 | 4 | 24 | 14.3% | $20.63 |
| `basic_30_8192` | `basic` | 28 | 3 | 25 | 10.7% | $2.28 |
| `basic_60_16384` | `basic` | 28 | 4 | 24 | 14.3% | $6.58 |
### Mutation impact
Relative to trunk `basic_30_8192` (10.7% pass, 3/28), both extended budgets improved to 14.3% (4/28), a +3.6 percentage-point gain driven entirely by the `scientific_computing` task `tune-mjcf`; `crack-7z-hash`, `headless-terminal`, and `pytorch-model-cli` already passed across legs and only changed on efficiency. The 60-turn/16k leg captured the full pass-rate gain at 2.9x trunk cost ($6.58 vs $2.28), while the 120-turn/32k leg added 0 extra percentage points over 60-turn and raised cost to 9.1x trunk ($20.63). The causal pattern is narrow: extra search budget helps evaluator-guided optimization, but on most tasks it just prolongs the same `repeated_failed_command` / `timeout_no_recovery` loops and increases `hallucinated_success`.
### Failure modes
-   **repeated_command_loops** (×64): 64/84 trials carried `repeated_failed_command` or `timeout_no_recovery`: the agent kept rerunning near-identical probes after a blocker instead of switching strategy.
-   **premature_abandonment** (×31): 31 trials were tagged `gave_up_too_early`, usually after the first missing-tool or hard-instance signal rather than after a verifier-grounded recovery attempt.
-   **wrong_tool_selection** (×23): 23 trials used the wrong tool family for the job, such as regex scraping or environment mutation where the task needed direct artifact generation, compilation, or evaluator-guided tuning.
-   **required_artifact_never_written** (×15): 15 trials made partial analytical progress but still never wrote the required output artifact, which is the decisive miss on tasks like `db-wal-recovery`, `password-recovery`, and `write-compressor`.
-   **insufficient_workspace_inspection** (×14): 14 trials were tagged `no_pre_edit_inspection`, with the agent locking onto regex-only or guessed-distribution plans before inspecting the real workspace and verifier contract.
-   **false_completion_after_partial_validation** (×6): 6 trials showed `hallucinated_success` or `partial_verification`: the agent treated a partial local check as completion even though the verifier still exercised missing paths.
### Tree effect
-   **Verdict:** **Reject** — auto-applied
-   **Target:** `basic`
-   **Pair:** trunk leg `basic_120_32768` vs mutation `basic_30_8192`
-   **Δ pass-rate:** -3.57 pp
-   **Δ $/pass:** -85.3%
-   **Confidence:** 0.71
-   **Rationale:** Δ pass-rate = -3.6pp; Δ $/pass = -85%; no positive cluster. (also: basic_60_16384 → no_op: Inconclusive: Δ pass-rate = +0.0pp (trunk 14.3% vs mutation 14.3%); 0 positive cluster(s) (threshold 2); Δ $/pass = -68%.)
-   **Evidence:** [`experiment-critic.json`](../runs/experiments/extended-budget-paired-on-trunk-20260423-184410/critic/experiment-critic.json), [`comparisons`](../runs/experiments/extended-budget-paired-on-trunk-20260423-184410/critic/comparisons), [`critic_summary.md`](../runs/experiments/extended-budget-paired-on-trunk-20260423-184410/results/critic_summary.md)

| Cluster | trunk pass | mut pass | Δ pp |
|---------|-----------:|---------:|-----:|
| `scientific_computing` | 1/1 | 0/1 | -100.0 |
| `binary_emulation` | 0/1 | 0/1 | +0.0 |
| `c_build` | 0/3 | 0/3 | +0.0 |
| `c_graphics` | 0/1 | 0/1 | +0.0 |
| `c_ml_inference` | 0/1 | 0/1 | +0.0 |
| `c_runtime_debugging` | 0/1 | 0/1 | +0.0 |
| `compression_reverse_engineering` | 0/1 | 0/1 | +0.0 |
| `coq_theorem_proving` | 0/1 | 0/1 | +0.0 |
### Linked follow-ups

-   [`loop-guard-on-basic-near-miss`](roadmap.md#loop-guard-on-basic-near-miss) — tests whether the dominant no-progress loops on this near-miss slice are recoverable without another budget increase.
-   [`stronger-model-baseline`](roadmap.md#stronger-model-baseline) — tests whether the broad wash after extra budget means the remaining failures are capability-bound rather than budget-bound.
-   [`artifact-first-output-policy`](ideas.md#artifact-first-output-policy) — abstract follow-up for the 15 trials that made partial progress but never wrote the required output artifact.

## 2026-04-17 — tb2-baseline-full-sweep

-   **Type:** broad-sweep
-   **Trunk at run-time:** none (this run anchored the trunk)
-   **Hypothesis:** the post-reset baseline runs cleanly across all of `terminal-bench@2.0` and produces a real per-agent pass-rate distribution to anchor every future ablation.
-   **Run:** [`runs/experiments/tb2-baseline-20260417-234913`](../runs/experiments/tb2-baseline-20260417-234913)

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `basic` | `basic` | 89 | 20 | 69 | 22.5% | $5.84 |
| `planner_executor` | `planner_executor` | 89 | 10 | 79 | 11.2% | $7.22 |
| `react` | `react` | 89 | 12 | 77 | 13.5% | $22.64 |

### Mutation impact
-   **Best leg:** `basic` (22.5%, $5.84)
-   **Worst leg:** `planner_executor` (11.2%, $7.22)
-   **Spread:** +11.2 pp
-   _(experiment-critic JSON missing a `mutation_impact` field; this is a DB-only fallback.)_

### Tree effect
-   **Verdict:** **Add branch** — auto-applied
-   **Target:** `planner_executor`
-   **Pair:** trunk leg `basic` vs mutation `planner_executor`
-   **Δ pass-rate:** -11.24 pp
-   **Δ $/pass:** +147.6%
-   **Confidence:** 1.00
-   **Rationale:** Trunk wins overall (Δ = -11.2pp), but mutation wins ≥ +5pp on 3 cluster(s): security_certificates (+100pp, n=1), system_administration (+33pp, n=3), python_data (+14pp, n=7). (also: react → no_op: Inconclusive: Δ pass-rate = -9.0pp (trunk 22.5% vs mutation 13.5%); 1 positive cluster(s) (threshold 2); Δ $/pass = +546%.)
-   **Use-when:** `{"any_of": [{"task_features.category": "security_certificates"}, {"task_features.category": "system_administration"}, {"task_features.category": "python_data"}], "derived_from": "tree_ops.evaluate cluster deltas"}`
-   **Evidence:** [`experiment-critic.json`](../runs/experiments/tb2-baseline-20260417-234913/critic/experiment-critic.json), [`comparisons`](../runs/experiments/tb2-baseline-20260417-234913/critic/comparisons), [`critic_summary.md`](../runs/experiments/tb2-baseline-20260417-234913/results/critic_summary.md)

| Cluster | trunk pass | mut pass | Δ pp |
|---------|-----------:|---------:|-----:|
| `bash_pipeline` | 1/1 | 0/1 | -100.0 |
| `git_service_deployment` | 1/1 | 0/1 | -100.0 |
| `interpreter_implementation` | 1/1 | 0/1 | -100.0 |
| `python_async` | 1/1 | 0/1 | -100.0 |
| `python_terminal_automation` | 1/1 | 0/1 | -100.0 |
| `security_certificates` | 0/1 | 1/1 | +100.0 |
| `vim_text_editing` | 1/1 | 0/1 | -100.0 |
| `binary_analysis` | 1/2 | 0/2 | -50.0 |

### Linked follow-ups
-   [`planner-executor-cluster-confirmation`](roadmap.md#planner-executor-cluster-confirmation) — focused re-test of the add_branch use-when on its 3 positive clusters with n>=5 (current verdict rests on n=1/3/7).
-   [`react-tentative-cluster-retest`](roadmap.md#react-tentative-cluster-retest) — flip react's no_op (1 positive cluster, threshold 2) into a clean verdict on its winning cluster.
-   [`extended-budget-paired-on-trunk`](roadmap.md#extended-budget-paired-on-trunk) — cheapest test of whether the 22.5% baseline is budget-bound vs capability-bound on a near-miss slice.
-   [`loop-guard-on-planner-executor`](ideas.md#loop-guard-on-planner-executor) — auto-proposed; depends on `loop-guard-paired-ablation` landing first.
-   [`tool-result-summariser-paired`](ideas.md#tool-result-summariser-paired) — auto-proposed; sibling of `reflection-context-compaction`.
