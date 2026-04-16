# Components

Agent building blocks that have graduated from [`ideas.md`](ideas.md)
via one or more experiments in [`experiments.md`](experiments.md).
Each component has a stable id, a toggle (lives in the agent YAML
under `components:`), a one-line hypothesis, and — once measured —
an impact summary.

## How to use

-   **Graduating an idea into a component**: add a section below
    with the id, scope, hypothesis, and link to the first
    experiment that wired it up. Status starts at `wired`.
-   **Once a paired experiment shows positive impact**: flip
    status to `validated` and fill in the "Impact" line.
-   **If a component becomes always-on in baselines**: flip status
    to `adopted`.
-   **If retired**: flip status to `retired` but keep the id and
    the historical notes — never reuse an id.
-   The list under each agent's `components:` in its YAML must
    match the ids here; enforcement is by convention (fast feedback
    when you grep).

Statuses: `wired` • `validated` • `adopted` • `retired`.

## Template

```markdown
### <component-id>

**Status:** wired
**Scope:** `<files touched>`
**Applies to:** `<agents that activate it>`
**Hypothesis:** one sentence.
**Wired in:** experiments.md#<slug>
**Impact:** _(pending)_
```

---

## Active

### grounded-planner-tools

**Status:** wired
**Scope:** `src/openharness/agents/configs/planner_executor.yaml`,
`src/openharness/agents/configs/planner_executor_critic.yaml`
**Applies to:** `planner_executor`, `planner_executor_critic`
**Hypothesis:** A planner with read-only tools (read_file, glob,
grep) produces executable plans instead of hallucinated
pseudo-tool-calls.
**Wired in:** [experiments.md#2026-04-16-tb2-baseline-phase4-smoke](experiments.md#2026-04-16--tb2-baseline-phase4-smoke)
**Impact:** _(qualitatively: no `tool_code` hallucinations in
smoke run; no quantitative ablation yet)_

### loop-guard

**Status:** wired
**Scope:** `src/openharness/engine/loop_guard.py`,
`src/openharness/engine/conversation.py`,
`src/openharness/runtime/session.py`
**Applies to:** all agents (runtime)
**Hypothesis:** Runtime detection of empty turns and identical
tool-call loops, with a short steering nudge, recovers trajectories
that would otherwise waste the turn budget.
**Wired in:** [experiments.md#2026-04-16-tb2-baseline-phase4-smoke](experiments.md#2026-04-16--tb2-baseline-phase4-smoke)
**Impact:** _(pending paired ablation)_

### web-tools

**Status:** wired
**Scope:** `src/openharness/agents/configs/default.yaml`,
`planner_executor.yaml`, `planner_executor_critic.yaml`
**Applies to:** `default`, `planner_executor`, `planner_executor_critic`
**Hypothesis:** Access to `web_fetch` + `web_search` unblocks tasks
that need external documentation or source tarballs.
**Wired in:** [experiments.md#2026-04-16-tb2-baseline-phase4-smoke](experiments.md#2026-04-16--tb2-baseline-phase4-smoke)
**Impact:** _(pending slice that specifically requires web)_

### planner-executor-critic

**Status:** wired
**Scope:** `src/openharness/agents/configs/planner_executor_critic.yaml`,
`src/openharness/agents/architectures/reflection.py`
**Applies to:** `planner_executor_critic` (opt-in agent)
**Hypothesis:** A reflection loop with a critic catches premature
completions and lifts pass rate on tasks where verification is
cheap.
**Wired in:** [experiments.md#2026-04-16-tb2-baseline-phase4-smoke](experiments.md#2026-04-16--tb2-baseline-phase4-smoke)
**Impact:** _(pending paired run vs `planner_executor`)_

### critic-strict-verification

**Status:** wired
**Scope:** `src/openharness/agents/configs/planner_executor_critic.yaml`
**Applies to:** `planner_executor_critic`
**Hypothesis:** A critic that fails reports lacking concrete
verification evidence outperforms a lenient critic.
**Wired in:** [experiments.md#2026-04-16-tb2-baseline-phase4-smoke](experiments.md#2026-04-16--tb2-baseline-phase4-smoke)
**Impact:** _(pending ablation vs lenient critic)_

### extended-budget

**Status:** wired
**Scope:** `src/openharness/agents/configs/*.yaml`,
`experiments/tb2-baseline.yaml`
**Applies to:** all baseline agents
**Hypothesis:** Raising defaults from 30 turns / 8192 tokens to
60 / 16384 converts near-misses into passes without hurting
efficiency enough to matter.
**Wired in:** [experiments.md#2026-04-16-tb2-baseline-phase4-smoke](experiments.md#2026-04-16--tb2-baseline-phase4-smoke)
**Impact:** _(pending distribution-of-final-turn analysis)_

## Retired

_(empty)_
