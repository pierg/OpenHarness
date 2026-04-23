# Ideas

## Auto-proposed

#### loop-guard-on-planner-executor

-   **Motivation:** Once loop-guard lands as a runtime atom on trunk, the same mechanism is more likely to help on planner_executor (which adds a planning hop that can also stall) than on basic alone. Composition test, not yet runnable.
-   **Sketch:** Paired ablation on planner_executor only: leg A current YAML; leg B same YAML with loop-guard runtime atom enabled. Run on the three positive clusters from the planner_executor branch (security_certificates, system_administration, python_data) plus a same-size negative-cluster control.
-   **Auto-proposed by:** lab-reflect-and-plan@2026-04-18

#### tool-result-summariser-paired

-   **Motivation:** Sibling of context-compaction but cheaper to test in isolation: rather than truncating raw tool stdout, inject an LLM-generated short summary of any tool result above K tokens before the next turn. Could rescue reflection (currently rejected) without the brittle line-count heuristic of context-compaction.
-   **Sketch:** Implement behind an AgentConfig flag (off by default). Paired ablation on basic (cheapest harness) with the flag on/off on a slice biased toward tasks where tool stdout exceeded 50 lines in tb2-baseline-full-sweep. Re-test on reflection only if the basic ablation is positive.
-   **Auto-proposed by:** lab-reflect-and-plan@2026-04-18

## Proposed

### Architecture

#### planner-executor-critic

-   **Motivation:** The reflection critic catches premature completions for the simple `reflection` worker; it might do the same for a richer `planner_executor` worker.
-   **Sketch:** Compose the existing `reflection` architecture with a `planner_executor` worker (pure YAML composition, no new code) and a strict critic prompt that fails reports lacking concrete verification.

#### planner-rerank

-   **Motivation:** First plan the planner produces is often mediocre.
-   **Sketch:** Generate N plans, rerank with a small judge model (same family, smaller size), execute top-1.

### Runtime

#### loop-guard

-   **Motivation:** Some Gemini variants emit empty assistant turns or repeat the same tool call indefinitely; without intervention the agent silently exhausts its budget on no progress.
-   **Sketch:** Runtime mechanism (already in `src/openharness/engine/loop_guard.py`, off by default) that detects empty turns and identical tool-call streaks, injects a short steering nudge, and gives up after a small budget.

#### tool-result-summariser

-   **Motivation:** Large tool results eat context and often drown useful signal.
-   **Sketch:** After any tool result above K tokens, inject a short summary before the next turn; keep the full result in the trace but hide it from the model.

#### reflection-context-compaction

-   **Motivation:** Reflection's worker conversation grows quadratically in tokens because every turn re-sends the full history including raw `bash`/`grep` stdout. A pre-reset smoke run saw both reflection trials hit the 900 s harbor wall-clock at 6.4 M input tokens / $0.67 each — long before the 30-turn worker budget could fire. Until this is fixed, `reflection` is excluded from `experiments/tb2-baseline.yaml`.
-   **Sketch:** Truncate or summarise tool outputs above some threshold (e.g. keep first/last 50 lines, replace middle with a `<truncated N lines>` marker) before they re-enter the next turn's history. Opt-in via an `AgentConfig` flag so the basic loop stays unaffected until measured.

#### executor-bash-timeout-aware-retry

-   **Motivation:** Long-running commands (builds, downloads) hit the bash tool timeout and we have no recovery path.
-   **Sketch:** Detect timeouts, relaunch the command in background, poll status. Or expose a `run_in_background=True` flag on the bash tool.

### Tools

#### grounded-planner-tools

-   **Motivation:** The `planner_executor` planner is currently wired with read-only tools (`read_file`, `glob`, `grep`) but no experiment has measured whether removing them hurts — or whether the planner still hallucinates `tool_code` blocks anyway.
-   **Sketch:** Paired ablation — planner with read-only tools (current default) vs planner with no tools. Same prompt, same model.

#### web-tools

-   **Motivation:** Some tasks need external documentation, source tarballs, or upstream references that aren't in the sandbox image.
-   **Sketch:** Add `web_fetch` + `web_search` tools to the `basic` agent and to the `planner_executor` planner/executor subagents.

### Memory

#### skill-memory

-   **Motivation:** Agents re-derive the same command sequences every run.
-   **Sketch:** Persist successful command sequences to a task-local `skills/` directory that the planner reads on its first turn.

#### episodic-memory

