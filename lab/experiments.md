# Experiments

Append-only log of concrete agent experiments. Newest at the top.
Each entry records: hypothesis, what was varied, what was held
constant, artifacts, numbers, and the decision.

## How to use

-   Running an experiment: spin up a git worktree (recommended for
    risky ideas), add a new `## YYYY-MM-DD — <slug>` section at the
    top of this file, fill in the template, run it, record results.
-   Never rewrite an old entry. If a later run changes the
    conclusion, write a new entry and link back with
    `**Supersedes:** <slug>`.
-   When an entry justifies promoting an idea from
    [`ideas.md`](ideas.md) into [`components.md`](components.md),
    link the entry from the component's "validated by" line.

## Template

```markdown
## YYYY-MM-DD — <slug>

**Status:** in-progress | complete | superseded
**Hypothesis:** one sentence.
**Varying:** `<component / idea id>` on vs off (or: new agent vs baseline).
**Held constant:** agent(s), model, dataset slice, budgets, sandbox.
**Run:** `runs/experiments/<instance-id>/`.

### Results

| Leg | Trials | Passed | Errored | Pass rate | Total tokens |
|-----|-------:|-------:|--------:|----------:|-------------:|
|     |        |        |         |           |              |

### Notes

-   ...

### Decision

-   ...
```

---

## 2026-04-16 — reflection-context-blowup-on-smoke

**Status:** complete
**Hypothesis:** the `reflection` baseline agent is healthy enough to
include in the first full TB2 sweep alongside `basic`,
`planner_executor`, `react`.
**Varying:** agent identity across the four baseline configs. All other
knobs held constant.
**Held constant:** model `gemini-3.1-flash-lite-preview`, dataset
`terminal-bench@2.0` profile=smoke (2 cached tasks: `regex-log`,
`log-summary-date-ranges`), worker budget 30 turns / 8192 tokens,
reflection parent budget 2 attempts, harbor wall-clock 900 s, Docker
sandbox.
**Run:** `runs/experiments/tb2-baseline-smoke-20260416-205703/`.

### Results

| Leg                | Trials | Passed | Errored | Pass rate | Total tokens | Cost (USD) |
|--------------------|-------:|-------:|--------:|----------:|-------------:|-----------:|
| `basic`            |      2 |      2 |       0 |     1.000 |      477 469 |     0.0498 |
| `planner_executor` |      2 |      0 |       0 |     0.000 |      162 423 |     0.0174 |
| `react`            |      2 |      0 |       0 |     0.000 |    1 604 316 |     0.1629 |
| `reflection`       |      2 |      1 |       1 |     0.500 |    9 127 356 |     0.9690 |

### Notes

-   `basic` is a healthy reference: passes both smoke tasks, ~$0.025
    per trial, single 503 retry handled cleanly by the new
    `MAX_RETRIES=5` / `_MAX_DELAY=90s` hardening.
-   `planner_executor` and `react` complete cleanly but fail the task —
    that is useful comparative signal and they should ship in the full
    sweep.
-   `reflection` is broken at the architecture level: both trials hit
    the 900 s harbor wall-clock and were killed mid-`bash` tool call.
    The `log-summary-date-ranges` trial alone burned **6.4 M input
    tokens / $0.67** before the kill. Avg 213 k input tokens *per
    worker turn* — tool outputs accumulate in the worker conversation
    each turn, so the per-call payload grows quadratically with turn
    count and the wall-clock fires before the 30-turn worker budget
    or the 2-attempt reflection budget can stop it.
-   The bug isn't the model or the prompt; it's that
    `src/openharness/agents/architectures/reflection.py` and the worker
    config never compact / truncate large stdout from `bash`/`grep`
    before they go back into the next turn's history.

### Decision

