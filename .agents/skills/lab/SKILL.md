---
name: lab
description: >
  Router for the OpenHarness fork's autonomous-research lab. Use
  when the user mentions `lab/`, the experimentation framework,
  roadmap / ideas / experiments / configs / components, verdicts,
  or the daemon. Points at the action skills:
  `lab-propose-idea`, `lab-plan-next`, `lab-design-variant`,
  `lab-implement-variant`, `lab-replan-roadmap`, `lab-finalize-pr`,
  `lab-operator`, plus legacy escape hatches like
  `lab-reflect-and-plan` and `lab-graduate-component`.
---

# Lab — Autonomous Experiment Loop

The lab is a compact audit surface around an autonomous experiment
daemon.

## Core files

| File | Role |
|------|------|
| `lab/configs.md` | configuration tree: trunk + branches + rejected + proposed |
| `lab/components.md` | catalog of atoms and their status |
| `lab/experiments.md` | append-only experiment journal |
| `lab/ideas.md` | themed backlog |
| `lab/roadmap.md` | ranked execution queue |

`configs.md` is state. `experiments.md` is the log. `components.md`
is the derived catalog view.

## New loop

One roadmap slug goes through one branch-owned experiment:

1. start from synced `main`
2. create worktree branch `lab/<slug>`
3. run `preflight → design → implement → run → critique → replan → finalize`
4. finalize creates the required PR artifact(s) and merges them back to `main`
5. only then does the daemon pick the next slug

Important consequence: the daemon no longer writes `lab/` directly on
the parent repo during run/critique/finalize. Durable experiment state
lives on the worktree branch until finalize merges it.

## Verdict handling

- `add_branch`: merge the experiment branch back to `main`
- `graduate`: merge the experiment branch back to `main`
- `reject` / `no_op`: merge a metadata-only PR back to `main`; do not
  merge rejected implementation code

There is no normal human `graduate confirm` gate anymore. That path is
legacy-only for historical staged rows.

## Planning ownership

- `lab-replan-roadmap` is the authoritative postmortem planner. It can
  move the finished slug to `## Done`, reorder `## Up next`, demote or
  remove entries, and add new ones.
- `lab-reflect-and-plan` is legacy advisory-only.
- `lab-plan-next` is the human queue editor outside the daemon close-out path.

## Which skill to use

- capture a new idea: `lab-propose-idea`
- edit the queue manually: `lab-plan-next`
- operate the daemon: `lab-operator`
- implement one phase by hand: `lab-design-variant`, `lab-implement-variant`, `lab-replan-roadmap`, `lab-finalize-pr`

See `lab/README.md`, `lab/METHODOLOGY.md`, and `lab/OPERATIONS.md`
for the human-facing docs.
