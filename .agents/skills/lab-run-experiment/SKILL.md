---
name: lab-run-experiment
description: >
  Scaffold, run, and log a concrete agent experiment. Use when the user
  says "let's try X", "run an experiment for X", "test this on
  tb2-baseline", "compare A vs B", or asks for a paired ablation. Wires
  up a worktree (for risky work), edits lab/ideas.md and lab/experiments.md,
  invokes `uv run exec`, and fills in the results table from the run
  artifacts. Companion skills: lab, lab-propose-idea, lab-graduate-component.
---

# Lab — Run Experiment

Take an idea (or a baseline snapshot) and turn it into a logged
experiment with hypothesis, results, and a decision. Edits two lab
files and (optionally) creates a git worktree for isolation.

## When to Use

- User asks to actually try an idea ("let's try planner-rerank now",
  "run an A/B for loop-guard").
- User asks for a paired comparison or ablation.
- User asks to record a baseline run as a reference point.

Do **not** use this skill for:

- Just capturing an idea on paper → `lab-propose-idea`.
- Promoting a validated idea to a component → `lab-graduate-component`.

## Instructions

### 1. Identify the experiment

Establish in this order:

1. **Idea id** — must already exist (or about to exist) in
   `lab/ideas.md`. If the user hasn't proposed it yet, run the
   `lab-propose-idea` skill first, then continue here.
2. **Baseline** — what you compare against. Either a prior entry
   in `lab/experiments.md` or "off vs on" of the component being
   tested.
3. **Held-constant axes** — agent(s), model, dataset slice, budgets,
   sandbox.
4. **Run plan** — single run, or paired run (two legs that differ
   only in the variable under test).

State the plan back to the user in 3–5 lines and **wait for
confirmation** before doing anything destructive.

### 2. Pick a slug and date

```bash
date +%Y-%m-%d
```

The slug is `<idea-or-topic>-<short-context>`, e.g.
`loop-guard-tb2demo-paired`,
`planner-rerank-tb2demo`,
`tb2-baseline-phase4-smoke`.

### 3. Optional: isolate in a git worktree

For anything riskier than a one-off baseline (especially when editing
agent prompts, components, or configs), use a worktree. Skip for pure
runs that only invoke `uv run exec`.

```bash
WORKTREE=../OpenHarness_fork.worktrees/lab-<slug>
git worktree add "$WORKTREE" -b lab/<slug>
cd "$WORKTREE"
```

Tell the user the worktree path and branch. All subsequent edits and
runs happen inside the worktree until the experiment is decided.

### 4. Move the idea to "Trying" in lab/ideas.md

If the experiment is testing a previously-proposed idea, edit
`lab/ideas.md`:

- Cut the idea entry from `## Proposed`.
- Paste it under `## Trying`.
- Append one line: `**Trying in:** experiments.md#YYYY-MM-DD--<slug>`.

If the experiment has no associated idea (e.g. a baseline snapshot),
skip this step.

### 5. Add a stub entry at the top of lab/experiments.md

Insert the new section **above** all existing dated sections (newest
on top), using the template from `lab/experiments.md`:

```markdown
## YYYY-MM-DD — <slug>

**Status:** in-progress
**Hypothesis:** <one sentence — copy from idea's "Expected experiment">.
**Varying:** <component or change> on vs off  _(or: new agent vs baseline)_.
**Held constant:** agent(s), model, dataset slice, budgets, sandbox.
**Run:** _(filled after the run completes)_

### Results

| Leg | Trials | Passed | Errored | Pass rate | Total tokens |
|-----|-------:|-------:|--------:|----------:|-------------:|
|     |        |        |         |           |              |

### Notes

-   _(filled after the run completes)_

### Decision

-   _(filled after the run completes)_
```

### 6. Make the experiment-specific edits

Apply only the changes the experiment requires. Examples:

- Toggle a component on a leg (e.g. duplicate the leg in
  `experiments/tb2-baseline.yaml`, one with the component listed in
  `components:` and one without; or flip a runtime flag).
- Add a new agent variant under `src/openharness/agents/configs/`.
- Tweak a prompt section under test.

Keep the diff tight. Do not also fix unrelated bugs in this worktree.

