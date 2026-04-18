# Lab

The lab is the audit trail for agent improvements. Four short markdown
files cover the full lifecycle from "what if we…" to "this is now a
default building block".

## Files

| File | Contents |
|------|----------|
| [`ideas.md`](ideas.md) | Themed backlog of agent improvements we might try, plus the trying/graduated/rejected piles. |
| [`roadmap.md`](roadmap.md) | What's queued to run next, in priority order. Completed runs move to `## Done` with a link to the experiment. |
| [`experiments.md`](experiments.md) | Append-only log of concrete runs (newest at top): hypothesis, results, decision. |
| [`components.md`](components.md) | Validated building blocks — ideas that earned a measured impact and now show up as `components: [...]` in agent YAMLs. |

Tier-1 changes (bug fixes, small prompt tweaks we'd never revert) go
into [`../CHANGELOG.md`](../CHANGELOG.md), not here.

The four files above describe the **human-curated** view. Once an
idea is on `roadmap.md`, the rest of the loop runs autonomously —
see [`AUTONOMOUS.md`](AUTONOMOUS.md) for the daemon, the four
critic skills, the DuckDB schema, and the operating commands.

## Workflow

```
ideas.md "Proposed"
   │  promote to the queue
   ▼
roadmap.md "Up next"
   │  run it
   ▼
experiments.md  (new dated entry)
   │
   ├──► roadmap.md "Done"        (link back to the experiment)
   │
   ├──► ideas.md "Graduated"  ──► components.md  (positive impact)
   │
   └──► ideas.md "Rejected"                     (no value, with reason)
```

1.  An idea is captured in [`ideas.md`](ideas.md) under one of four
    themes (Architecture / Runtime / Tools / Memory). Two bullets:
    motivation and sketch.
2.  When the idea is worth committing to run, an entry is appended
    to [`roadmap.md`](roadmap.md) under `## Up next` with a
    hypothesis, plan, and rough cost. The idea entry moves to
    `## Trying`.
3.  When the experiment runs, a dated entry is appended to the top
    of [`experiments.md`](experiments.md). The roadmap entry moves
    to `## Done` with a link to the experiment.
4.  If the experiment shows positive impact, the idea moves to
    `## Graduated`, a new section appears in
    [`components.md`](components.md) citing the experiment, and the
    component id is added to the relevant agent YAML's
    `components: [...]` list.
5.  If the experiment shows no value, the idea moves to
    `## Rejected` with a one-line reason and a link to the
    experiment.

`ideas.md`, `experiments.md`, and `components.md` are append-only —
entries change state by moving sections and gaining cross-reference
bullets, never by being rewritten. `roadmap.md` is mutable —
`## Up next` can be reordered freely.

## Current state

-   **Baseline:** [`experiments/tb2-baseline.yaml`](../experiments/tb2-baseline.yaml)
    -   Agents: `basic`, `planner_executor`, `react`
    -   Model: `gemini-3.1-flash-lite-preview`
    -   Worker budget: 30 turns / 8192 tokens
    -   Sandbox: Harbor + Docker
    -   Trial concurrency: `n_concurrent=4` (smoke / demo: 2)
    -   Excluded: `reflection` — context-blowup, see
        [`ideas.md#reflection-context-compaction`](ideas.md#reflection-context-compaction)
-   **Completed experiments:** none. The lab was reset on 2026-04-17.
-   **Validated components:** none. The baseline ships with no opt-in
    components.
-   **Next experiment:**
    [`tb2-baseline-full-sweep`](roadmap.md#tb2-baseline-full-sweep) —
    full sweep on `terminal-bench@2.0` to anchor every future
    ablation.