Drop `reflection` from `experiments/tb2-baseline.yaml` for the first
full TB2 sweep. Running it across 89 tasks at the smoke per-trial
cost would burn ~$43 to reproduce the same context-blowup data point
on every task. Track the fix as an idea in `lab/ideas.md`
("reflection-context-compaction") and add reflection back via
`exec rerun` against the same instance once it lands.

---

## 2026-04-16 — baseline-reset

**Status:** complete
**Hypothesis:** the previous "baseline" had several unvalidated
components wired in by default (loop-guard, web-tools,
extended-budget, grounded-planner-tools, planner-executor-critic,
critic-strict-verification) plus elaborate prompt protocols. Strip
all of it back to known-good architecture pieces (single agent,
planner+executor, reflection, react), drop the
`planner_executor_critic` composite, default the loop-guard runtime
to off, and revert budgets to 30 turns / 8192 tokens. The smoke
slice moves to two lighter cached tasks (`regex-log`,
`log-summary-date-ranges`).
**Varying:** N/A — this is a reset, not an A/B.
**Held constant:** N/A.
**Run:** none. The previous smoke run
(`tb2-baseline-demo-20260416-134729`) is recorded below as the
starting evidence; this entry documents the cleanup itself.

### Notes

-   `src/openharness/agents/configs/planner_executor_critic.yaml`
    deleted; the composite is now only an idea
    (`planner-executor-critic` in `ideas.md`).
-   `LoopGuardConfig.enabled` defaults to `False` so the runtime
    mechanism stays available for future A/B experiments without
    affecting the baseline.
-   Agent YAMLs no longer carry a `components:` list. The field
    still exists on `AgentConfig` (free-form metadata for future
    runs) but is empty in the baseline.
-   `experiments/tb2-baseline.yaml` defaults: `max_turns=30`,
    `max_tokens=8192`. New `smoke` profile = `regex-log` +
    `log-summary-date-ranges`. The old `demo` profile (`build-*`,
    `git-*`) is kept commented-style under a separate profile for
    parity with prior runs.
-   `lab/components.md` cleared — no validated components yet. All
    previously-listed components moved back to
    `lab/ideas.md#proposed`.

### Decision

This is the new baseline. The very first set of paired ablations
should re-introduce the stripped components one at a time on the
new smoke slice and graduate the ones that earn their pass-rate
delta.

---

## 2026-04-16 — tb2-baseline-phase4-smoke

**Status:** complete
**Hypothesis:** after the Phase 0–4 refactor
(`gemini-thought-signature` fix, portable run paths, prompt
protocols, `loop-guard`, `web-tools`, `extended-budget`, new
`planner_executor_critic`), the pipeline is healthy enough to serve
as the baseline for future ablations. Pass rate is secondary signal.
**Varying:** nothing — baseline snapshot.
**Held constant:** agents `default` + `planner_executor`, model
`gemini-2.0-flash`, dataset `terminal-bench@2.0` profile=demo (2
tasks), budgets 60 turns / 16384 tokens, Harbor + Docker sandbox.
**Run:** `runs/experiments/tb2-baseline-demo-20260416-134729/`.

### Results

| Leg                | Trials | Passed | Errored | Pass rate | Total tokens |
|--------------------|-------:|-------:|--------:|----------:|-------------:|
| `default`          |      2 |      0 |       1 |     0.000 |       75 025 |
| `planner_executor` |      2 |      0 |       0 |     0.000 |      907 148 |

### Notes

-   No Gemini `400 thought_signature` errors. Phase-0 fix holds.
-   No `tool_code` hallucinations in planner trajectories —
    `grounded-planner-tools` qualitatively working.
-   `planner_executor` burns ~12× the tokens of `default` for the
    same 2 tasks; planner iterates more than needed on simple work.
-   `default` hit a 900s agent-phase timeout on `git-multibranch`.
    Budget issue, not a code issue.

### Decision

Record as baseline. No components promoted yet — all remain ideas
that happen to be wired up. Next: paired ablations per component,
and a stronger-model run to separate "agent broken" from "model too
weak".