### 7. Run the experiment

Use `uv run exec` against the chosen experiment YAML. For the demo
profile:

```bash
uv run exec tb2-baseline --profile demo 2>&1 | tee /tmp/lab-<slug>.log
```

For a custom spec:

```bash
uv run exec experiments/<your-spec>.yaml 2>&1 | tee /tmp/lab-<slug>.log
```

The run is long-lived — for any spec larger than `--profile demo`,
launch with `block_until_ms: 0` and poll with `Await`.

### 8. Locate the run directory

After the run completes:

```bash
ls -dt runs/experiments/* | head -1
```

That's `runs/experiments/<instance-id>/`. Inside you'll find:

- `experiment.json` — schema-versioned summary of legs and trials.
- `results/summary.md` — per-leg pass/fail/tokens table.
- `legs/<agent>/agent.resolved.yaml` — exact agent config used,
  including the `components:` list.
- `legs/<agent>/harbor/<instance>-<agent>/<task>/` — per-trial
  artifacts (`run.json`, `result.json`, trajectories).

### 9. Fill in the experiment entry

Read `runs/experiments/<instance-id>/results/summary.md` and the
relevant `legs/*/harbor/.../result.json` files. Update the entry in
`lab/experiments.md`:

- **Status:** in-progress → complete.
- **Run:** `runs/experiments/<instance-id>/`.
- **Results table:** copy numbers from `results/summary.md`.
- **Notes:** 3–6 short bullets — qualitative observations from
  trajectories (no `400` errors, planner hallucination count,
  loop-guard nudges fired, agent timeouts, etc.). For trajectory-
  level evidence, grep:

  ```bash
  rg -l "loop_guard_nudge" runs/experiments/<instance-id>/legs
  rg -l "thought_signature" runs/experiments/<instance-id>/legs
  ```

- **Decision:** one of:
  - `keep iterating` — try a follow-up experiment (record what to
    change next).
  - `graduate` — invoke `lab-graduate-component` next.
  - `reject / park` — move the idea from `## Trying` to
    `## Rejected / parked` in `lab/ideas.md` and explain why in the
    decision block.

### 10. Tidy up

- If you used a worktree and the experiment is complete:
  - Stage and commit the experiment-specific edits + the lab edits
    on the experiment branch (`lab/<slug>`). Do not push.
  - If decision is `reject / park`, optionally delete the worktree:
    `git worktree remove "$WORKTREE"` from the main checkout.
- If decision is `graduate`, hand off to `lab-graduate-component`
  with the slug.

Always finish with:

- The slug + date.
- The run directory path (repo-relative).
- Headline numbers (pass rates per leg).
- The decision and the next concrete step.

## Examples

### Example: paired ablation of an existing component

Input: "Run a loop-guard ablation on tb2 demo with planner_executor."

Output:

1. Propose plan: slug `loop-guard-tb2demo-paired`, baseline =
   `planner_executor` with `loop-guard` listed in `components:`,
   experimental = same agent with `LoopGuardConfig.enabled=False`
   plus the `loop-guard` tag dropped from `components:`. Held
   constant: `gemini-2.0-flash`, demo profile, 60 turns / 16384
   tokens, Docker sandbox. Confirm with user.
2. Create worktree `lab/loop-guard-tb2demo-paired`.
3. Add a new leg in `experiments/tb2-baseline.yaml` (or a copy of
   it) for the experimental variant.
4. Insert the stub at the top of `lab/experiments.md`.
5. `uv run exec tb2-baseline --profile demo`.
6. Read `runs/experiments/<instance>/results/summary.md`, fill the
   entry, write decision.

### Example: trying a brand-new idea

Input: "Let's try `tool-result-summariser`."

Output:

1. If the idea isn't yet in `lab/ideas.md` → invoke
   `lab-propose-idea` first.
2. Then proceed: slug `tool-result-summariser-smoke`. Confirm a
   minimal smoke plan with the user (e.g. demo profile, default
   agent, summariser injected at threshold K=2000 tokens).
3. Create worktree.
4. Move the idea to `## Trying`.
5. Implement the summariser behind a toggle.
6. Stub the experiment entry, run, fill results, decide.
