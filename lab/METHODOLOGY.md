# Methodology

The scientific contract for lab experiments.

## 0. Mission and generalization contract

The lab exists to discover, implement, test, and preserve
generalizable improvements to the OpenHarness agent system. Good ideas
may come from agent literature or from new hypotheses: prompting,
ReAct-style loops, supervisor/specialist architectures, memory,
tool-output management, exploration/search, test-time inference,
model-selection policies, validation gates, runtime recovery, tools,
or evaluation infrastructure.

The benchmark is a measurement instrument, not information the agent
may memorize. The core boundary is:

- offline analysis may use task names, clusters, prior failures,
  task features, and per-task results to decide what to test next
- experiment slices may target known difficult tasks to cheaply test a
  hypothesis
- runtime agent policy may use only information available on an
  unseen task: the instruction, workspace, tools, environment
  observations, and reasoning derived during the run

Promotable implementation code must not branch on exact benchmark
identity (`task_name`, `task_id`, `task_checksum`, trial directory
name, known benchmark task lists, or prior per-task outcomes). Offline
`task_features` are analysis metadata; runtime routing may use only
features re-derived from the task instruction/workspace at runtime.

Every experiment should be classifiable as:

- `promotable` — runtime mechanism is deployable on unseen tasks
- `diagnostic_only` — uses benchmark knowledge to estimate an upper
  bound or understand a failure surface, but cannot produce
  `add_branch` or `graduate`
- `invalid` — leaks benchmark identity into runtime behavior or
  otherwise cannot answer a useful harness question

## 1. Unit of evidence

One experiment produces one verdict over one declared slice and one
declared set of legs. The verdict is one of:

- `graduate`
- `add_branch`
- `reject`
- `no_op`

That verdict must be explainable from:

- the slice
- the leg definitions
- the repetition policy
- the control
- the deterministic verdict thresholds below

## 2. Slice

Every experiment must declare a full slice in design.

Allowed shapes:

- `full-bench`
- `cluster:<name>`
- `cluster_combined:<a,b,...>`
- `regression:<task ids>`
- `near-miss`

Hard rules:

- all legs run the same task list
- the slice size must be explicit
- a `graduate` claim must come from `full-bench` or a broad enough
  combined slice, not a tiny cherry-picked subset
- `regression`, `near-miss`, and known-cluster slices are allowed for
  evaluation, but the mutation itself must remain runtime-admissible
  if it is eligible for `add_branch` or `graduate`

## 3. Legs

Default is a paired ablation:

- trunk leg
- one mutation leg

Multi-arm runs are allowed when the question genuinely needs them, but
the contrasts must still be interpretable.

Hard rule:

- do not confound multiple independent changes in a 2-leg comparison
  unless the experiment is explicitly testing the combined package

## 4. Repetitions

`n_attempts` controls per-cell noise. It does not widen population
coverage.

Defaults:

- `full-bench`: usually `n_attempts = 1`
- smaller or noisier slices: usually `n_attempts = 2`

Hard rule:

- do not pretend repeated runs on the same tiny slice are equivalent
  to wider population coverage

## 5. Control

Default and expected mode is `fresh` control: the control leg runs in
the same experiment as the mutation.

Historical-control modes remain deferred unless explicitly implemented
with drift guards.

## 6. Verdict thresholds

These thresholds are enforced in `src/openharness/lab/tree_ops.py`.

### Floors

| Constant | Value | Meaning |
|----------|------:|---------|
| `MIN_TRIALS_PER_LEG_FOR_VERDICT` | 5 | below this, the outcome collapses to `no_op` due to insufficient data |
| `SMALLEST_MEANINGFUL_EFFECT_PP` | 5.0 | smaller effects imply low confidence |

### Verdicts

| Verdict | Conditions |
|---------|------------|
| `graduate` | runtime-admissible mechanism, overall pass-rate lift at or above the graduate threshold, no serious per-cluster regression, acceptable cost delta |
| `add_branch` | runtime-admissible mechanism with a clear win on coherent sub-clusters, but not enough to replace trunk overall |
| `reject` | clear regression or unacceptable cost blow-up without offsetting upside |
| `no_op` | everything else, including insufficiently strong evidence |

The thresholds are deterministic and must not depend on narrative
interpretation after the fact.

Offline cluster wins are evidence, not automatically deployable
routing policy. If the only available branch predicate is an offline
analysis label such as `task_features.category`, `tree_ops.evaluate`
must record a diagnostic branch signal as `no_op` and leave replan to
queue a follow-up with an instruction/workspace-derived runtime
trigger.

## 7. Verdict lifecycle

The verdict is first materialized on the experiment branch during
critique. It becomes real for `main` only after finalize merges the
experiment outcome.

Normal merge behavior:

- `add_branch` / `graduate`: merge accepted code + `lab/` changes
- `reject` / `no_op`: merge metadata-only `lab/` changes; keep
  rejected implementation off `main`

There is no normal human `graduate confirm` gate in this workflow.
Historical staged-graduate rows are legacy cleanup only.

## 8. Replan is part of the method

The experiment is not methodologically complete until its findings have
been reflected into the queue.

The dedicated `replan` phase must:

- move the finished slug to `## Done`
- reprioritize `## Up next` based on the evidence
- add, demote, or remove future entries when warranted
- optionally write lower-confidence work to `### Suggested` or
  `## Auto-proposed`

This is deliberate: roadmap mutation is part of the evidence loop, not
an optional side task.

## 9. Anti-patterns

- drawing a verdict from a smoke run
- using different task lists across legs
- confounded 2-leg comparisons
- selection-biased tiny slices as evidence for trunk promotion
- promoting diagnostic-only experiments as `add_branch` or `graduate`
- converting offline `task_features` cluster wins directly into
  branch-routing predicates
- runtime routing or prompting keyed by exact benchmark identity
- letting finalize succeed without a merge back to `main`
