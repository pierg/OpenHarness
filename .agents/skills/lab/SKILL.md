---
name: lab
description: >
  Router for the OpenHarness fork's agent-iteration framework. Use when
  the user mentions "lab/", asks how to track an agent idea, asks how
  the experimentation framework works, asks where ideas/experiments/
  components are tracked, or you encounter the lab/ directory and need
  to know how to interact with it. Points at three action skills:
  lab-propose-idea, lab-run-experiment, lab-graduate-component.
---

# Lab — Agent Iteration Framework

The `lab/` folder at the repo root holds the audit trail for agent
improvements. Three append-only markdown files, no validation code:

| File | Purpose |
|------|---------|
| [`lab/ideas.md`](../../../lab/ideas.md) | Backlog of things to try. Cheap to add. |
| [`lab/experiments.md`](../../../lab/experiments.md) | Append-only log of concrete runs (newest at top). |
| [`lab/components.md`](../../../lab/components.md) | Ideas that graduated into building blocks, with measured impact. |

Tier-1 changes (bug fixes, small prompt tweaks we'd never revert) go
into [`CHANGELOG.md`](../../../CHANGELOG.md) instead — not the lab.

## When to Use

Use this skill (and pick the right action skill below) when the user:

- says "I have an idea for the agent" / "what if we…" → use
  **`lab-propose-idea`**
- says "let's try X" / "run an experiment for X" / "test this on
  tb2-baseline" → use **`lab-run-experiment`**
- says "promote X" / "graduate X" / "X worked, let's adopt it" → use
  **`lab-graduate-component`**
- asks "how is X tracked?" or "is X already wired up?" → answer by
  reading the three lab files; no action skill needed.
- asks "what's our experimentation framework?" → explain this
  three-file flow in 2–3 sentences and point at the three files.

## Lifecycle in one diagram

```
[ideas.md "Proposed"]
        │  user says "let's try it"
        ▼
[ideas.md "Trying"]  ←──── lab-run-experiment ────►  [experiments.md "<date> — <slug>"]
        │  experiment shows positive impact            │
        ▼                                              │
[ideas.md "Graduated"]  ←─ lab-graduate-component ────┘
        │
        ▼
[components.md "Active"]   (id appears in agent YAML "components:")

If an experiment shows no value:
[ideas.md "Rejected / parked"]   (experiment stays in experiments.md as evidence)
```

## Conventions Common to All Lab Skills

- All three lab files are **append-only**. Never rewrite an old
  entry; supersede it with a new one and link back.
- Use stable kebab-case ids for ideas and components
  (`loop-guard`, `planner-rerank`, `episodic-memory`). Once an id
  appears in any lab file, never reuse it for something else.
- An idea graduates into a component **only if at least one
  experiment in `experiments.md` justifies it.** No promotion
  without evidence.
- Component ids surface in the agent YAMLs as
  `components: [...]` — see the existing entries in
  `src/openharness/agents/configs/*.yaml`. The list is plain
  metadata (no validation); keeping it accurate is by convention.

## Other Useful Reads

- [`docs/runs.md`](../../../docs/runs.md) — how `runs/experiments/`
  is laid out (each `lab/experiments.md` entry should link a
  matching directory there).
- [`experiments/tb2-baseline.yaml`](../../../experiments/tb2-baseline.yaml)
  — the canonical experiment spec used by `uv run exec`.
