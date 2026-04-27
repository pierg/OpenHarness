# Experiments

## 2026-04-26 — runtime-component-label-audit

-   **Type:** paired-ablation
-   **Current best at run-time:** [`basic`](../src/openharness/agents/configs/basic.yaml)
-   **Hypothesis:** A preflight or ingest validation that requires runtime-flag ablation legs to declare their expected component id will prevent component_perf undercounting and make future runtime experiments verdict-bearing.
-   **Run:** [`runs/experiments/runtime-component-label-audit-20260426-022341`](../runs/experiments/runtime-component-label-audit-20260426-022341)
-   **Branch:** [`lab/runtime-component-label-audit`](https://github.com/pierg/OpenHarness/pull/48) — metadata-only merge (no_op: metadata-only no-op outcome; implementation branch discarded; discarded=`11c01bd`)

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `basic_flash` | `basic` | 6 | 3 | 3 | 50.0% | $0.78 |
| `basic_timeout_aware_retry` | `basic_timeout_aware_retry` | 6 | 3 | 3 | 50.0% | $0.63 |
### Mutation impact
Accuracy was a wash: basic_timeout_aware_retry and basic_flash both passed 3/6 trials, so the headline delta is 0.0 percentage points. The retry leg reduced total cost from $0.777 to $0.629 and median trial time from 99.125s to 81.983s, with the largest tie-break gain on low-complexity log-summary-date-ranges where both legs scored 1.0 but retry averaged $0.022901 versus $0.068390. It did not address high-complexity c_build work or zero-output starts: build-pov-ray stayed 0/4 across legs, and regex-log still had one empty first completion in each leg.
### Failure modes
-   **legacy-build-turn-budget-exhaustion** (×4): All four build-pov-ray trials failed after archive discovery, UNIX build staging, and uppercase filename versus Makefile mismatch work consumed the 30-turn budget; critiques cite anti-patterns including budget-exhaustion, turn_budget_exhausted, budget-exhausted, and legacy-build-struggle.
-   **empty-first-completion** (×2): Both legs had one regex-log trial where the model produced an empty first response with no tool calls, recorded as empty-model-response/empty-response and gave-up-too-early.
-   **sequential-regex-edge-testing** (×1): basic_flash had one regex-log critique marked inefficient_testing and timeout_no_recovery after it tested edge cases one Perl command at a time instead of batching them after python3 was unavailable.
-   **critic-registry-outcome-disagreement** (×3): Three reward-1.0 registry passes were described as failed by trial-critic: both timeout log-summary-date-ranges trials and one basic_flash regex-log trial, so downstream analysis should prefer registry score for pass-rate math and preserve the discrepancy as evidence metadata.
### Tree effect
-   **Verdict:** **No-op** — recorded for trend analysis
-   **Target:** `basic_timeout_aware_retry`
-   **Pair:** baseline leg `basic_flash` vs mutation `basic_timeout_aware_retry`
-   **Δ pass-rate:** +0.00 pp
-   **Δ $/pass:** -19.1%
-   **Confidence:** 0.00
-   **Rationale:** Inconclusive: Δ pass-rate = +0.0pp (current best 50.0% vs mutation 50.0%); 0 positive cluster(s) (threshold 2); Δ $/pass = -19%.
-   **Evidence:** [`experiment-critic.json`](../runs/experiments/runtime-component-label-audit-20260426-022341/critic/experiment-critic.json), [`comparisons`](../runs/experiments/runtime-component-label-audit-20260426-022341/critic/comparisons), [`critic_summary.md`](../runs/experiments/runtime-component-label-audit-20260426-022341/results/critic_summary.md)

| Cluster | baseline pass | mut pass | Δ pp |
|---------|-----------:|---------:|-----:|
| `bash_pipeline` | 2/2 | 2/2 | +0.0 |
| `c_build` | 0/2 | 0/2 | +0.0 |
| `regex_programming` | 1/2 | 1/2 | +0.0 |
### Linked follow-ups

-   **roadmap** `component-catalog-registration-gate`: queued at the top of `## Up next` because cross-experiment analysis found 32 `unknown_id` component misconfiguration rows after the runtime label repair.
-   **roadmap** `toolchain-fallback-playbooks-on-c-build`: kept next among score-seeking experiments because bare timeout-aware retry tied control while c_build still failed on legacy build and turn-budget loops.
-   **roadmap** `timeout-strategy-switch-checkpoint`: demoted to `### Suggested` until toolchain-specific playbooks or metadata gates justify another timeout-aware retry derivative.
-   **roadmap** `critic-score-outcome-consistency-check`: added to `### Suggested` because three registry-passing trials were described as failed by trial-critic.

## 2026-04-26 — timeout-recovery-hard-cluster-slice

-   **Type:** paired-ablation
-   **Current best at run-time:** [`basic`](../src/openharness/agents/configs/basic.yaml)
-   **Hypothesis:** Timeout-aware recovery may be more valuable on hard `c_build`, `regex_programming`, and `python_ml` failure clusters than on the original network-only smoke slice, because those clusters repeatedly exhaust turn or wall-clock budgets.
-   **Run:** [`runs/experiments/timeout-recovery-hard-cluster-slice-20260426-003209`](../runs/experiments/timeout-recovery-hard-cluster-slice-20260426-003209)
-   **Branch:** [`lab/timeout-recovery-hard-cluster-slice`](https://github.com/pierg/OpenHarness/pull/47) — metadata-only merge (no_op: both legs passed 0/14; retry only reduced cost/runtime and did not recover hard-cluster failures.; discarded=`86482e0`)

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `basic_flash` | `basic` | 14 | 0 | 14 | 0.0% | $3.41 |
| `basic_timeout_aware_retry` | `basic_timeout_aware_retry` | 14 | 0 | 14 | 0.0% | $2.50 |
### Mutation impact
The mutation produced a 0.0 percentage-point pass-rate delta: basic_flash passed 0/14 and basic_timeout_aware_retry also passed 0/14. It reduced cost by about $0.91 and median runtime by about 60.6s, with nominal tie-break wins on six of seven tasks, but every task still failed. The largest unchanged clusters were c_build tasks blocked by dependency/toolchain and turn-budget loops, regex_programming tasks blocked by brittle parsing or empty termination, and python_ml tasks blocked by slow iteration, PyStan argument loops, and CLI-shape mistakes. Causal hypothesis: timeout awareness shortened some failing trajectories but did not add a concrete recovery policy before max turns, so it saved spend without improving correctness.
### Failure modes
-   **turn-budget-or-timeout** (×32): Normalized anti-pattern tags repeatedly cite timeout_no_recovery, turn_budget_exhausted, exhausted-budget, max-turns-exceeded, or slow iteration. This dominated c_build, rstan-to-pystan, and sam-cell-seg failures.
-   **brittle-parser-or-argument-assumption** (×5): filter-js-from-html used regex HTML parsing that missed XSS vectors, and sam-cell-seg used positional CLI arguments where hidden tests expected named flags.
-   **premature-empty-termination** (×5): regex-chess had empty or immediate give-up responses in multiple trials, and one retry trial for rstan-to-pystan terminated with only a stray closing brace after gathering context.
-   **dependency-toolchain-loop** (×3): Build and ML trials repeatedly stalled around missing compilers, Coq/CompCert setup, MIPS cross-compiler discovery, or PyStan environment details instead of reaching a verified deliverable.
-   **insufficient-or-misdirected-testing** (×3): Agents relied on manual checks or local scratch tests and missed the verifier's real edge cases, especially filter-js-from-html and sam-cell-seg.
-   **repeated-failed-command-loop** (×2): Several trials kept guessing URLs, package/tool invocations, or PyStan sampling arguments without switching to a more reliable strategy before the turn budget expired.
### Tree effect
-   **Verdict:** **No-op** — recorded for trend analysis
-   **Target:** `basic_timeout_aware_retry`
-   **Pair:** baseline leg `basic_flash` vs mutation `basic_timeout_aware_retry`
-   **Δ pass-rate:** +0.00 pp
-   **Confidence:** 0.00
-   **Rationale:** Inconclusive: Δ pass-rate = +0.0pp (current best 0.0% vs mutation 0.0%); 0 positive cluster(s) (threshold 2); no cost data.
-   **Evidence:** [`experiment-critic.json`](../runs/experiments/timeout-recovery-hard-cluster-slice-20260426-003209/critic/experiment-critic.json), [`comparisons`](../runs/experiments/timeout-recovery-hard-cluster-slice-20260426-003209/critic/comparisons), [`critic_summary.md`](../runs/experiments/timeout-recovery-hard-cluster-slice-20260426-003209/results/critic_summary.md)

| Cluster | baseline pass | mut pass | Δ pp |
|---------|-----------:|---------:|-----:|
| `c_build` | 0/6 | 0/6 | +0.0 |
| `python_ml` | 0/4 | 0/4 | +0.0 |
| `regex_programming` | 0/4 | 0/4 | +0.0 |
### Linked follow-ups

-   **roadmap** `runtime-component-label-audit`: queued at the top of `## Up next` because this run left 14 `basic_timeout_aware_retry` mutation trials without component attribution, blocking reliable `component_perf` evidence.
-   **roadmap** `toolchain-fallback-playbooks-on-c-build`: promoted near the top because the hard-cluster slice still failed on dependency/toolchain loops and turn-budget exhaustion after bare timeout retry.
-   **roadmap** `timeout-strategy-switch-checkpoint`: queued behind the label audit and c_build playbook work because timeout recovery needs an explicit strategy switch after timeouts or repeated failed commands, not only background polling.
-   **roadmap** `timeout-aware-retry-needs-network-confirmation`: demoted to `### Suggested` because the bare retry branch now has only an under-powered network smoke and a 0/14 hard-cluster no-op.

## 2026-04-24 — tb2-gemini3-model-baseline

-   **Type:** paired-ablation
-   **Current best at run-time:** [`basic`](../src/openharness/agents/configs/basic.yaml)
-   **Hypothesis:** The current-best score is partly model-bound: replacing `gemini-3.1-flash-lite-preview` with the stronger Gemini 3 Flash / 3.1 Pro coding models on the same `basic` harness will raise full-suite pass rate enough to change which runtime and prompt mechanisms are worth pursuing next.
-   **Run:** [`runs/experiments/tb2-gemini3-model-baseline-20260424-225008`](../runs/experiments/tb2-gemini3-model-baseline-20260424-225008)
-   **Branch:** [`lab/tb2-gemini3-model-baseline`](https://github.com/pierg/OpenHarness/pull/45)

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `basic_flash` | `basic` | 89 | 31 | 58 | 34.8% | $14.02 |
| `basic_lite` | `basic` | 89 | 21 | 68 | 23.6% | $5.10 |
| `basic_pro` | `basic` | 89 | 40 | 49 | 44.9% | $111.35 |
### Mutation impact
The higher-capacity basic_pro leg helped overall: +10.1 percentage points over basic_flash and +21.3 pp over basic_lite, with decisive gains in python_ml, c_build, regex_programming, binary_analysis, and several one-off implementation tasks. The effect was not cost-efficient: cost per pass was about $2.78 for pro, $0.45 for flash, and $0.24 for lite, so flash/lite remain the cheaper frontier when exact correctness is not required. The causal hypothesis is that pro more often completed full implementations after setup, while cheaper legs more often hit "gave-up-too-early", repeated-command, or timeout failure modes.
### Failure modes
-   **timeout-no-recovery** (×100): Turn budget or wall-clock exhaustion dominated failures; critiques repeatedly used "timeout-no-recovery" / "timeout_no_recovery" and related budget-exhaustion labels.
-   **repeated-failed-command** (×38): Agents retried broken commands or fixes instead of switching tactics; critiques used "repeated-failed-command" / "repeated_failed_command".
-   **gave-up-too-early** (×28): Agents stopped after setup or partial progress; critiques used "gave-up-too-early", "gave_up_too_early", or "empty-assistant-turn".
-   **hallucinated-success** (×7): Agents claimed completion despite failed or missing verification; critiques used "hallucinated-success" / "hallucinated_success".
-   **analysis-paralysis** (×7): Agents spent turns planning or overcomplicating without converging on the deliverable; critiques used "analysis-paralysis" or "overcomplication".
### Tree effect
-   **Verdict:** **No-op** — historical scoped-accept signal retained as diagnostic evidence
-   **Target:** `basic`
-   **Current classification:** measurement-only scoped signal. Pro/Lite model deltas are useful evidence, but any selective model policy must derive its route from instruction/workspace/runtime observations, not offline `task_features`.
-   **Pair:** baseline leg `basic_flash` vs mutation `basic_lite`
-   **Δ pass-rate:** -11.24 pp
-   **Δ $/pass:** -46.3%
-   **Confidence:** 1.00
-   **Rationale:** Baseline wins overall (Δ = -11.2pp), but mutation wins ≥ +5pp on 5 cluster(s): c_runtime_debugging (+100pp, n=1), git_service_deployment (+100pp, n=1), security_python_web (+100pp, n=1), sparql_query (+100pp, n=1), git_workflow (+25pp, n=4). (also: basic_pro produced a historical scoped-accept signal: Baseline wins overall (Δ = +10.1pp), but mutation wins ≥ +5pp on 15 cluster(s): c_runtime_debugging (+100pp, n=1), calendar_scheduling (+100pp, n=1), compression_reverse_engineering (+100pp, n=1), database_recovery (+100pp, n=1), image_ocr (+100pp, n=1), logic_circuit_synthesis (+100pp, n=1), python_async (+100pp, n=1), r_scientific_computing (+100pp, n=1), security_python_web (+100pp, n=1), sparql_query (+100pp, n=1), binary_analysis (+50pp, n=2), regex_programming (+33pp, n=3), python_ml (+29pp, n=7), git_workflow (+25pp, n=4), c_build (+17pp, n=6).)
-   **Historical slice evidence:** `{"any_of": [{"task_features.category": "c_runtime_debugging"}, {"task_features.category": "git_service_deployment"}, {"task_features.category": "security_python_web"}, {"task_features.category": "sparql_query"}, {"task_features.category": "git_workflow"}], "derived_from": "archived cluster deltas"}`
-   **Evidence:** [`experiment-critic.json`](../runs/experiments/tb2-gemini3-model-baseline-20260424-225008/critic/experiment-critic.json), [`comparisons`](../runs/experiments/tb2-gemini3-model-baseline-20260424-225008/critic/comparisons), [`critic_summary.md`](../runs/experiments/tb2-gemini3-model-baseline-20260424-225008/results/critic_summary.md)

| Cluster | baseline pass | mut pass | Δ pp |
|---------|-----------:|---------:|-----:|
| `c_runtime_debugging` | 0/1 | 1/1 | +100.0 |
| `coq_theorem_proving` | 1/1 | 0/1 | -100.0 |
| `cpp_memory_debugging` | 1/1 | 0/1 | -100.0 |
| `git_service_deployment` | 0/1 | 1/1 | +100.0 |
| `latex_document_repair` | 1/1 | 0/1 | -100.0 |
| `legacy_modernization` | 1/1 | 0/1 | -100.0 |
| `python_grpc` | 1/1 | 0/1 | -100.0 |
| `python_packaging_server` | 1/1 | 0/1 | -100.0 |
### Linked follow-ups
_(none retained)_

## 2026-04-24 — timeout-aware-retry-on-needs-network

-   **Type:** paired-ablation
-   **Current best at run-time:** [`basic`](../src/openharness/agents/configs/basic.yaml)
-   **Hypothesis:** timeout-aware retry / background polling recovers a meaningful share of the `needs_network` + `high_env_complexity` failures that currently collapse into repeated command loops or unrecovered bash timeouts.
-   **Run:** [`runs/experiments/timeout-aware-retry-on-needs-network-smoke-20260424-193153`](../runs/experiments/timeout-aware-retry-on-needs-network-smoke-20260424-193153)
-   **Branch:** [`lab/timeout-aware-retry-on-needs-network`](https://github.com/pierg/OpenHarness/pull/38) — metadata-only merge (no_op: smoke tied control at 2/4 passes per leg and was under the evidence floor.; discarded=`a4ca455`)

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `basic` | `basic` | 4 | 2 | 2 | 50.0% | $0.46 |
| `basic_timeout_aware_retry` | `basic_timeout_aware_retry` | 4 | 2 | 2 | 50.0% | $0.09 |
### Mutation impact
-   **Best leg:** `basic` (50.0%, $0.46)
-   **Worst leg:** `basic_timeout_aware_retry` (50.0%, $0.09)
-   **Spread:** +0.0 pp
-   _(experiment-critic JSON missing a `mutation_impact` field; this is a DB-only fallback.)_
### Failure modes

_(pending)_

### Tree effect
-   **Verdict:** **No-op** — recorded for trend analysis
-   **Target:** `basic_timeout_aware_retry`
-   **Pair:** baseline leg `basic` vs mutation `basic_timeout_aware_retry`
-   **Δ pass-rate:** +0.00 pp
-   **Δ $/pass:** -79.4%
-   **Confidence:** 0.00
-   **Rationale:** insufficient_data: smallest leg has n=4 trials (< floor of 5); under-sampled legs: {'basic': 4, 'basic_timeout_aware_retry': 4}. Re-run on a wider slice (the design's `## Slice > Full` section) before drawing a verdict.
### Linked follow-ups

-   **roadmap** `timeout-aware-retry-needs-network-confirmation`: queued at the top of `## Up next` because this smoke run tied control at 2/4 passes per leg and fell below the evidence floor, leaving the full needs_network timeout-recovery hypothesis unresolved.

## 2026-04-24 — planner-schema-guard-paired

-   **Type:** paired-ablation
-   **Current best at run-time:** [`basic`](../src/openharness/agents/configs/basic.yaml)
-   **Hypothesis:** forcing `planner_executor` to repair invalid or empty planner JSON before executor handoff cuts planner-side `ValidationError` / `structured-output-failure` enough to recover trustworthy signal on the planner-positive slice.
-   **Run:** [`runs/experiments/planner-schema-guard-paired-20260424-154436`](../runs/experiments/planner-schema-guard-paired-20260424-154436)
-   **Branch:** [`lab/planner-schema-guard-paired`](https://github.com/pierg/OpenHarness/pull/33) — metadata-only merge (no_op: schema guard matched control at 8/22 passes and only lowered cost, so the variant stays rejected.; discarded=`74d125b`)

### Aggregate
| Leg | Agent | Trials | Passed | Failed | Pass rate | Cost (USD) |
|-----|-------|-------:|-------:|-------:|----------:|-----------:|
| `planner_executor_control` | `planner_executor` | 22 | 8 | 14 | 36.4% | $1.52 |
| `planner_executor_schema_guard` | `planner_executor_schema_guard` | 22 | 8 | 14 | 36.4% | $1.17 |
### Mutation impact
Overall pass rate moved 0.0 percentage points: `planner_executor_control` stayed at 36.4% and `planner_executor_schema_guard` stayed at 36.4%. The biggest positive shift was the system-administration task `git-multibranch`, where schema_guard kept recovery aligned with the required `/git/project` path and improved the paired mean score by +0.5. The biggest regression was the security-certificates task `openssl-selfsigned-cert`, where control recovered by replacing `cryptography` with an `openssl x509` checker while schema_guard kept the environment-mutation path. Elsewhere the mutation mostly changed cost and time on tied outcomes, so its practical effect looks like plan-shape repair and faster failure rather than better task reasoning.
### Failure modes
-   **repeated_failed_command** (×22): Repeated empty `glob` probes, repeated solver rewrites, or other low-yield retries consumed budget without changing the approach.
-   **no_pre_edit_inspection** (×14): The agent committed to an implementation before reading the verifier, task files, or live config it needed to ground the work.
-   **gave_up_too_early** (×13): The run ended after an unrecovered bad plan or last-turn speculation instead of closing the loop with a verifier-aligned recovery.
-   **hallucinated_success** (×9): The agent declared success from local checks that did not match the verifier contract or ignored failing evidence already in the run.
-   **verification_gap** (×8): Validation stayed partial or sample-only, missing global constraints, clean-environment execution, or exact numeric targets.
-   **environment_mutation** (×5): The agent used environment changes such as `pip install` as a fix instead of delivering portable task artifacts.
### Tree effect
-   **Verdict:** **No-op** — recorded for trend analysis
-   **Target:** `planner_executor_schema_guard`
-   **Pair:** baseline leg `planner_executor_control` vs mutation `planner_executor_schema_guard`
-   **Δ pass-rate:** +0.00 pp
-   **Δ $/pass:** -22.7%
-   **Confidence:** 0.00
-   **Rationale:** Inconclusive: Δ pass-rate = +0.0pp (current best 36.4% vs mutation 36.4%); 1 positive cluster(s) (threshold 2); Δ $/pass = -23%.
-   **Evidence:** [`experiment-critic.json`](../runs/experiments/planner-schema-guard-paired-20260424-154436/critic/experiment-critic.json), [`comparisons`](../runs/experiments/planner-schema-guard-paired-20260424-154436/critic/comparisons), [`critic_summary.md`](../runs/experiments/planner-schema-guard-paired-20260424-154436/results/critic_summary.md)

| Cluster | baseline pass | mut pass | Δ pp |
|---------|-----------:|---------:|-----:|
| `security_certificates` | 1/2 | 0/2 | -50.0 |
| `system_administration` | 3/6 | 4/6 | +16.7 |
| `python_data` | 4/14 | 4/14 | +0.0 |
### Linked follow-ups
-   **roadmap** `timeout-aware-retry-on-needs-network`: promoted to the top of `## Up next` because repeated command loops and unrecovered timeouts remain the strongest cross-experiment blocker after schema repair only reduced cost.
-   **roadmap** `planner-executor-cluster-confirmation`: demoted to `### Suggested` because `planner-schema-guard-paired` was a score wash, so the higher-cost planner confirmation is no longer front-of-queue.
-   **idea** `planner-empty-glob-breaker`: remains the narrower planner-specific follow-up if the current-best-facing timeout-recovery run still leaves planner path-grounding failures unresolved.

## 2026-04-24 — loop-guard-on-basic-near-miss

-   **Type:** paired-ablation
-   **Current best at run-time:** [`basic`](../src/openharness/agents/configs/basic.yaml)
-   **Hypothesis:** enabling `LoopGuardConfig.enabled` on current best `basic` recovers a meaningful share of the loop-heavy near-miss failures from `extended-budget-paired-on-trunk` by breaking repeated command / timeout spirals without the cost blow-up of longer budgets.
-   **Run:** [`runs/experiments/loop-guard-on-basic-near-miss-20260424-021810`](../runs/experiments/loop-guard-on-basic-near-miss-20260424-021810)
-   **Branch:** [`lab/loop-guard-on-basic-near-miss`](https://github.com/pierg/OpenHarness/pull/32) — metadata-only merge (reject: loop-guard on basic scored 1/46 vs current best 2/46 on the near-miss slice and did not recover loop-heavy failures.; discarded=`9b96272`)

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
-   **Pair:** baseline leg `basic` vs mutation `basic_loop_guard`
-   **Δ pass-rate:** -2.17 pp
-   **Δ $/pass:** +98.9%
-   **Confidence:** 0.43
-   **Rationale:** Δ pass-rate = -2.2pp; Δ $/pass = +99%; cost spike ≥ 50%; no positive cluster.

| Cluster | baseline pass | mut pass | Δ pp |
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
-   **Current best at run-time:** [`basic`](../src/openharness/agents/configs/basic.yaml)
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
Relative to current best `basic_30_8192` (10.7% pass, 3/28), both extended budgets improved to 14.3% (4/28), a +3.6 percentage-point gain driven entirely by the `scientific_computing` task `tune-mjcf`; `crack-7z-hash`, `headless-terminal`, and `pytorch-model-cli` already passed across legs and only changed on efficiency. The 60-turn/16k leg captured the full pass-rate gain at 2.9x baseline cost ($6.58 vs $2.28), while the 120-turn/32k leg added 0 extra percentage points over 60-turn and raised cost to 9.1x current best ($20.63). The causal pattern is narrow: extra search budget helps evaluator-guided optimization, but on most tasks it just prolongs the same `repeated_failed_command` / `timeout_no_recovery` loops and increases `hallucinated_success`.
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
-   **Pair:** baseline leg `basic_120_32768` vs mutation `basic_30_8192`
-   **Δ pass-rate:** -3.57 pp
-   **Δ $/pass:** -85.3%
-   **Confidence:** 0.71
-   **Rationale:** Δ pass-rate = -3.6pp; Δ $/pass = -85%; no positive cluster. (also: basic_60_16384 → no_op: Inconclusive: Δ pass-rate = +0.0pp (current best 14.3% vs mutation 14.3%); 0 positive cluster(s) (threshold 2); Δ $/pass = -68%.)
-   **Evidence:** [`experiment-critic.json`](../runs/experiments/extended-budget-paired-on-trunk-20260423-184410/critic/experiment-critic.json), [`comparisons`](../runs/experiments/extended-budget-paired-on-trunk-20260423-184410/critic/comparisons), [`critic_summary.md`](../runs/experiments/extended-budget-paired-on-trunk-20260423-184410/results/critic_summary.md)

| Cluster | baseline pass | mut pass | Δ pp |
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
-   **Current best at run-time:** none (this run anchored the current best)
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
-   **Verdict:** **Accept** — anchored current best
-   **Target:** `basic`
-   **Pair:** full-suite sweep of `basic`, `planner_executor`, and `react`
-   **Confidence:** 1.00
-   **Rationale:** `basic` was the strongest full-suite baseline: 22.5% pass rate (20/89) at $5.84. `react` and `planner_executor` were not retained as current-best candidates; their narrow cluster signals are diagnostic only and require fresh leakage-free follow-up before promotion.
-   **Evidence:** [`experiment-critic.json`](../runs/experiments/tb2-baseline-20260417-234913/critic/experiment-critic.json), [`comparisons`](../runs/experiments/tb2-baseline-20260417-234913/critic/comparisons), [`critic_summary.md`](../runs/experiments/tb2-baseline-20260417-234913/results/critic_summary.md)

### Linked follow-ups
-   [`planner-executor-cluster-confirmation`](roadmap.md#planner-executor-cluster-confirmation) — focused re-test of the historical scoped signal on its 3 positive clusters with n>=5 (current verdict rests on n=1/3/7).
-   [`react-tentative-cluster-retest`](roadmap.md#react-tentative-cluster-retest) — flip react's no_op (1 positive cluster, threshold 2) into a clean verdict on its winning cluster.
-   [`extended-budget-paired-on-trunk`](roadmap.md#extended-budget-paired-on-trunk) — cheapest test of whether the 22.5% baseline is budget-bound vs capability-bound on a near-miss slice.
-   [`loop-guard-on-planner-executor`](ideas.md#loop-guard-on-planner-executor) — auto-proposed; depends on `loop-guard-paired-ablation` landing first.
-   [`tool-result-summariser-paired`](ideas.md#tool-result-summariser-paired) — auto-proposed; sibling of `reflection-context-compaction`.
