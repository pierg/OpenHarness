# Configs

## Trunk

-   **Agent:** [`trunk`](../src/openharness/agents/configs/trunk.yaml) (alias of [`basic`](../src/openharness/agents/configs/basic.yaml))
-   **Why:** highest pass-rate at lowest $/pass on tb2-baseline (22.5%, $5.84, 5 errors / 89 trials).
-   **Anchored by:** [`tb2-baseline-full-sweep`](experiments.md#2026-04-17--tb2-baseline-full-sweep)

## Branches

| ID | Mutation vs trunk | Use-when predicate | Last verified |
|----|-------------------|--------------------|---------------|
| `planner_executor` | adds explicit planner subagent on top of trunk | `task_features.category ∈ {security_certificates, system_administration, python_data}` | tb2-baseline-full-sweep |
| `react` | scratchpad-driven re-plan loop on top of trunk | (tentative; one positive cluster on tb2-baseline; needs targeted re-test) | tb2-baseline-full-sweep |

## Rejected

| ID | Reason | Evidence |
|----|--------|----------|
| `reflection` | context blow-up: ≥ 500k input tokens / trial on the smoke slice. Re-add only with [`reflection-context-compaction`](ideas.md#reflection-context-compaction). | [`reflection-context-compaction`](ideas.md#reflection-context-compaction) (idea — not yet on the roadmap; needs a meaningful slice, not a smoke run) |
| `basic` | Δ pass-rate = -3.6pp; Δ $/pass = -85%; no positive cluster. (also: basic_60_16384 → no_op: Inconclusive: Δ pass-rate = +0.0pp (trunk 14.3% vs mutation 14.3%); 0 positive cluster(s) (threshold 2); Δ $/ | /home/pier_ridgesecurity_ai/OpenHarness/runs/experiments/extended-budget-paired-on-trunk-20260423-184410/critic/experiment-critic.json, /home/pier_ridgesecurity_ai/OpenHarness/runs/experiments/extended-budget-paired-on-trunk-20260423-184410/critic/comparisons |

## Proposed

| ID | Sketch | Linked idea |
|----|--------|-------------|
| [`loop-guard`](../components/loop-guard.yaml) | detects no-progress turns, steers toward recovery before the turn budget runs out | [`loop-guard`](ideas.md#loop-guard) |
