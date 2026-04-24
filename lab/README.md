# Lab

The lab is the repo's autonomous experiment surface.

## Files

| File | Purpose |
|------|---------|
| `configs.md` | configuration tree: trunk + branches + rejected + proposed |
| `components.md` | catalog of atoms and their status |
| `experiments.md` | append-only journal of experiment outcomes |
| `ideas.md` | themed backlog |
| `roadmap.md` | ranked execution queue |

## Execution model

One experiment owns one branch.

1. daemon starts on synced `main`
2. preflight creates `lab/<slug>` worktree from `main`
3. the slug runs through:
   `preflight → design → implement → run → critique → replan → finalize`
4. critique materializes the verdict on the experiment branch
5. replan rewrites the queue consequences on that same branch
6. finalize creates the required PR artifact(s) and merges them back to `main`
7. only then does the daemon pick the next roadmap entry

This means the parent repo no longer accumulates side commits to
`lab/` during run/critique. Durable experiment state lives on the
experiment branch until finalize lands it.

## Verdicts

- `add_branch`: merge accepted code + `lab/` updates
- `graduate`: merge accepted code + `lab/` updates
- `reject`: merge metadata-only `lab/` updates, preserve discarded implementation SHA
- `no_op`: merge metadata-only `lab/` updates, preserve discarded implementation SHA

The normal flow does **not** require a human `graduate confirm`
step. That path is legacy-only.

## Planning

`ideas.md` is the backlog. `roadmap.md` is the queue.

The postmortem planner is the dedicated `replan` phase:

- moves the finished slug to `## Done`
- reprioritizes `## Up next`
- can add, demote, or remove queue entries
- may still write lower-confidence work to `### Suggested` or
  `## Auto-proposed`

## Where to read more

- `lab/METHODOLOGY.md` for experiment-design and verdict thresholds
- `lab/OPERATIONS.md` for daemon/service/worktree operation
- `.agents/skills/lab/SKILL.md` for the skill-level routing model
