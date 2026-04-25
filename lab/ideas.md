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

#### artifact-first-output-policy

-   **Motivation:** 15 trials in extended-budget-paired-on-trunk made partial progress but never wrote the required output artifact, so the run spent budget without producing the thing the verifier actually scores.
-   **Sketch:** Add a runtime or prompt policy that creates or updates the required output artifact early and forces a verifier-aware recheck before more analysis. Test it first with a paired ablation on file-output-heavy tasks such as db-wal-recovery, password-recovery, and write-compressor.
-   **Auto-proposed by:** lab-reflect-and-plan@2026-04-23

#### loop-guard-on-creates-new-file

-   **Motivation:** Across 249 `creates_new_file` trials from the current two experiments, pass rate was only 14.5% and the dominant failure shape was no-progress looping (136 `repeated_failed_command`, 80 `timeout_no_recovery`) rather than clean verifier misses.
-   **Sketch:** Paired ablation on the existing basic agent with the loop-guard runtime atom off vs on, restricted to `creates_new_file` tasks that were all-leg failures or near-misses in tb2-baseline and extended-budget. Measure whether the guard converts loop-heavy runs into artifact-producing attempts without the broad cost increase seen from longer budgets.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-23

#### timeout-aware-retry-on-needs-network

-   **Motivation:** The `needs_network` slice is still broadly unresolved: across 177 trials the pass rate was 15.8%, with 102 `repeated_failed_command` and 62 `timeout_no_recovery` tags concentrated in service startup, download, and long-build tasks.
-   **Sketch:** Implement the executor timeout-aware retry / background-polling path as a paired ablation, then run it on a `needs_network` + `high_env_complexity` slice drawn from the current bench. This isolates whether the failures are mostly bash-timeout recoverability problems rather than generic model weakness or missing external tools.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-23

#### verifier-completion-gate-on-long-budget

-   **Motivation:** In extended-budget-paired-on-trunk, the 120-turn/32k leg matched the 60-turn/16k leg on passes, cost 3.1x more, and showed 4x as many `hallucinated_success` tags as the trunk budget, so more search mostly amplified false completion rather than finding new wins.
-   **Sketch:** Add a verifier-completion gate that blocks success claims until the required output paths or end-to-end checks have been revalidated, then replay the extended-budget slice with the gate enabled on the long-budget leg. The comparison should answer whether the remaining long-budget spend is rescuing real work or just prolonging premature success states.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-23

#### loop-guard-recovery-playbook

-   **Motivation:** Across 28 creates_new_file loop-guard trials and 42 high/medium-env trials, loop-guard cut spend roughly 49-61% and reduced hallucinated_success, but it produced zero decisive wins because failures shifted toward gave_up_too_early, wrong_tool_family, and unverified outputs.
-   **Sketch:** Extend loop-guard so a trigger runs a verifier-aware recovery playbook instead of a generic nudge: inspect README/tests, create or update the required artifact, run the narrow verifier command, then resume search. Test basic vs loop-guard vs loop-guard-plus-recovery-playbook on the current near-miss slice biased toward creates_new_file and single_output_file tasks.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-24

#### toolchain-fallback-playbooks-on-c-build

-   **Motivation:** Across 49 c_build trials the pass rate is only 8.2%, with repeated_failed_command on 40 trials and timeout_no_recovery on 24; loop-guard lowers cost but does not change scores, so the unresolved blocker is still toolchain/bootstrap strategy rather than control-flow alone.
-   **Sketch:** Add build-task fallbacks that inspect repo build docs first, switch to repo-local or package-manager alternatives when clang/gcc/opam/pip paths fail, and treat long bootstrap steps as background-poll work instead of repeated probing. Run a paired ablation on a c_build plus network_dependency slice, optionally alongside the existing timeout-aware-retry branch.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-24

#### planner-empty-glob-breaker

-   **Motivation:** Across the 22 `planner-schema-guard` trials, the mutation cut spend but produced zero decisive wins because planner-side empty `glob` loops and ungrounded filesystem guesses still dominated the failed tasks.
-   **Sketch:** Add a planner-side breaker that stops repeated `glob`/`grep` probes after repeated no-match results, forces README/verifier/task-local inspection, and retries plan generation with grounded paths. Measure `planner_executor_schema_guard` vs the breaker-enhanced variant on the existing `python_data + system_administration + security_certificates` slice.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-24

#### portable-artifact-clean-env-gate

-   **Motivation:** Across the current planner run and the earlier long-budget / loop-guard evidence, cost-saving components still leave the agent failing on hallucinated success, environment mutation, and sample-only validation instead of producing portable artifacts that survive the real verifier.
-   **Sketch:** Add a runtime completion gate that reruns a narrow clean-environment smoke check or verifier-aligned command against the produced artifacts before the final answer, and blocks success when the fix depends on undeclared packages or misses global constraints. Test it first on `openssl-selfsigned-cert`, `reshard-c4-data`, `raman-fitting`, and another clean-env-sensitive control task.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-24

#### timeout-aware-retry-needs-network-confirmation

-   **Motivation:** [low confidence: 4 active trials / 4 control trials] The timeout-aware retry smoke did not exercise the intended network-dependent slice, so it produced no >=5-trial component_perf row and leaves the needs_network hypothesis unanswered.
-   **Sketch:** Run a paired confirmation on at least 10 trials per side from network_dependent plus high_env_complexity tasks, including c_build/download-heavy cases, and compare timeout-aware retry against current basic on decisive wins and timeout_no_recovery tags.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-24

#### model-escalation-router-hard-clusters

