---
name: lab-reflect-and-plan
description: >
  Legacy advisory planner. The daemon no longer uses this skill for
  close-out; the authoritative postmortem step is
  `lab-replan-roadmap`, which rewrites the real queue on the
  experiment branch before finalize. Use this only when a human wants
  a read-mostly "what should we try next?" pass without directly
  reprioritizing `## Up next`.
---

# Lab — Reflect and Plan (Legacy Advisory)

This skill is now advisory-only.

The daemon's real postmortem phase is
[`lab-replan-roadmap`](../lab-replan-roadmap/SKILL.md). That phase:

- moves the just-ran slug to `## Done`
- rewrites the main `## Up next` queue when warranted
- records follow-ups that will actually merge back to `main`

Use this legacy skill only when the user explicitly wants a softer,
non-authoritative reflection pass.

## Allowed writes

If you do write, keep it limited to:

- `lab/roadmap.md > ## Up next > ### Suggested`
- `lab/ideas.md > ## Auto-proposed`

Do not mutate the main queue here.

## Preferred workflow

1. Read the current tree, roadmap, ideas, recent journal entries, and
   latest cross-experiment snapshot.
2. Summarize the strongest evidence-backed follow-ups.
3. If the user wants them recorded, write them as Suggested entries or
   Auto-proposed ideas.

## When not to use this

- Any daemon-driven close-out path.
- Any workflow that intends the result to become the new queue state.
- Any request to move slugs to `## Done`, reorder `## Up next`, or
  demote/remove entries directly. Use `lab-replan-roadmap` or
  `lab-plan-next` instead.
