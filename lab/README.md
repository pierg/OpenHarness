# Lab

The lab is the repo's autonomous experiment surface. Its job is to find
generalizable improvements to agentic harnesses and preserve the
evidence behind each experiment evaluation.

## Files

| File | Purpose |
|------|---------|
| `configs.md` | operational baseline + rejected + proposed configs |
| `components.md` | catalog of reusable building blocks |
| `experiments.md` | append-only journal of experiment outcomes |
| `ideas.md` | themed backlog |
| `roadmap.md` | ranked execution queue |

## Execution model

One experiment owns one branch.

1. daemon starts on synced `main`
2. preflight creates `lab/<slug>` worktree from `main`
3. the slug runs through:
   `preflight → design → implement → run → critique → replan → finalize`
4. critique writes the experiment-critic evaluation on the experiment branch
5. replan rewrites the queue consequences on that same branch
6. finalize opens the canonical experiment PR and syncs the outcome to `main`
7. only then does the daemon pick the next roadmap entry

Durable experiment state lives on the experiment branch until finalize
lands it. The parent checkout should not accumulate side commits during
run/critique/replan.

Every experiment ID has one canonical audit path:

- experiment ID: `runs/experiments/<instance-id>/`
- branch: `lab/<slug>`
- PR: the GitHub PR opened from `lab/<slug>`

That PR is the record of what the candidate changed, including the
config YAML and any source edits. Accepted experiments merge it.
Rejected and no-op experiments get a final verdict comment and are
closed unmerged so their implementation diff remains inspectable.
If lab metadata still needs to land for a rejected/no-op experiment,
finalize syncs that metadata separately while keeping the evaluation and
journal linked to the canonical experiment PR.

## Evaluations And Rankings

- `accept`: comment on and merge the canonical experiment PR
- `reject`: comment on and close the canonical experiment PR, preserve discarded SHA
- `no_op`: comment on and close the canonical experiment PR, preserve discarded SHA

The critic evaluation is a recommendation backed by evidence and
confidence. It decides PR disposition, not global best.

The leaderboard is a separate dynamic view derived from
`experiments`, `legs`, `trials`, and `experiment_evaluations`. It ranks
comparable results by model id, dataset, evidence scope, pass rate,
cost, tokens, and duration. A valid no-op measurement can still change
the leaderboard; an accepted PR can later be outranked by newer
evidence.

## Planning

`ideas.md` is the backlog. `roadmap.md` is the queue.

The postmortem planner is the dedicated `replan` phase:

- moves the finished slug to `## Done`
- reprioritizes `## Up next`
- can add, demote, or remove queue entries
- may still write lower-confidence work to `### Suggested` or
  `## Auto-proposed`

## Manual operation

Humans can use the same pipeline without bypassing it:

- add a backlog item with `uv run lab idea append ...`
- queue or reorder work by editing `lab/roadmap.md` through
  `lab-plan-next`
- run the daemon one tick with `uv run lab daemon start --foreground --once`
- inspect or repair state with `uv run lab phases show <slug>` and
  `uv run lab phases reset <slug> --phase <phase>`
- invoke a phase skill directly only when repairing that phase's
  contract (`lab-design-variant`, `lab-implement-variant`,
  `lab-replan-roadmap`, or `lab-finalize-pr`)

Manual edits should preserve the lab methodology: benchmark evidence
may inform planning, while runtime policies and harness mechanisms
should generalize beyond one benchmark identity.

## Where to read more

- `lab/METHODOLOGY.md` for experiment-design guidance
- `lab/OPERATIONS.md` for daemon/service/worktree operation
- `.agents/skills/lab/SKILL.md` for the skill-level routing model
