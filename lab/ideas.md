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

### reflection-context-compaction

**Status:** proposed
**Motivation:** Reflection's worker conversation grows quadratically in
tokens because every turn re-sends the full history including raw
`bash`/`grep` stdout. The smoke run
`tb2-baseline-smoke-20260416-205703` saw both reflection trials hit
the 900 s harbor wall-clock at 6.4 M input tokens / $0.67 each — long
before the 30-turn worker budget could fire. Until this is fixed,
reflection is excluded from the baseline sweep (see
`experiments.md#reflection-context-blowup-on-smoke`).
**Sketch:** Truncate or summarize tool outputs above some threshold
(e.g. keep first/last 50 lines, replace middle with a `<truncated N
lines>` marker) before they re-enter the next turn's history. Could
live as a runtime hook on `query.py` so it benefits any architecture,
or as a `bash`/`grep` tool option. Either way it should be opt-in via
an `AgentConfig` flag so the basic loop stays unaffected until
measured.
**Expected experiment:** rerun reflection-only on the same smoke slice
(`exec rerun tb2-baseline-smoke-20260416-205703 -l reflection`),
target: both trials complete within wall-clock and at <500 k input
tokens each.

### grounded-planner-tools

**Status:** proposed
**Motivation:** With the current planner having no tools, Gemini
sometimes hallucinates ```tool_code``` blocks pretending to call
shell. Giving the planner read-only tools (`read_file`, `glob`,
`grep`) might let it ground the plan in real files instead.
**Sketch:** Wire `read_file`, `glob`, `grep` into the planner
subagent of `planner_executor`; tighten the planner system prompt
to be explicit that it has no execution tools.
**Expected experiment:** paired run on `tb2-baseline` smoke slice;
report pass rate, planner turn count, presence of `tool_code` in
trajectories.

### loop-guard

**Status:** proposed
**Motivation:** Some Gemini variants emit empty assistant turns
or repeat the same tool call indefinitely; without intervention the
agent silently exhausts its budget on no progress.
**Sketch:** Runtime mechanism (already in
`src/openharness/engine/loop_guard.py`, off by default) that
detects empty turns and identical tool-call streaks, injects a
short steering nudge, and gives up after a small budget.
**Expected experiment:** paired run with the guard on vs off on
`tb2-baseline` smoke slice; report pass rate, mean turn count,
nudges injected.

### web-tools

**Status:** proposed
**Motivation:** Some tasks need external documentation, source
tarballs, or upstream references that aren't in the sandbox image.
**Sketch:** Add `web_fetch` + `web_search` tools to the `basic`
agent and to the `planner_executor` planner/executor subagents.
**Expected experiment:** pass rate delta on a curated slice of
tasks that plausibly need the network (e.g. `build-*` that fetch
sources, tasks with explicit URLs in the instruction).

### planner-executor-critic

**Status:** proposed
**Motivation:** The reflection critic catches premature
completions for the simple `reflection` worker; it might do the
same for a richer `planner_executor` worker.
**Sketch:** Compose the existing `reflection` architecture with a
`planner_executor` worker (no new code; pure YAML composition) and
a strict critic prompt that fails reports lacking concrete
verification.
**Expected experiment:** paired run vs `planner_executor` alone on
the smoke slice; report pass rate, mean attempts per task,
token cost.

### extended-budget

**Status:** proposed
**Motivation:** The 30/8192 baseline sometimes hits the agent-phase
timeout on heavier `build-*` and `git-*` tasks. Raising to
60/16384 might convert near-misses into passes.
**Sketch:** Bump `defaults.max_turns` to 60 and `max_tokens` to
16384 in `experiments/tb2-baseline.yaml` (and matching agent
configs).
**Expected experiment:** paired run with both budgets on the same
slice; report pass rate, distribution of final turn index, total
tokens.

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

_(empty — no idea has earned a slot in `components.md` yet)_

## Rejected / parked

_(empty)_
