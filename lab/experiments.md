# Experiments

## 2026-04-22 — reflection-context-compaction-smoke

-   **Type:** paired-ablation
-   **Trunk at run-time:** [`trunk`](../src/openharness/agents/configs/trunk.yaml)
-   **Hypothesis:** truncating tool stdout above some threshold lets `reflection` complete on the smoke slice within wall-clock and at <500 k input tokens per trial.
-   **Run:** [`runs/experiments/reflection-context-compaction-smoke-20260422-213746`](../runs/experiments/reflection-context-compaction-smoke-20260422-213746)
-   **Branch:** `lab/reflection-context-compaction-smoke`

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `reflection_compacted` | `reflection_context_compaction` | 2 | 1 | 1 | 50.0% | $1.79 |
| `reflection_control` | `reflection` | 2 | 1 | 1 | 50.0% | $1.68 |
### Mutation impact
-   **Best leg:** `reflection_compacted` (50.0%, $1.79)
-   **Worst leg:** `reflection_control` (50.0%, $1.68)
-   **Spread:** +0.0 pp
-   _(experiment-critic JSON missing a `mutation_impact` field; this is a DB-only fallback.)_
### Failure modes

_(pending)_

### Tree effect
-   **Verdict:** **No-op** — recorded for trend analysis
-   **Target:** `reflection`
-   **Pair:** trunk leg `reflection_compacted` vs mutation `reflection_control`
-   **Δ pass-rate:** +0.00 pp
-   **Δ $/pass:** -6.1%
-   **Confidence:** 0.00
-   **Rationale:** Inconclusive: Δ pass-rate = +0.0pp (trunk 50.0% vs mutation 50.0%); 0 positive cluster(s) (threshold 2); Δ $/pass = -6%.

| Cluster | trunk pass | mut pass | Δ pp |
|---------|-----------:|---------:|-----:|
| `bash_pipeline` | 1/1 | 1/1 | +0.0 |
| `regex_programming` | 0/1 | 0/1 | +0.0 |
### Linked follow-ups

_(pending)_

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
-   [`loop-guard-on-planner-executor`](ideas.md#loop-guard-on-planner-executor) — auto-proposed; depends on `loop-guard-tb2-paired` landing first.
-   [`tool-result-summariser-paired`](ideas.md#tool-result-summariser-paired) — auto-proposed; sibling of `reflection-context-compaction`.
