---
name: lab-graduate-component
description: >
  Promote a validated idea into a component. Use when the user says
  "graduate X", "promote X to a component", "X worked, let's adopt
  it", or asks to record measured impact for a component. Edits
  lab/ideas.md, lab/components.md, and (for adopted components) the
  relevant agent YAMLs. Refuses if no supporting experiment exists.
  Companion skills: lab, lab-propose-idea, lab-run-experiment.
---

# Lab — Graduate Component

Move a tried-and-validated idea from `lab/ideas.md` into
`lab/components.md`, link the supporting experiment, and (when
adopting) wire the component into the relevant baseline agent YAMLs.

## When to Use

- User says an experiment showed positive impact and asks to
  "graduate", "promote", "adopt", or "make it a component".
- User asks to update a component's measured impact after a new
  experiment refines it.

Do **not** use this skill when:

- The user only wants to capture the idea → `lab-propose-idea`.
- The user wants to *try* the idea → `lab-run-experiment`.
- There is no supporting experiment in `lab/experiments.md` — refuse
  and run an experiment first.

## Instructions

### 1. Locate the supporting experiment

Identify the most recent (or user-specified) entry in
`lab/experiments.md` that justifies the promotion. Verify it:

- has `**Status:** complete`,
- contains a results table with non-empty numbers,
- has a `### Decision` section that says `graduate` (or equivalent
  positive language).

If the entry is missing, in-progress, or its decision is `keep
iterating` / `reject`, **stop** and report what's missing. Tell the
user to run `lab-run-experiment` first or to update the decision
block.

### 2. Decide the target lifecycle

Two settings, by user instruction:

- **wired → validated** (default): the component is supported by
  one experiment with a positive signal but is not yet the default
  in baseline agents. Update `lab/components.md` only.
- **validated → adopted**: the component is now active by default
  in one or more baseline agents. Update `lab/components.md` *and*
  add the component id to the relevant agent YAMLs' `components:`
  lists if it isn't there already.

When ambiguous, ask the user explicitly.

### 3. Update lab/ideas.md

Find the idea entry under `## Trying`:

- Move it to `## Graduated`.
- Replace its body with one line:
  `- <kebab-id> → see `components.md`` (matching the existing
  graduated entries).
- Do not delete the entry from `## Graduated` ever — it's the
  audit trail.

If the idea is still under `## Proposed` (graduated without ever
sitting in `## Trying`, which is unusual), apply the same move from
`## Proposed`.

### 4. Add or update the entry in lab/components.md

If the component id is **not** already a section under
`## Active` in `lab/components.md`, append a new section using the
template at the top of that file:

```markdown
### <kebab-id>

**Status:** wired   _(or: validated, adopted — set per step 2)_
**Scope:** `<files where the component is implemented or wired>`
**Applies to:** `<agents that activate it>`
**Hypothesis:** <copy from the idea or the experiment hypothesis>
**Wired in:** [experiments.md#YYYY-MM-DD--<slug>](experiments.md#YYYY-MM-DD--<slug>)
**Impact:** <one or two sentences citing the headline numbers from the experiment>
```

If the component id **already** exists in `## Active`:

- Update its `**Status:**` line.
- Append a new `**Wired in:**` line (do not replace the previous
  link — multiple experiments may support a component).
- Update or append the `**Impact:**` line with the new numbers.

Section ordering: keep `## Active` sorted by graduation date
(oldest first). Insert at the bottom unless the user says otherwise.

### 5. (Adopted only) Wire the component into agent YAMLs

If the lifecycle is `adopted`, ensure the component id appears in
the `components:` list of every agent YAML where it is active:

```bash
rg -l "^components:" src/openharness/agents/configs/
```

For each relevant `*.yaml`, if the id isn't already listed under
`components:`, add it (preserve YAML formatting). Do not touch
agents the component does not apply to.

If wiring the component requires a runtime/config change (e.g.
defaulting `LoopGuardConfig.enabled=True`), make that change too,
keep the diff tight, and mention it in the report.

### 6. Sanity checks

Run the lint/format toolchain on touched Python files (if any) and
run the agent tests:

```bash
uv run ruff check --fix .
uv run ruff format .
uv run pytest tests/test_agents/ -q
```

If any test fails as a result of the wiring, fix it before
finishing — promotion must leave the suite green.

### 7. Report

Finish with:

- The component id and its new lifecycle status.
- The supporting experiment(s) by slug.
- Headline impact numbers (one line each).
- Files touched (`lab/ideas.md`, `lab/components.md`, agent YAMLs
  if adopted).
- The next experiment to run (if the user wants to push toward
  `adopted`).

Do **not**:

- Edit `lab/experiments.md` here — it's append-only and historical.
- Commit or push unless the user asks.
- Promote anything without a citing experiment.

## Examples

### Example: graduate a wired component to validated

Input: "Loop-guard ablation came back positive, graduate it."

Output:

1. Read the most recent `loop-guard-*` entry in
   `lab/experiments.md`; verify status complete and a positive
   decision.
2. Move `loop-guard` from `## Trying` to `## Graduated` in
   `lab/ideas.md`.
3. Update the existing `### loop-guard` section in
   `lab/components.md`: status `wired` → `validated`, append a new
   `**Wired in:**` line for the ablation entry, write `**Impact:**`
   citing the pass-rate delta.
4. Run `uv run pytest tests/test_agents/ -q`.
5. Report.

### Example: adopt a component into baselines

Input: "Adopt `loop-guard` — turn it on by default in all agents."

Output:

1. Verify `loop-guard` is `validated` in `lab/components.md`.
2. Update its status to `adopted`.
3. Confirm the id is in the `components:` list of `default.yaml`,
   `planner_executor.yaml`, `planner_executor_critic.yaml` (it
   already is in the current baseline; in this example it would be
   a no-op, but the skill must still verify).
4. If a runtime default needs flipping, make the minimal code
   change.
5. Run lint, format, and `tests/test_agents/`.
6. Report.

### Example: refuse without evidence

Input: "Promote `episodic-memory` to a component."

Output:

1. Search `lab/experiments.md` for `episodic-memory` — none found.
2. Refuse: "No supporting experiment for `episodic-memory` in
   `lab/experiments.md`. Run an experiment first via
   `lab-run-experiment`, then graduate."
