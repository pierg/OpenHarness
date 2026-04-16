# Ideas

Backlog of agent improvements we might try. Append-only — when an idea
graduates into an experiment, leave it here and cross-reference the
entry in [`experiments.md`](experiments.md). When it then graduates
into a validated building block, add it to [`components.md`](components.md).

Keep entries short. Add detail inside an experiment only when we
actually try the idea.

## How to use

-   **Proposing**: add a new `### <idea-id>` section below with 1–3
    sentences of motivation and an expected experiment.
-   **Trying**: spin up a git worktree, create an entry in
    `experiments.md`, and do the work there. Only edit back to this
    file to flip the status line at the top of the idea.
-   **Graduating**: once at least one experiment shows positive
    impact, move the idea's summary into `components.md` and mark
    it `graduated` here.

Statuses: `proposed` • `trying` • `graduated` • `rejected` •
`parked`.

## Template

```markdown
### <idea-id>

**Status:** proposed
**Motivation:** one-sentence why.
**Sketch:** one-paragraph how.
**Expected experiment:** what we'd measure, on which slice.
```

---

## Proposed

### executor-bash-timeout-aware-retry

**Status:** proposed
**Motivation:** Long-running commands (builds, downloads) hit the
bash tool timeout and we have no recovery path.
**Sketch:** Detect timeouts, relaunch the command in background,
poll status. Or expose a `run_in_background=True` flag on the bash
tool.
**Expected experiment:** pass-rate delta on `build-*` tasks from
`tb2-baseline`.

### planner-rerank

**Status:** proposed
**Motivation:** First plan the planner produces is often mediocre.
**Sketch:** Generate N plans, rerank with a small judge model
(same family, smaller size), execute top-1.
**Expected experiment:** compare 1-plan vs 3-plan-rerank on
`planner_executor`; track pass rate and plan-quality subjective
notes.

### tool-result-summariser

**Status:** proposed
**Motivation:** Large tool results eat context and often drown
useful signal.
**Sketch:** After any tool result above K tokens, inject a short
summary before the next turn; keep full result in the trace but
hide from model.
**Expected experiment:** pass rate on long-running tasks
(compilebench, tarball-heavy tasks); token usage delta.

### skill-memory

**Status:** proposed
**Motivation:** Agents re-derive the same command sequences every
run.
**Sketch:** Persist successful command sequences to a task-local
`skills/` directory that the planner reads on its first turn.
**Expected experiment:** second-run pass rate on the same tasks;
first-meaningful-edit turn index.

### episodic-memory

**Status:** proposed
**Motivation:** Cross-task patterns (e.g. "how to read a Dockerfile
before editing") aren't reused.
**Sketch:** Indexed store of post-run reflections keyed by task
signature; planner pulls top-k before producing a plan.
**Expected experiment:** paired run with memory warm vs cold on a
diverse slice; pass rate, mean turn count.

## Trying

_(empty — move an idea here while an experiment is open)_

## Graduated

-   `grounded-planner-tools` → see `components.md`
-   `loop-guard` → see `components.md`
-   `web-tools` → see `components.md`
-   `planner-executor-critic` → see `components.md`
-   `critic-strict-verification` → see `components.md`
-   `extended-budget` → see `components.md`

## Rejected / parked

_(empty)_
