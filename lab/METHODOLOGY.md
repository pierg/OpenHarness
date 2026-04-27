# Methodology

The lab exists to autonomously discover, implement, measure, and keep
generalizable improvements to agentic harnesses. Successful results
identify mechanisms that can transfer across agent systems, benchmarks,
or production engineering tasks.

## Mission

The search space is intentionally broad: prompting, ReAct-style loops,
supervisor/specialist architectures, memory, tool-output management,
exploration/search, test-time inference, model-selection policies,
runtime recovery, validation gates, tools, and evaluation
infrastructure are all in scope.

Prefer simple experiments that answer a clear question. If an
experiment requires many moving parts, split it until each run has one
main hypothesis and one interpretable outcome.

## Generalization

Benchmark metadata is allowed for analysis and planning. The lab may
use task names, clusters, prior failures, task features, and per-task
results to decide what to test next or to build a targeted diagnostic
slice.

Runtime agent behavior should use information available on an unseen
task: the instruction, workspace, tools, environment
observations, and reasoning derived during the run. Exact benchmark
identity (`task_name`, `task_id`, `task_checksum`, trial directory
name, known benchmark task lists, or prior per-task outcomes) is not a
general agent policy. Offline `task_features` are analysis metadata;
if a useful cluster pattern appears, re-derive the trigger from the
task instruction/workspace before treating it as deployable behavior.

These criteria guide design, critique, and replan. If a diagnostic
experiment uses benchmark knowledge, keep the result as measurement and
queue a follow-up that tests a runtime-observable mechanism.

## Evidence Shape

Every experiment should declare:

- the hypothesis
- the baseline leg and candidate leg(s)
- the task slice being measured
- the repetition policy
- the control mode
- what would count as a meaningful outcome

Common slice shapes are full benchmark, cluster-focused, combined
cluster, regression list, and near-miss. Small slices can provide early
signal, but they should not be over-described as broad evidence.
Repeated attempts on the same tiny slice estimate noise; they do not
create population coverage.

The default control is fresh: run baseline and candidate in the same
experiment. Historical controls can be useful for planning, but should
be treated with drift skepticism unless the configs, models, verifier,
dataset, and repetition policy are clearly comparable.

## Verdicts

`experiment-critic` writes the experiment decision. The three outcomes
are:

- `accept` — the candidate is the best current harness choice for the
  measured goal, and the implementation should land with the lab
  metadata.
- `reject` — the candidate is worse, invalid, too costly, or otherwise
  not worth keeping as implemented. The lab should keep the evidence
  and discard the implementation branch.
- `no_op` — the run was inconclusive, diagnostic-only, underpowered, or
  useful mainly as trend data. The lab should keep the metadata and
  decide in replan whether a clearer follow-up is worth running.

Confidence is a judgment field, not a threshold. The critic should
explain the evidence, the likely causal story, and any generalization
risk in plain language.

## Replan

An experiment is not complete until its findings have changed the
queue. The `replan` phase should move the finished slug to `## Done`,
reprioritize `## Up next`, demote stale work, and add follow-ups when
the evidence suggests a sharper next question.

## Anti-Patterns

- drawing a decision from a wiring-only smoke run
- using different task lists across legs without saying so
- confounding unrelated changes in one candidate
- treating a tiny selected slice as proof of broad superiority
- accepting runtime policy keyed by exact benchmark identity
- converting offline `task_features` labels directly into deployed
  routing behavior
- preserving a complex mechanism when a simpler experiment would answer
  the same question
- letting finalize succeed without a merged outcome back to `main`
