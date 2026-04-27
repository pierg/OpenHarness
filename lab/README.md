# Lab

The lab is the repo's autonomous experiment surface. Its job is to find
generalizable improvements to agentic harnesses and preserve the
evidence behind each decision.

## Files

| File | Purpose |
|------|---------|
| `configs.md` | current best config + rejected + proposed configs |
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
4. critique writes the experiment-critic decision on the experiment branch
5. replan rewrites the queue consequences on that same branch
6. finalize creates the required PR artifact(s) and merges them back to `main`
7. only then does the daemon pick the next roadmap entry

Durable experiment state lives on the experiment branch until finalize
lands it. The parent checkout should not accumulate side commits during
run/critique/replan.

## Decisions

- `accept`: merge accepted code + `lab/` updates
- `reject`: merge metadata-only `lab/` updates, preserve discarded implementation SHA
- `no_op`: merge metadata-only `lab/` updates, preserve discarded implementation SHA

The critic decision is a recommendation backed by evidence and
confidence. Replan decides what that evidence means for the queue.

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
