---
name: lab-plan-next
description: >
  Manage `lab/roadmap.md` when a human wants to add, reorder,
  promote, demote, remove, or inspect queue entries outside the
  daemon's automatic replan step. Use when the user says "queue X
  for next", "what's on deck?", "move X up", "demote X", or "clean
  up the roadmap". Edits `lab/roadmap.md`, and may move the linked
  idea entry to `## Trying` when a brand-new queued experiment is
  created from a proposed idea.
---

# Lab — Plan Next

The roadmap is the ranked execution queue. This skill is the human
operator's queue editor.

The daemon also edits the roadmap now, but only during the dedicated
`replan` phase on an experiment branch. Use this skill when a human
is making a queue decision outside that automatic close-out path.

## What this skill is for

- add a new `## Up next` entry
- inspect the current queue
- reorder `## Up next`
- promote a `### Suggested` entry
- demote or remove an entry

It is not the daemon's postmortem planner. That is
[`lab-replan-roadmap`](../lab-replan-roadmap/SKILL.md).

## Preferred commands

Add:

```bash
uv run lab roadmap add <slug> \
  --idea <idea-id> \
  --hypothesis "<...>" \
  --plan "<...>"
```

Promote/demote/remove:

```bash
uv run lab roadmap promote <slug>
uv run lab roadmap demote <slug>
uv run lab roadmap remove <slug> --section up-next
```

Reorder:

```bash
uv run lab roadmap move <slug> --before <other-slug>
uv run lab roadmap move <slug> --after <other-slug>
uv run lab roadmap move <slug> --to-top
uv run lab roadmap move <slug> --to-bottom
```

## Rules

- Read `lab/roadmap.md` first and summarize the top of `## Up next`
  before making changes when the user's intent is ambiguous.
- When queueing a brand-new experiment from a proposed idea, ensure
  the idea exists first. If needed, use `lab-propose-idea`.
- Do not touch source files, PRs, or experiment outputs here.
- Do not use this skill to close a finished experiment; the daemon's
  `replan` phase already owns that path.
