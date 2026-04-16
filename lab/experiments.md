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
