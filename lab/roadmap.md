# Roadmap

## Up next



### loop-guard-tb2-paired

-   **Idea:** [`loop-guard`](ideas.md#loop-guard)
-   **Hypothesis:** enabling the loop-guard runtime mechanism on the baseline cuts wasted turns on tasks where Gemini repeats tool calls or emits empty assistant turns.
-   **Plan:** paired ablation on the smoke slice first, then full sweep if smoke is positive. Vary `LoopGuardConfig.enabled` true vs false on `planner_executor`. Hold everything else constant.
-   **Depends on:** `tb2-baseline-full-sweep`
-   **Cost:** smoke ~$0.50; full ~$15-25 if it advances.

### grounded-planner-tools-ablation

-   **Idea:** [`grounded-planner-tools`](ideas.md#grounded-planner-tools)
-   **Hypothesis:** the read-only planner tools currently wired into `planner_executor.yaml` actually move the pass rate vs a tools-less planner.
-   **Plan:** paired ablation on `planner_executor` only. Leg A: current YAML. Leg B: planner subagent with `tools: []` plus a prompt edit acknowledging the constraint. Smoke slice first.
-   **Depends on:** `tb2-baseline-full-sweep`
-   **Cost:** smoke ~$0.20; full ~$10-15.

### reflection-context-compaction-smoke

-   **Idea:** [`reflection-context-compaction`](ideas.md#reflection-context-compaction)
-   **Hypothesis:** truncating tool stdout above some threshold lets `reflection` complete on the smoke slice within wall-clock and at <500 k input tokens per trial.
-   **Plan:** implement opt-in compaction behind an `AgentConfig` flag, then `exec rerun <latest-smoke-instance> -l reflection` on the smoke slice. If green, add `reflection` back to `experiments/tb2-baseline.yaml`'s `agents:` list and rerun the full sweep with the compaction default flipped on.
-   **Depends on:** `tb2-baseline-full-sweep`
-   **Cost:** smoke ~$0.50.

### stronger-model-baseline

-   **Idea:** baseline snapshot
-   **Hypothesis:** running the same baseline on a stronger Gemini SKU on a small slice tells us how much of the current pass-rate gap is "agent too weak" vs "model too weak".
-   **Plan:** custom small experiment YAML, same agents, stronger model (e.g. `gemini-2.5-pro`), 5–10 task slice biased toward tasks the baseline failed.
-   **Depends on:** `tb2-baseline-full-sweep`
-   **Cost:** ~$5-10.

### Suggested

#### planner-executor-cluster-confirmation

-   **Hypothesis:** A focused re-run of trunk vs planner_executor on the three positive clusters {security_certificates, system_administration, python_data} with n>=5 per cluster confirms the use-when predicate isn't an artefact of n=1/3/7 from tb2-baseline-full-sweep.
-   **Source:** lab-reflect-and-plan@2026-04-18
-   **Cost:** smoke ~$1.50; full not needed (this IS the focused full)

#### react-tentative-cluster-retest

-   **Hypothesis:** Re-running trunk vs react on react's one positive cluster from tb2-baseline-full-sweep with n>=5 flips the current no_op (1 positive cluster, threshold 2, Δ $/pass +546%) into either a clean add_branch or reject. React is currently a tentative branch we cannot route to with confidence.
-   **Source:** lab-reflect-and-plan@2026-04-18
-   **Cost:** smoke ~$2.00 (react burns ~$0.25/trial × ~5 trials × 2 legs)

#### extended-budget-paired-on-trunk

-   **Hypothesis:** Bumping max_turns 30→60 and max_tokens 8192→16384 on the trunk (basic) on a focused near-miss slice converts >=2 currently-failing tasks into passes, validating whether the 22.5% baseline is budget-bound or capability-bound. Cheapest possible test of the 'extended-budget' idea.
-   **Source:** lab-reflect-and-plan@2026-04-18
-   **Cost:** smoke ~$0.40 (small near-miss slice, 2 legs)

## Done




### tb2-baseline-full-sweep

-   **Idea:** baseline snapshot
-   **Hypothesis:** the post-reset baseline runs cleanly across all of `terminal-bench@2.0` and produces a real per-agent pass-rate distribution to anchor every future ablation.
-   **Plan:** `uv run exec tb2-baseline` (no `--profile`); 3 legs × ~89 tasks. Launch via `scripts/exp/start.sh exec tb2-baseline` so it survives an SSH disconnect. Watch `events.jsonl` for 429s on the ~30 RPM Gemini cap and adjust `n_concurrent` if needed.
-   **Cost:** ~$15-25, a few hours wall-clock.
