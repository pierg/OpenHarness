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
type" phase so failure modes are safe and resumable.

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

The orchestrator passes you via CLI arguments:

- `slug` (positional) — the experiment slug, also the branch name
  and the output filename stem for the design doc.
- `--idea=<id>` — the idea entry id in `lab/ideas.md` (or the
  literal string ``baseline`` for entries that don't have one).
- `--worktree=<path>` — the path to the per-experiment worktree
  (``../OpenHarness.worktrees/lab-<slug>/``). Read from it freely;
  do **not** write to it from this skill.
- `--hypothesis=<text>` — the one-line hypothesis from the roadmap
  entry.
- `--roadmap-body=<markdown>` — the full body of the roadmap entry
  (everything between the `### <slug>` header and the next entry),
  including `**Plan:**`, `**Cost:**`, `**Depends on:**` etc. Use
  this rather than re-reading `lab/roadmap.md` — the body here is
  exactly what the entry says at the moment preflight ran.
- `--design-path=<path>` — the absolute path to write the output
  `design.md` to (pre-created by the orchestrator).

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

## Slice

The slice is the contract the run phase will execute. **You MUST
satisfy [`lab/METHODOLOGY.md`](../../../lab/METHODOLOGY.md) §2** —
declare a shape, cite evidence, count trials, and confirm the
§6 verdict floor (`MIN_TRIALS_PER_LEG_FOR_VERDICT = 5`) is clearable.

Two named slices, both running on the **same** experiment spec — the
implement phase distinguishes them via the spec's `profiles:` block.

- **Smoke (validation only):** 1–4 fast, cached terminal-bench tasks
  the implement phase runs with `uv run exec <spec> --profile smoke`
  to prove the spec resolves, every leg starts, and at least one
  trial per leg completes without an uncaught exception. Pass / fail
  is **not** required — only "no crash". Reuse the standard smoke
  task list from `experiments/tb2-baseline.yaml > profiles.smoke`
  unless the variant needs different tasks (state which and why).
  **Smoke never produces a verdict** (METHODOLOGY §8 / §9).
- **Full (verdict-bearing):** the meaningful slice the run phase
  executes with `uv run exec <spec>` (no `--profile`). Pick exactly
  one allowed shape from METHODOLOGY §2:
    - `full-bench` — every task in `terminal-bench@2.0` (~89
      tasks/leg). Default for any variant claiming to move the
      aggregate pass rate. **Required for `Graduate` verdicts.**
    - `cluster: <names>` — only the task-feature clusters this
      variant claims to address. **You MUST cite Appendix B of
      METHODOLOGY.md** for the cluster sizes; in `tb2` only
      `python_data` and `python_ml` (n=7) clear the floor at
      `n_attempts=1`. Any other cluster needs `cluster_combined:`
      (DEFERRED, see ideas.md > Framework) or `paired-double`
      repetitions.
    - `near-miss` — the failing-by-≤K-turns subset of a prior run
      (budget / compaction / loop-guard style variants). Cite the
      prior `instance_id` and the exact selection criterion.
      **Selection-biased** — see METHODOLOGY §5; cannot produce a
      `Graduate` verdict.
    - `regression: <task_ids>` — explicit task list; only when
      targeting known failures. Cite the prior `instance_id`.
      Cannot produce a `Graduate` verdict.
- **Expected n_tasks per leg:** <number from the shape above>.
- **Total n_trials per leg:** `n_tasks × n_attempts` (see ## Slice
  > Repetitions below). Must be ≥ 5 (METHODOLOGY §6 floor).
- **Evidence justifying the shape:** cite the prior `instance_id`
  whose results motivate this slice (e.g. "max-turns-30 subset of
  `tb2-baseline-20260417-234913`"), or "first measurement, no
  prior" for genuinely new populations.

## Slice > Repetitions

How many times each `(leg, task)` cell runs. Bounds per-cell noise;
does NOT extend coverage. **You MUST satisfy METHODOLOGY §4** — pick
one mode and justify against the decision matrix there.

- **Mode:** one of `single-shot` (n_attempts=1) | `paired-double`
  (n_attempts=2) | `replication: r` (Graduate gate only, see
  METHODOLOGY §7).
- **Justification:** name the slice size band (small ≤ 30 / large
  ≥ 30 trials/leg) and the mechanism noise band (high if the
  variant has stochastic internal state — sampled plans, runtime
  nudges, retries; low for pure config / prompt tweaks). The
  matrix in METHODOLOGY §4 picks the mode from those two axes.
- **Spec mapping:** the chosen `n_attempts` becomes the
  experiment spec's global `n_attempts:` value. Per-leg overrides
  are forbidden (METHODOLOGY §8 anti-pattern).

## Slice > Control

What the comparison anchor is. Today only `fresh` is enforced;
the other shapes are documented for forward compatibility.

- **Mode:** `fresh` (default — control re-runs alongside the
  treatment in this experiment).
- DEFERRED: `historical: <instance_id>/<leg_id>` and
  `historical+replay: ...` borrow control trials from a prior
  experiment, gated on drift checks. See METHODOLOGY §5 and
  ideas.md > Framework. **Do not declare these today** — the
  implement phase will refuse them.

## Files to touch

A flat list, repo-relative. Be specific.

- `experiments/<spec-name>.yaml` — new file. Default = paired
  ablation (two legs); use 3-leg multi-arm only when the question's
  structure demands it (METHODOLOGY §3 — variable has > 2 levels,
  or two independent variables share a slice for ~50% marginal
  cost). Each leg differs from its control in **exactly one
  axis** — confounded ablations are forbidden (METHODOLOGY §8).
  MUST include `task_filter:` (the full slice) at the top level AND
  a `profiles.smoke` block (the smoke slice) so the implement phase
  can run both. The spec's `n_attempts:` is global per Slice >
  Repetitions above.
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
- [ ] **Smoke run:** `uv run exec <spec-name> --profile smoke`
      (always — confirms every leg starts, no leg ERRORs out, and
      no trial throws an uncaught exception). The implement phase
      blocks on this and refuses to mark itself `ok` until the
      smoke instance lands cleanly. Pass-rate of the smoke slice
      is **not** a validation criterion; only "no crash".

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
