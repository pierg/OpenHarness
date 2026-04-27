# Configs

## Current best

-   **Agent:** [`basic`](../src/openharness/agents/configs/basic.yaml)
-   **Why:** baseline single-loop agent used as the simplest known-good harness; model overrides in experiment specs may outperform this YAML on particular benchmarks.
-   **Anchored by:** [`tb2-baseline-full-sweep`](experiments.md#2026-04-17--tb2-baseline-full-sweep)

## Rejected

| ID | Reason | Evidence |
|----|--------|----------|
| `planner_executor` | Removed as a standing alternative config in the simplified lab. Earlier cluster wins were benchmark-slice evidence, not a general runtime routing policy; re-test from `## Proposed` if a fresh idea needs explicit planning. | [`tb2-baseline-full-sweep`](experiments.md#2026-04-17--tb2-baseline-full-sweep), [`planner-schema-guard-paired`](experiments.md#2026-04-24--planner-schema-guard-paired) |
| `react` | Removed as a standing alternative config in the simplified lab. The tentative system-administration signal was too narrow to keep as an alternative harness. | [`tb2-baseline-full-sweep`](experiments.md#2026-04-17--tb2-baseline-full-sweep) |
| `reflection` | context blow-up: ≥ 500k input tokens / trial on the smoke slice. Re-add only with [`reflection-context-compaction`](ideas.md#reflection-context-compaction). | [`reflection-context-compaction`](ideas.md#reflection-context-compaction) (idea — not yet on the roadmap; needs a meaningful slice, not a smoke run) |
| `basic_model_router` | Invalid benchmark-oracle config: routed by exact Terminal-Bench task names instead of runtime-observable task instruction/workspace signals. Removed from runnable agent configs; keep only as measurement evidence. | [model-escalation-router-hard-clusters](experiments.md#2026-04-25--model-escalation-router-hard-clusters) |
| `extended-budget-basic` | Longer-turn `basic` variants did not produce a meaningful pass-rate lift on the near-miss slice; one narrow task win did not justify acceptance. | [extended-budget-paired-on-trunk](experiments.md#2026-04-23--extended-budget-paired-on-trunk) |
| `basic_loop_guard` | Δ pass-rate = -2.2pp; Δ $/pass = +99%; cost spike ≥ 50%; no positive cluster. | (see journal) |

## Proposed

| ID | Sketch | Linked idea |
|----|--------|-------------|
| [`loop-guard`](../components/loop-guard.yaml) | detects no-progress turns, steers toward recovery before the turn budget runs out | [`loop-guard`](ideas.md#loop-guard) |
