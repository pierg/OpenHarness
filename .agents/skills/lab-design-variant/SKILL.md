---
name: lab-design-variant
description: >
  Phase 1 of the autonomous lab pipeline. Take an idea + roadmap entry,
  read the relevant parts of the codebase, and write a concrete
  variant-design document (`design.md`) that the next phase will
  implement. Read-only: this skill never modifies source files. Use
  when invoked by the orchestrator daemon for a roadmap entry whose
  pipeline state shows `design: pending`, or when the operator says
  "design the variant for X" / "plan the implementation for X" without
  actually editing code yet. Companion skills: lab,
  lab-implement-variant, lab-finalize-pr, lab-run-experiment (deprecated).
---

# Lab — Design Variant

Take an idea + roadmap entry and produce a single artifact: a
**design document** the next phase (`lab-implement-variant`) will
follow. You touch zero source files — this is the "think before you
type" phase, and it runs read-only by design so failure modes are
safe and resumable.

## When to Use

- The orchestrator daemon spawns you for a roadmap entry whose
  ``runs/lab/state/<slug>/phases.json`` has ``design: pending``.
- The operator says "design X first, don't implement yet" or
  "what would it take to add X?" and wants a written plan.

Do **not** use this skill for:

- Capturing an idea in `lab/ideas.md` → `lab-propose-idea`.
- Editing source code → `lab-implement-variant` (phase 2).
- Re-running a failed experiment → the orchestrator handles retries
  at the phase level; just rerun this skill if `design.md` is
  missing or stale.

## Inputs

The orchestrator passes you (via the prompt):

- `slug` — the experiment slug, also the branch name and the
  filename for the design doc.
- `idea_id` — the idea entry in `lab/ideas.md` driving this work
  (or the literal string ``baseline snapshot`` / ``infrastructure``
  for entries that don't have one).
- `worktree` — the path to the per-experiment worktree
  (``../OpenHarness.worktrees/lab-<slug>/``). Read from it freely;
  do **not** write to it from this skill.
- `roadmap_entry` — the markdown of the matching roadmap entry,
  including its `**Hypothesis:**` and `**Plan:**` lines.

## Output

Exactly one file:

```
runs/lab/state/<slug>/design.md
```

(The orchestrator pre-creates the directory; you just write the file.)

The file is markdown with these sections, in this order. Keep each
section short — this is a checklist for the implementer, not an essay.

```markdown
# Design — <slug>

## Goal

<1-2 sentences restating what success looks like. Quote the
roadmap entry's Hypothesis verbatim if useful.>

## Scope

- **Spec name:** <name of the experiment YAML to be created at
  `experiments/<spec-name>.yaml`. Defaults to the slug.>
- **Variant kind:** `agent-config` | `runtime-component` | `prompt`
  | `tool` | `model` | `experiment-spec-only`
- **Trunk anchor:** <which trunk agent the variant builds on, e.g.
  `basic`. Read from `uv run lab trunk show`.>
- **Mutation summary:** <one sentence — this becomes the journal
  entry's `Mutation:` bullet later.>

## Files to touch

A flat list, repo-relative. Be specific.

- `experiments/<spec-name>.yaml` — new file, paired ablation, two
  legs (trunk vs. trunk+variant).
- `src/openharness/agents/configs/<variant>.yaml` — new agent
  config layering the variant component(s) on top of trunk.
- `src/openharness/components/<area>/<variant>.py` — new component
  implementation. (Only list what's actually new.)

## Implementation sketch

A concrete recipe the implement phase can follow without re-reading
the codebase. Aim for 5–15 bullets total. For each non-trivial code
change, name the function/class to add or modify, and the imports
required.

## Validation

Which deterministic checks the implement phase must run before
declaring success:

- [ ] `uv run lab components --validate` (if components.md was
      edited or a new component file landed).
- [ ] `uv run plan <spec-name>` (always — confirms the spec
      resolves and shows the resulting leg list).
- [ ] `pytest tests/<area>/...` (only if a unit test exists or
      should exist for the change).

## Risks / open questions

Anything the implementer should pause on before committing —
ambiguities in the idea, missing context, alternative shapes you
considered. If empty, write "_none_".
```

## Instructions

1. **Read the inputs.** Open `lab/ideas.md`, `lab/roadmap.md`,
   `lab/configs.md`, `lab/components.md` to ground yourself in the
   current trunk and what variants already exist. Use `Grep` /
   `SemanticSearch` over the codebase to confirm the files you'll
   touch actually exist (or to identify the right place to add
   new files).
2. **Pick the smallest scope that tests the hypothesis.** A
   "design" that touches 8 files is almost always over-spec'd.
   Prefer `experiment-spec-only` whenever the existing code can
   already express the variant via a YAML toggle.
3. **Write the design doc** to
   `runs/lab/state/<slug>/design.md` using the template above.
   No source-file edits.
4. **Sanity-check** by re-reading the doc end-to-end: every file
   listed under "Files to touch" must be addressed in the
   "Implementation sketch", and every check listed under
   "Validation" must be runnable.

That's it. No git commits, no journal edits, no codex spawns. The
orchestrator notices `design.md` exists, marks `design: ok` in
`phases.json`, and hands off to `lab-implement-variant`.

## Anti-patterns

- **Don't speculate about results.** This file describes what to
  build, not what we expect to find. Hypotheses live in the
  roadmap entry / `lab/experiments.md` journal, not here.
- **Don't write code.** Even sketched-out code blocks belong in
  the implement phase's commit, not in `design.md`. Pseudocode
  bullets are fine; full functions are not.
- **Don't re-justify the idea.** If the idea is bad, the right
  move is to refuse with a one-paragraph note and let the
  orchestrator demote the entry. Don't pad `design.md` with
  defensive prose.