-   **Motivation:** Cross-task patterns (e.g. "how to read a Dockerfile before editing") aren't reused.
-   **Sketch:** Indexed store of post-run reflections keyed by task signature; planner pulls top-k before producing a plan.

### Framework

#### cluster-combined-slice-shape

-   **Motivation:** `tb2` clusters are tiny (42 of 56 categories have n=1; only `python_data` and `python_ml` clear the §6 floor as single-cluster slices at `n_attempts=1`). The current `cluster: <names>` shape silently treats multi-cluster lists as separate slices when it should treat them as one combined slice, making cluster-based confirmations awkward.
-   **Sketch:** Add `cluster_combined: <names>` to `## Slice` shapes (METHODOLOGY §2). Spec-side, this resolves to a single `task_filter:` over the union; verdict-side, `tree_ops.evaluate` reports both per-cluster Δpp (current behaviour) AND a combined Δpp on the union (new). Lets `planner-executor-cluster-confirmation` declare `cluster_combined: python_data, system_administration, security_certificates` and clear the floor at `n_attempts=2` (n=22/leg) without per-cluster gymnastics.
-   **Referenced from:** METHODOLOGY §2, Appendix B.

#### adaptive-repetitions

-   **Motivation:** Blanket `paired-double` (n_attempts=2) doubles cost on every cell, even cells where leg A passes 1/1 and leg B passes 1/1 (no information gained from re-rolling). On cells where legs disagree (1/1 vs 0/1) or where pass-rate falls in [0.3, 0.7], a third or fourth re-roll is high-value.
-   **Sketch:** Add a "phase 3.5" between `phase_run` and `phase_critique` that examines per-cell results from phase 3, identifies borderline cells per the rule above, and queues re-runs (capped by `max=k` from the spec) on just those cells. Target cost: ~1.2-1.4× single-shot vs 2× for paired-double. Implementation: extend the spec to declare `adaptive: max=3`; phase 3.5 runs `uv run exec <spec> --profile retop --tasks <list>`; ingest merges the new trials into the same `instance_id`.
-   **Referenced from:** METHODOLOGY §4.

#### historical-control-shape

-   **Motivation:** Every experiment currently re-runs its control fresh. For runtime-flag ablations on byte-identical existing branches (e.g. `loop-guard` on `planner_executor`), the control trials already exist in `runs/lab/trials.duckdb` from a prior run. Borrowing them cuts spend ~50% AND wall-clock ~50% on those experiments.
-   **Sketch:** Add `control: historical: <instance_id>/<leg_id>` and `control: historical+replay: ...` modes to `## Slice` (METHODOLOGY §5). Implement phase blocks the run unless drift guards pass: control config hash, bench version pin, verifier hash, model pin (vendor + checkpoint), and `n_attempts` all byte-match. Trunk-graduation invalidates historical references to the old trunk (DB marks them stale; design phase rejects them). The `+replay` variant adds a third leg that re-runs the borrowed config on the slice to bound regression-to-the-mean noise (recommended for derived slices like `near-miss`).
-   **Referenced from:** METHODOLOGY §5.

#### graduate-replication-gate

-   **Motivation:** A `Graduate` TreeDiff swaps trunk — the highest-stakes mutation in the lab. Today `lab graduate confirm <slug>` is the sole gate (human approval, no replication required). One bad luck run could land a regressing config on trunk, contaminating every downstream experiment until detected.
-   **Sketch:** `lab graduate confirm <slug>` requires one full-experiment replication on the same slice with a fresh random seed before the trunk swap commits. Replication's verdict must agree (`Graduate` or `AddBranch` with Δ ≥ +3pp) for the swap to honor. Adds ~1× experiment cost per graduate, but graduates are rare (≤ 1/month at current cadence) and trunk integrity is worth it. Implementation: extend `lab graduate confirm` to spawn a fresh phase-3 run with a different `seed:` value, ingest, evaluate, and gate the swap on the second verdict.
-   **Referenced from:** METHODOLOGY §7.

## Trying

#### extended-budget

-   **Motivation:** The 30/8192 baseline sometimes hits the agent-phase timeout on heavier `build-*` and `git-*` tasks. Raising to 60/16384 might convert near-misses into passes.
-   **Sketch:** Bump `defaults.max_turns` to 60 and `max_tokens` to 16384 in `experiments/tb2-baseline.yaml` (and matching agent configs).

## Graduated

_(none)_

## Rejected

_(none)_
