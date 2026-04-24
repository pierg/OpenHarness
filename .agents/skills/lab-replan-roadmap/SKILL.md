---
name: lab-replan-roadmap
description: >
  Phase 5 of the autonomous lab pipeline. After critique has written
  the verdict and branch-local lab state, perform the deep
  postmortem: move the finished slug to `## Done`, update the main
  `## Up next` queue, add or demote roadmap entries, optionally write
  `### Suggested` / `## Auto-proposed` follow-ups, and emit a summary
  JSON payload for the runner. Use when the orchestrator daemon
  invokes replan for a slug whose `phases.json` shows `replan:
  pending`.
---

# Lab — Replan Roadmap

This is the explicit reflection step between critique and finalize.

Its job is not just "suggest ideas". Its job is to turn one finished
experiment into the next coherent queue state that will be merged back
to `main`.

## What you own

You may edit, on the experiment worktree:

- `lab/roadmap.md`
- `lab/ideas.md`
- `lab/experiments.md > ### Linked follow-ups` via deterministic helpers

You do not edit source code here.

## Inputs

The orchestrator passes:

- `slug`
- `worktree`
- `instance-id`
- `verdict`
- `replan-json`
- optional repair arguments

The worktree already contains:

- the finished journal entry
- the branch-local `Tree effect`
- any tree/catalog mutations from critique

## Required outcome

When this phase succeeds, the branch should contain the durable
planning consequences of the experiment:

- the just-ran slug is moved to `## Done`
- `## Up next` reflects the new priority order
- low-signal or obsolete items may be demoted / removed
- new concrete entries may be added directly to `## Up next`
- lower-confidence follow-ups may land in `### Suggested`
- abstract ideas may land in `## Auto-proposed`

This phase is allowed to rewrite the main queue. That is the point.

## Read first

Inside the worktree, inspect:

```bash
uv run lab tree show --json
uv run lab query "SELECT slug, kind, target_id, applied, applied_by, applied_at
                  FROM tree_diffs ORDER BY applied_at DESC NULLS LAST LIMIT 10"
```

And read:

- `lab/roadmap.md`
- `lab/ideas.md`
- the current journal entry in `lab/experiments.md`
- the most recent `runs/lab/cross_experiment/*.json` snapshot if it exists

## Replan heuristics

Apply these in order.

### 1. Close the finished slug

Move the current roadmap entry to `## Done` with:

- a `Ran:` link to `runs/experiments/<instance-id>`
- an `Outcome:` line summarizing verdict + headline result

Use the CLI helper when possible:

```bash
uv run lab roadmap done <slug> \
  --ran "[runs/experiments/<instance-id>](../runs/experiments/<instance-id>)" \
  --outcome "<verdict>: <one-line summary>"
```

### 2. Decide what should be next

Use the experiment evidence, not generic backlog intuition.

- `graduate`: queue follow-up ablations against the new trunk, confirm or re-rank existing branch work, and demote stale entries anchored to the old trunk.
- `add_branch`: prioritize validation of the new specialization on the cluster(s) it claims to help, plus any obvious sibling-cluster falsification run.
- `reject`: demote or remove roadmap items that depended on the rejected direction unless the evidence still justifies them.
- `no_op`: prefer narrower or better-powered reruns only when the result was genuinely inconclusive; otherwise demote the direction.

### 3. Mutate the queue directly

Use the deterministic helpers:

```bash
uv run lab roadmap add <slug> ...
uv run lab roadmap move <slug> --before <other-slug>
uv run lab roadmap move <slug> --after <other-slug>
uv run lab roadmap move <slug> --to-top
uv run lab roadmap demote <slug>
uv run lab roadmap remove <slug> --section up-next
```

Do not hand-edit when a CLI helper exists.

### 4. Use Suggested / Auto-proposed only for lower-confidence work

`### Suggested` and `## Auto-proposed` remain valid escape valves, but
they are not the primary output anymore. Prefer concrete queue changes
when the evidence is strong enough.

Use:

```bash
uv run lab roadmap suggest <slug> --hypothesis "<...>" --source "lab-replan-roadmap@$(date +%Y-%m-%d)"
uv run lab idea auto-propose <idea-id> --motivation "<...>" --sketch "<...>" --source "lab-replan-roadmap@$(date +%Y-%m-%d)"
```

### 5. Refresh `### Linked follow-ups`

After queue/idea mutations, refresh the journal cross-links:

```bash
uv run lab experiments synthesize <slug> --section "Linked follow-ups"
```

## `replan-json` contract

Write a compact summary object the runner can persist. Example:

```json
{
  "summary": "moved slug to Done; promoted branch-validation follow-up to top of queue",
  "done_slug": "loop-guard-paired",
  "queue_top": ["loop-guard-on-build-cluster", "tb2-baseline-refresh"],
  "added": ["loop-guard-on-build-cluster"],
  "moved": ["tb2-baseline-refresh"],
  "demoted": ["old-loop-guard-wide-rerun"],
  "removed": [],
  "suggested": ["loop-guard-on-sibling-cluster"],
  "auto_proposed": ["loop-guard-cross-cluster-generalization"]
}
```

Keep it small and factual.

## Constraints

- Do not touch source files outside `lab/`.
- Do not create or merge PRs here.
- Do not leave the finished slug in `## Up next`.
- Do not blindly append suggestions without considering whether the main queue itself should change.
