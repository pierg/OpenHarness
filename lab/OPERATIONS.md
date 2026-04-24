# Operations

Operating guide for the autonomous lab loop.

## The loop

The daemon runs a resumable **7-phase** pipeline per roadmap slug:

1. `preflight`
2. `design`
3. `implement`
4. `run`
5. `critique`
6. `replan`
7. `finalize`

Per-slug state lives in `runs/lab/state/<slug>/phases.json`. A restart
resumes from the first unfinished phase.

## Core architecture

- parent repo starts on synced `main`
- preflight creates worktree branch `lab/<slug>`
- all durable experiment edits live on that worktree branch
- critique materializes the verdict on the branch
- replan writes the roadmap/idea consequences on the branch
- finalize creates 1 or more PR artifacts and merges them back to `main`
- only after that merge does the daemon pick the next roadmap entry

There is no normal "open PR now, wait for a later tick to merge it"
mode, and there is no normal human `graduate confirm` gate.

## Phase ownership

| Phase | Owner | Output |
|------|-------|--------|
| `preflight` | deterministic Python | worktree path, branch, base SHA |
| `design` | `lab-design-variant` | `runs/lab/state/<slug>/design.md` |
| `implement` | `lab-implement-variant` | worktree commits + `implement.json` |
| `run` | deterministic Python | `runs/experiments/<instance-id>/...` + journal stub |
| `critique` | deterministic Python + Gemini trial critics + Codex aggregate critics | branch-local tree verdict + journal narrative |
| `replan` | `lab-replan-roadmap` | branch-local roadmap/ideas updates + `replan.json` |
| `finalize` | `lab-finalize-pr` | merged PR outcome + `finalize.json` |

## Important commands

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
uv run lab daemon start --foreground --once --dry-run
uv run lab daemon pause-after run --slug <slug>
uv run lab daemon clear-pause-after
```

Use `pause-after run` instead of stopping the service mid-run. The
barrier trips only after the selected phase finishes cleanly, switches
daemon mode to `paused`, and leaves the next phase pending for resume.

Recovery:

```bash
uv run lab phases reset <slug> --phase <phase>
uv run lab phases reset <slug>
uv run lab preflight list
uv run lab preflight remove <slug>
```

## File ownership

| Surface | Human | Daemon / skills |
|---------|-------|------------------|
| `lab/ideas.md > ## Proposed / Trying / Graduated / Rejected` | yes | no |
| `lab/ideas.md > ## Auto-proposed` | review / promote / reject | yes |
| `lab/roadmap.md > ## Up next` | yes | yes, during `replan` |
| `lab/roadmap.md > ## Up next > ### Suggested` | yes | yes |
| `lab/roadmap.md > ## Done` | review | yes, during `replan` |
| `lab/experiments.md` | no manual close-out editing | yes |
| `lab/configs.md` | rare manual repair only | critique/finalize flow |
| `lab/components.md` | rare manual repair only | critique/finalize flow |
| `src/openharness/agents/configs/trunk.yaml` | rare manual repair only | critique/finalize flow |

## Finalize rules

- `add_branch` / `graduate`: merge accepted code + `lab/` changes
- `reject` / `no_op`: merge metadata-only `lab/` changes, keep
  rejected implementation out of `main`, record discarded SHA

Finalize must not return success without a merged PR outcome.

## Portable runs via GCS

Use GCS as a mirror for portable artifacts, not as a mirror of the
entire `runs/` tree.

```bash
export OPENHARNESS_RUNS_GCS_URI=gs://<bucket>/<prefix>

uv run lab runs push-gcs
uv run lab runs pull-gcs

uv run lab runs push-gcs --instance-id <instance_id>
uv run lab runs pull-gcs --instance-id <instance_id>
```

Portable:

- `runs/experiments/<id>/`
- `runs/lab/task_features/`
- `runs/lab/cross_experiment/`
- `runs/lab/components_perf/`
- `runs/lab/auto_proposed/`
- `runs/lab/spawns/`

Local-only:

- `runs/lab/trials.duckdb`
- `runs/lab/daemon-state.json`
- `runs/lab/state/`
- lock files
- logs / web command audit

Optional daemon auto-push:

```bash
export OPENHARNESS_RUNS_GCS_AUTO_PUSH=1
```

## What to inspect when stuck

1. `uv run lab phases show <slug>`
2. newest file in `runs/lab/logs/`
3. `uv run lab svc logs daemon -f`
4. `uv run lab query "SELECT skill, exit_code, started_at FROM spawns ORDER BY started_at DESC LIMIT 20"`

Interpretation:

- `design` / `implement` / `replan` / `finalize` failures are usually skill failures
- `run` failures are usually execution/infrastructure
- `critique` failures are usually ingest/data/Gemini trial-critic issues

## Critic model policy

- `trial-critic` runs through Gemini CLI from
  `critic/trial-evidence.json`; default model is
  `gemini-3.1-pro-preview` via `OPENHARNESS_GEMINI_TRIAL_MODEL`.
- Compare Pro vs Flash without overwriting canonical critiques:
  `uv run lab trial-critic-shadow <instance_id> --models gemini-3.1-pro-preview,gemini-3-flash-preview`.
- Judgment-heavy phases stay on Codex `gpt-5.5` with `xhigh`
  reasoning: experiment critic, cross-experiment critic, replan,
  finalize, design, and implement.

## Legacy notes

`lab graduate confirm` and the old staged-graduate workflow remain only
for historical cleanup. They are not part of the normal autonomous
path anymore.