-   **Motivation:** The Gemini 3 model baseline improved the full-suite control floor, but pro gained accuracy at a much higher cost while flash/lite stayed cheaper on many easy or tied tasks.
-   **Sketch:** Test a selective escalation policy: run the chosen cheap trunk model first, then escalate only on verifier failure or clusters where pro showed reliable lift such as c_build, python_ml, regex_programming, and binary_analysis. Compare pass rate, cost per pass, and whether escalation reduces gave_up_too_early/repeated_failed_command failures without making pro the default.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-25

#### runtime-guards-on-gemini3-floor

-   **Motivation:** [medium confidence; structural gap, 0 component trials on flash/pro] The measured runtime and planner guard rows all come from flash-lite component ablations, while the Gemini 3 model baseline raised the no-component control floor, so the zero-win guard conclusion may be model-floor dependent.
-   **Sketch:** Run a small paired confirmation on the selected Gemini 3 trunk model with current basic/planner controls versus loop-guard and planner-schema-guard on their strongest historical slices. Treat this as a model-floor interaction test, not a new component graduation attempt.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-25

#### targeted-router-score-win-confirmation

-   **Motivation:** [low confidence: 3 score-decided router wins] The hard-cluster router lost the aggregate but uniquely won extract-elf, mteb-retrieve, and regex-log, suggesting a narrow route surface may exist.
-   **Sketch:** Run a conservative router confirmation that escalates only binary_analysis, retrieval, and regex-log-like tasks while leaving flash as the default elsewhere. Require at least 10 trials per side or tag the result as exploratory if the slice cannot clear that floor.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-25

#### router-cheap-baseline-preservation-gate

-   **Motivation:** The current router cost nearly as much as pro while failing to preserve several cheap flash/pro wins, so routing needs an explicit cheap-baseline preservation check.
-   **Sketch:** Add a route validation gate that prefers the cheap model unless the route rule is high-confidence or a verifier failure justifies escalation. Compare against flash, pro, and the current hard-cluster router on cost per pass and lost cheap-model wins.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-25

#### timeout-recovery-hard-cluster-slice

-   **Motivation:** The model-router hard-cluster run still had timeout_no_recovery as the dominant failure mode, including seven all-leg failed tasks concentrated in c_build, regex_programming, and python_ml.
-   **Sketch:** Run timeout-aware recovery on a hard-cluster slice rather than only needs_network tasks, with c_build, regex_programming, and python_ml represented separately. Measure decisive wins plus reductions in timeout_no_recovery and repeated_failed_command.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-25

#### runtime-component-label-audit

-   **Motivation:** The timeout-recovery-hard-cluster-slice mutation leg is named basic_timeout_aware_retry but its 14 trials have empty components_active, so cross-experiment component_perf cannot count those active hard-cluster attempts.
-   **Sketch:** Add a preflight or ingest validation that runtime-flag ablation legs declare the expected component id, and fail or repair metadata before critique. Re-ingest the affected timeout run after the label path is fixed so future cross-experiment passes can measure the component rather than treating it as unlabeled control.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-26

#### timeout-strategy-switch-checkpoint

-   **Motivation:** The hard-cluster timeout-aware retry run went 0/14 while only lowering cost and median runtime, with failures still dominated by turn-budget, toolchain, parser, and premature-stop loops.
-   **Sketch:** Extend timeout-aware retry with a forced strategy-switch checkpoint after the first timeout or repeated failed command: choose a task-specific recovery playbook such as toolchain triage, parser edge-case tests, or CLI-shape discovery before spending more turns. Test it against the same c_build, regex_programming, and python_ml hard-cluster slice with at least 10 trials per side.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-26

#### component-catalog-registration-gate

-   **Motivation:** [medium confidence: 32 unknown_id misconfiguration rows] Components can be present in trials.components_active while still being flagged as unknown_id, with planner-schema-guard and executor-bash-timeout-aware-retry both affected.
-   **Sketch:** Add a preflight or ingest gate that requires every active component id to resolve against the component catalog before the run becomes verdict-bearing. If a branch-local component is intentionally experimental, register it deterministically during tree apply or mark it with an explicit experimental catalog entry.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-26

#### critic-score-outcome-consistency-check

-   **Motivation:** [low confidence: 3 registry-pass disagreements in the latest 12-trial audit] Trial critiques can describe reward-1.0 registry passes as failed, which makes cross-experiment anti-pattern summaries noisier even when pass-rate math uses registry scores.
-   **Sketch:** Add a deterministic post-critic consistency check that compares critique outcome against trials.score and passed, retries or patches the critic payload when they conflict, and stores any irreconcilable discrepancy in extra metadata rather than the main outcome field.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-26

#### targeted-router-cost-calibration

-   **Motivation:** The targeted-router confirmation improved the hard slice from 6/24 to 8/24 passes, but mean cost rose about 162% and the lift concentrated in python_ml while binary_analysis and regex_programming mostly preserved scores at higher cost.
-   **Sketch:** Add route calibration that escalates only when historical cluster lift clears a cost-per-extra-pass threshold, and keep the cheap model for tied or already-strong clusters. Rerun flash vs current targeted router vs cost-calibrated router on the same hard slice plus an easy-slice guardrail to measure cost per pass and lost cheap-model wins.
-   **Auto-proposed by:** cross-experiment-critic@2026-04-25

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

#### planner-schema-guardrail

-   **Motivation:** planner_executor lost many baseline tasks to planner-side ValidationError / structured-output failures, so the current branch signal is contaminated by schema breakage rather than execution quality
-   **Sketch:** add a planner-output repair guard that retries invalid or empty planner JSON with explicit schema feedback before executor handoff, then test planner_executor with vs without the guard on the existing planner-positive slice

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
