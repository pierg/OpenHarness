---
name: lab-operator
description: >
  Operate the autonomous lab loop end-to-end: start, stop, inspect,
  monitor, restart, and answer "what is the daemon doing?" Use when
  the user asks about the orchestrator, roadmap execution, per-slug
  phase state, logs, worktrees, or the next experiment to run.
  Routes to the per-phase skills only when the user explicitly asks
  for one phase by hand. Companion reference: `lab/OPERATIONS.md`.
---

# Operating the autonomous lab

The daemon now runs a **7-phase** loop:

1. `preflight`
2. `design`
3. `implement`
4. `run`
5. `critique`
6. `replan`
7. `finalize`

One experiment owns one worktree branch. Finalize must merge that
experiment's durable outcome back to `main` before the daemon can
advance to the next roadmap entry.

## First reads

When invoked, start with:

```bash
codex login status
uv run lab svc status
uv run lab info
uv run lab tree show
uv run lab phases show
uv run lab query "SELECT skill, exit_code, started_at FROM spawns ORDER BY started_at DESC LIMIT 5"
```

Summarize:

- whether the daemon is running
- which slug is active, if any
- current trunk
- latest finished or failed spawn
- whether any slug is stuck on a failed phase

## Core operator commands

Status:

```bash
uv run lab svc status
uv run lab daemon status
uv run lab phases show
uv run lab phases show <slug>
```

Lifecycle:

```bash
uv run lab svc start daemon
uv run lab svc stop daemon
uv run lab svc restart daemon
uv run lab daemon start --foreground --once
```

Logs:

```bash
uv run lab svc logs daemon
uv run lab svc logs daemon -f
ls -1t runs/lab/logs/ | head
tail -f runs/lab/logs/<spawn-log>
```

Recovery:

```bash
uv run lab phases reset <slug> --phase <phase>
uv run lab phases reset <slug>
uv run lab preflight list
uv run lab preflight remove <slug>
```

## Interpretation rules

- A failed `design`, `implement`, `replan`, or `finalize` phase is a
  skill failure. Read the latest spawn log first.
- A failed `run` or `critique` phase is usually infrastructure or data.
- There is no normal "waiting for human graduate confirm" state
  anymore. If a verdict is still `applied=false` in the DB, that
  means "pending merge" or "finalize failed", not "awaiting manual
  trunk approval".

## What not to do

- Do not open ad-hoc PRs outside finalize for an in-flight slug.
- Do not move the daemon past a finalize that has not merged.
- Do not mutate `lab/roadmap.md` by hand as part of close-out; that is
  replan's job unless the user explicitly asks for manual queue edits.
