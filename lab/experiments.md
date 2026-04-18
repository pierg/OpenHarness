# Experiments

> **Reset on 2026-04-17.** Pre-reset entries (a Phase 0–4 baseline
> snapshot, a `baseline-reset` housekeeping note, and a reflection
> context-blowup smoke run) were cleared because none reflected the
> post-reset baseline. The reflection-blowup rationale is preserved
> as [`reflection-context-compaction`](ideas.md#reflection-context-compaction).
> The first real entry will be the full TB2 sweep queued in
> [`roadmap.md`](roadmap.md).

## 2026-04-17 — tb2-baseline-full-sweep

-   **Hypothesis:** the post-reset baseline runs cleanly across all of `terminal-bench@2.0` and produces a real per-agent pass-rate distribution to anchor every future ablation.
-   **Variant:** Current post-reset state (3.1-flash-lite-preview)
-   **Run:** `runs/experiments/tb2-baseline-20260417-234913`

### Results

| Leg | Trials | Passed | Failed | Errored | Pass rate | Total tokens | Cost (USD) |
|-----|-------:|-------:|-------:|--------:|----------:|-------------:|-----------:|
| basic | 89 | 20 | 64 | 5 | 22.5% | 22,092,802 | $5.84 |
| planner_executor | 89 | 10 | 32 | 47 | 11.2% | 27,684,929 | $7.22 |
| react | 89 | 12 | 54 | 23 | 13.5% | 85,206,702 | $22.64 |

### Notes

- The experiment fully ran across all 89 `terminal-bench@2.0` tasks!
- The `basic` agent emerged as the strongest baseline with a **22.5% pass rate** and minimal errors (only 5 infrastructure errors out of 89).
- The `react` agent was the most expensive ($22.64) and slowest due to large prompts but performed worse (13.5% pass rate) with 23 setup/agent errors.
- The `planner_executor` struggled the most, passing only 11.2% and hitting heavy agent execution errors (47 errors, primarily agent-side).

### Decision

The `basic` agent serves as our solid ground-truth baseline. We have successfully proven the viability of `n_concurrent: 30` with `gemini-3.1-flash-lite-preview` on this VM. We can now proceed to explore and ablate improvements off of the `basic` agent in subsequent experiments.
