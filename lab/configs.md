# Configs

## Trunk

-   **Agent:** [`trunk`](../src/openharness/agents/configs/trunk.yaml) (alias of [`basic`](../src/openharness/agents/configs/basic.yaml))
-   **Why:** highest pass-rate at lowest $/pass on tb2-baseline (22.5%, $5.84, 5 errors / 89 trials).
-   **Anchored by:** [`tb2-baseline-full-sweep`](experiments.md#2026-04-17--tb2-baseline-full-sweep)

## Branches

Branch predicates are operator/runtime-admissible hints, not
benchmark-oracle routers. Offline `task_features` labels may justify
why a branch is worth retesting, but production selection must be
derived from the task instruction/workspace or chosen manually by the
operator.

| ID | Mutation vs trunk | Use-when predicate | Last verified |
|----|-------------------|--------------------|---------------|
| `planner_executor` | adds explicit planner subagent on top of trunk | manual/runtime-observable: task appears to require explicit multi-step planning across certificates, system administration, or Python data workflows; do not route by offline `task_features` alone | tb2-baseline-full-sweep |
| `react` | scratchpad-driven re-plan loop on top of trunk | tentative/manual: task appears to benefit from repeated reason-action-observation cycles; needs targeted re-test before promotion | tb2-baseline-full-sweep |

## Rejected

| ID | Reason | Evidence |
|----|--------|----------|
| `reflection` | context blow-up: ≥ 500k input tokens / trial on the smoke slice. Re-add only with [`reflection-context-compaction`](ideas.md#reflection-context-compaction). | [`reflection-context-compaction`](ideas.md#reflection-context-compaction) (idea — not yet on the roadmap; needs a meaningful slice, not a smoke run) |
| `basic_model_router` | Invalid benchmark-oracle branch: routed by exact Terminal-Bench task names instead of runtime-observable task instruction/workspace signals. Removed from runnable agent configs; keep only as diagnostic evidence. | [model-escalation-router-hard-clusters](experiments.md#2026-04-25--model-escalation-router-hard-clusters) |
| `extended-budget-basic` | Longer-turn `basic` variants did not produce a meaningful pass-rate lift on the near-miss slice; one narrow task win did not justify promotion. | [extended-budget-paired-on-trunk](experiments.md#2026-04-23--extended-budget-paired-on-trunk) |
| `basic_loop_guard` | Δ pass-rate = -2.2pp; Δ $/pass = +99%; cost spike ≥ 50%; no positive cluster. | (see journal) |

## Proposed

| ID | Sketch | Linked idea |
|----|--------|-------------|
| [`loop-guard`](../components/loop-guard.yaml) | detects no-progress turns, steers toward recovery before the turn budget runs out | [`loop-guard`](ideas.md#loop-guard) |
